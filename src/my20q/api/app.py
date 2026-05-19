"""FastAPI backend for the caregiver cockpit.

Wraps the async Round/Session engine in a small REST surface. Session and
round state is in-memory — single caregiver, local-only deployment (see
docs/design/beta-retool.md §3). Every answer/undo returns the full round
state, so the cockpit renders from one undo-safe source of truth.

The reasoner returns whole queries, not token streams, so the API is
plain request/response. SSE/WebSocket is deferred until there is streamed
content to carry.

Run it: ``python -m my20q.api`` (or
``uvicorn my20q.api.app:create_app --factory --reload`` for dev reload).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from my20q.agent.dialogue import Answer, Round, RoundEvent, Session
from my20q.api import schemas
from my20q.config import Config
from my20q.llm import select_backend
from my20q.pictograms import Pictogram, load_catalog, retrieve
from my20q.profiles import is_real_patient, load_profile
from my20q.recording import Recorder
from my20q.topics import load_topics

log = logging.getLogger(__name__)

_UNSET: object = object()


@dataclass
class _RoundHandle:
    """In-memory record of one round, its latest event, and SSE subscribers."""

    round: Round
    last_event: RoundEvent
    session_id: str
    round_id: str
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    recorded: bool = False


def _event_out(ev: RoundEvent, catalog: list[Pictogram]) -> schemas.EventOut:
    match = retrieve(ev.text, catalog) if ev.text else None
    return schemas.EventOut(
        kind=ev.kind,
        text=ev.text,
        rationale=ev.rationale,
        query_index=ev.query_index,
        engine=ev.engine,
        emergency_screen=ev.emergency_screen,
        pictogram=match.id if match else None,
    )


def _publish(handle: _RoundHandle, payload: dict) -> None:
    """Push a payload to every SSE subscriber of this round."""
    for queue in list(handle.subscribers):
        queue.put_nowait(payload)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _maybe_record(state, handle: _RoundHandle) -> None:
    """Append a round to the dataset once it is terminal — if recording is on.

    `recorded` guards against double-writes; `undo` clears it so a
    re-finalized round is re-recorded (consumers take the last line per
    round_id).
    """
    recorder = state.recorder
    if recorder is None or state.recording_paused:
        return
    rnd = handle.round
    if rnd.is_terminal and not handle.recorded:
        recorder.record_round(
            session_id=handle.session_id,
            round_id=handle.round_id,
            topic_id=rnd.topic.id,
            engine=rnd.engine,
            history=rnd.history,
            outcome=rnd.outcome,
            final_utterance=rnd.final_utterance,
            model=state.model_label,
            emotional_state=rnd.emotional_state,
        )
        handle.recorded = True


def _recording_status(state) -> schemas.RecordingStatusOut:
    recorder = state.recorder
    threshold = state.config.recording_threshold_bytes
    if recorder is None:
        return schemas.RecordingStatusOut(
            enabled=False,
            paused=False,
            bytes=0,
            threshold_bytes=threshold,
            status="disabled",
            rounds=0,
        )
    nbytes = recorder.dataset_bytes()
    if nbytes >= threshold:
        status = "over"
    elif nbytes >= threshold * 0.8:
        status = "warning"
    else:
        status = "ok"
    return schemas.RecordingStatusOut(
        enabled=True,
        paused=state.recording_paused,
        bytes=nbytes,
        threshold_bytes=threshold,
        status=status,
        rounds=recorder.rounds_recorded(),
    )


def create_app(config: Config | None = None, *, backend: object = _UNSET) -> FastAPI:
    """Build the cockpit API.

    `backend` is normally selected via `select_backend` (which enforces
    the privacy invariant); tests may inject one explicitly.
    """
    config = config or Config.from_env()
    topics = load_topics(config.topics_path)
    profile = load_profile(config.profile_path)
    llm = select_backend(config, profile) if backend is _UNSET else backend

    app = FastAPI(title="my20Q Caregiver Cockpit API", version="0.1.0-beta")

    origins = [
        o.strip()
        for o in os.environ.get(
            "MY20Q_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = config
    app.state.topics = topics
    app.state.pictograms = load_catalog()
    app.state.profile = profile
    app.state.llm = llm
    app.state.sessions = {}
    app.state.rounds = {}
    # Recording is enabled only for a real patient (the privacy invariant).
    app.state.recorder = (
        Recorder(config.recording_dir, profile.id) if is_real_patient(profile) else None
    )
    app.state.recording_paused = False
    app.state.model_label = getattr(llm, "model", None) or "fallback"

    _register_routes(app)
    _mount_assets(app)
    _mount_frontend(app)
    log.info(
        "cockpit API ready — mode=%s engine=%s real_patient=%s",
        config.mode,
        "reasoning" if llm is not None else "fallback",
        is_real_patient(profile),
    )
    return app


def _register_routes(app: FastAPI) -> None:
    state = app.state

    def _session(sid: str) -> Session:
        session = state.sessions.get(sid)
        if session is None:
            raise HTTPException(404, f"unknown session: {sid}")
        return session

    def _handle(sid: str, rid: str) -> _RoundHandle:
        handle = state.rounds.get(rid)
        if handle is None or handle.session_id != sid:
            raise HTTPException(404, f"unknown round: {rid}")
        return handle

    def _round_state(h: _RoundHandle) -> schemas.RoundStateOut:
        r = h.round
        return schemas.RoundStateOut(
            session_id=h.session_id,
            round_id=h.round_id,
            topic_id=r.topic.id,
            event=_event_out(h.last_event, state.pictograms),
            history=[
                schemas.HistoryEntryOut(
                    kind=e["kind"], text=e["text"], answer=e.get("answer")
                )
                for e in r.history
            ],
            outcome=r.outcome,
            final_utterance=r.final_utterance,
            query_count=r.query_count,
            engine=r.engine,
        )

    @app.get("/api/health", response_model=schemas.HealthOut)
    def health() -> schemas.HealthOut:
        return schemas.HealthOut(
            status="ok",
            mode=state.config.mode,
            engine="reasoning" if state.llm is not None else "fallback",
            real_patient=is_real_patient(state.profile),
        )

    @app.get("/api/topics", response_model=list[schemas.TopicOut])
    def topics() -> list[schemas.TopicOut]:
        return [
            schemas.TopicOut(id=t.id, label=t.label, emergency=t.emergency)
            for t in state.topics
        ]

    @app.post("/api/sessions", response_model=schemas.CreateSessionOut)
    def create_session() -> schemas.CreateSessionOut:
        sid = uuid.uuid4().hex
        state.sessions[sid] = Session(
            state.topics, llm=state.llm, config=state.config, profile=state.profile
        )
        return schemas.CreateSessionOut(session_id=sid)

    @app.post("/api/sessions/{sid}/rounds", response_model=schemas.RoundStateOut)
    async def start_round(sid: str, body: schemas.StartRoundIn) -> schemas.RoundStateOut:
        session = _session(sid)
        # A new round abandons any still-live round in this session
        # (a topic switch ends the current round — recorded as abandoned).
        for prior in state.rounds.values():
            if prior.session_id == sid and not prior.round.is_terminal:
                prior.round.abandon()
                _maybe_record(state, prior)
        try:
            rnd = session.start_round(body.topic_id, seed_context=body.seed_context)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        event = await rnd.open()
        rid = uuid.uuid4().hex
        handle = _RoundHandle(round=rnd, last_event=event, session_id=sid, round_id=rid)
        rnd.on_phase = lambda phase: _publish(handle, {"phase": phase})
        state.rounds[rid] = handle
        # An emergency topic short-circuits to a terminal round on open(),
        # so record it here. Non-emergency rounds aren't terminal yet — no-op.
        _maybe_record(state, handle)
        return _round_state(handle)

    @app.get("/api/sessions/{sid}/rounds/{rid}", response_model=schemas.RoundStateOut)
    def get_round(sid: str, rid: str) -> schemas.RoundStateOut:
        return _round_state(_handle(sid, rid))

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/answer", response_model=schemas.RoundStateOut
    )
    async def answer(
        sid: str, rid: str, body: schemas.AnswerIn
    ) -> schemas.RoundStateOut:
        handle = _handle(sid, rid)
        try:
            handle.last_event = await handle.round.answer(Answer(body.answer))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        _maybe_record(state, handle)
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/context", response_model=schemas.RoundStateOut
    )
    def add_context(
        sid: str, rid: str, body: schemas.ContextIn
    ) -> schemas.RoundStateOut:
        handle = _handle(sid, rid)
        try:
            handle.round.add_context(body.text)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/undo", response_model=schemas.RoundStateOut
    )
    async def undo(sid: str, rid: str) -> schemas.RoundStateOut:
        handle = _handle(sid, rid)
        try:
            handle.last_event = await handle.round.undo()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        handle.recorded = False  # undo un-finalizes — allow a re-record
        _maybe_record(state, handle)
        return _round_state(handle)

    @app.get("/api/sessions/{sid}/rounds/{rid}/events")
    async def round_events(sid: str, rid: str) -> StreamingResponse:
        """SSE progress channel for one round.

        Streams short reasoner phase events ("thinking", "re-asking") so
        the cockpit can show live progress while a request is in flight.
        The authoritative round state still comes from the REST responses;
        this channel is purely progress. A 15s keep-alive holds the
        connection open through idle gaps.
        """
        handle = _handle(sid, rid)
        queue: asyncio.Queue = asyncio.Queue()
        handle.subscribers.append(queue)

        async def stream():
            try:
                yield _sse({"phase": "connected"})
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield _sse(item)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                if queue in handle.subscribers:
                    handle.subscribers.remove(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/recording", response_model=schemas.RecordingStatusOut)
    def recording_status() -> schemas.RecordingStatusOut:
        return _recording_status(state)

    @app.post("/api/recording/pause", response_model=schemas.RecordingStatusOut)
    def set_recording_paused(body: schemas.PauseIn) -> schemas.RecordingStatusOut:
        state.recording_paused = body.paused
        return _recording_status(state)

    @app.post("/api/sessions/{sid}/emotion")
    def set_emotion(sid: str, body: schemas.EmotionIn) -> dict:
        """Set the caregiver's emotional-slider reading for the session.

        Applied to the session and to any live round, so mid-round slider
        moves reach the reasoner's next query.
        """
        session = _session(sid)
        values = dict(body.values)
        session.emotional_state = values
        for handle in state.rounds.values():
            if handle.session_id == sid and not handle.round.is_terminal:
                handle.round.emotional_state = dict(values)
        return {"ok": True}


_LANDING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>my20Q Caregiver Cockpit API</title>
<style>
 body{font-family:system-ui,"Segoe UI",sans-serif;background:#1a1a1a;color:#bdb191;
   margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
 main{max-width:32rem;padding:2rem;line-height:1.6}
 h1{color:#d0c2a5} a{color:#6ea262}
 code{background:#2a2a2a;padding:.1em .4em;border-radius:4px}
</style></head><body><main>
<h1>my20Q Caregiver Cockpit API</h1>
<p>The server is running, but the cockpit UI has not been built yet.</p>
<p>For development, run the cockpit dev server:
   <code>npm run dev --prefix web</code> &rarr;
   <a href="http://localhost:5173">localhost:5173</a></p>
<p>Or build it once — <code>npm run build --prefix web</code> — and restart
   this server; it will then host the cockpit here at <code>/</code>.</p>
<p>API reference: <a href="/docs">/docs</a></p>
</main></body></html>
"""


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built cockpit at / when present; else a landing page.

    The dist path can be overridden with MY20Q_WEB_DIST; by default it is
    `web/dist` relative to the repo (present after `npm run build`).
    """
    override = os.environ.get("MY20Q_WEB_DIST")
    web_dist = (
        Path(override)
        if override
        else Path(__file__).resolve().parents[3] / "web" / "dist"
    )
    if (web_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="cockpit")
        log.info("serving the built cockpit from %s", web_dist)
        return

    @app.get("/", include_in_schema=False)
    def root() -> HTMLResponse:
        return HTMLResponse(_LANDING_HTML)


def _mount_assets(app: FastAPI) -> None:
    """Serve fetched pictograms at /assets when present (see fetch_icons.py)."""
    assets = Path(__file__).resolve().parents[3] / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
        log.info("serving pictogram assets from %s", assets)
