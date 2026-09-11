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
import contextlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from my20q.agent.dialogue import Answer, Round, RoundEvent, Session
from my20q.agent.safety import for_speech
from my20q.api import schemas
from my20q.config import Config
from my20q.llm import select_backend
from my20q.llm.ollama_client import OllamaBackend
from my20q.pictograms import Pictogram, load_catalog, retrieve
from my20q.profiles import is_real_patient, load_profile
from my20q.recording import Recorder, build_round_record, transcript
from my20q.topics import load_topics
from my20q.tts import TTSUnavailable, select_tts

log = logging.getLogger(__name__)

_UNSET: object = object()

# Sentinel pushed into each subscriber's queue at server shutdown so any
# generator blocked in `queue.get()` returns immediately and exits the
# loop. Without this, uvicorn's graceful shutdown waits forever for the
# SSE generator to finish on its own (it never does).
_SHUTDOWN_SENTINEL: object = object()


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
    match = retrieve(ev.text, catalog) if ev.text and ev.kind != "diagnostic" else None
    return schemas.EventOut(
        kind=ev.kind,
        text=ev.text,
        rationale=ev.rationale,
        preface=ev.preface,
        query_index=ev.query_index,
        engine=ev.engine,
        emergency_screen=ev.emergency_screen,
        pictogram=match.id if match else None,
        facets=[schemas.FacetOut(**f) for f in ev.facets],
        diagnostic=ev.diagnostic,
        flipped_from=ev.flipped_from,
        clarifying=ev.clarifying,
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
            board=rnd.board_record(),
            seed_context=rnd.seed_context,
            seed_ms=rnd.seed_ms,
            pending_question=rnd.pending_question,
            clarifications=rnd.clarifications,
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
        dev=getattr(state, "dev_capture", False),
    )


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan hook that gracefully unblocks SSE generators on shutdown.

    Uvicorn waits for in-flight requests to drain before exiting. The SSE
    progress channel is an infinite generator — it will never drain on its
    own. On shutdown we set `shutdown_event` and push a sentinel onto every
    active subscriber queue so each generator wakes from `queue.get()`,
    breaks out of its loop, and the response closes cleanly.

    Combined with `timeout_graceful_shutdown` in __main__.py, this keeps
    Ctrl+C responsive even with open EventSource connections.
    """
    app.state.shutdown_event = asyncio.Event()
    try:
        yield
    finally:
        app.state.shutdown_event.set()
        rounds = getattr(app.state, "rounds", {})
        for handle in rounds.values():
            for queue in list(handle.subscribers):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(_SHUTDOWN_SENTINEL)


def create_app(config: Config | None = None, *, backend: object = _UNSET) -> FastAPI:
    """Build the cockpit API.

    `backend` is normally selected via `select_backend` (which enforces
    the privacy invariant); tests may inject one explicitly.
    """
    config = config or Config.from_env()
    topics = load_topics(config.topics_path)
    profile = load_profile(config.profile_path)
    llm = select_backend(config, profile) if backend is _UNSET else backend

    app = FastAPI(
        title="my20Q Caregiver Cockpit API",
        version="0.1.0-beta",
        lifespan=_lifespan,
    )

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
    # The PATIENT DATASET is enabled only for a real patient — the privacy
    # invariant, unchanged: recording => real patient => local LLM.
    #
    # DEV CAPTURE is a separate artifact with the mirror-image guard: it writes
    # only for a SYNTHETIC persona, into its own directory, and only when asked
    # for explicitly. The two are mutually exclusive by construction, so there
    # is no path by which real patient content reaches the dev directory — and
    # a synthetic development trial can still leave a round record to audit,
    # which is otherwise impossible because the patient dataset is (correctly)
    # off for synthetic runs.
    real = is_real_patient(profile)
    dev_capture = config.dev_capture and not real
    if config.dev_capture and real:
        log.warning(
            "MY20Q_DEV_CAPTURE ignored — a REAL patient profile is loaded, so the "
            "patient dataset is the only recording channel (privacy invariant)"
        )
    app.state.recorder = (
        Recorder(config.recording_dir, profile.id)
        if real
        else Recorder(config.dev_capture_dir, profile.id if profile else "synthetic")
        if dev_capture
        else None
    )
    app.state.dev_capture = dev_capture
    app.state.recording_paused = False
    app.state.model_label = getattr(llm, "model", None) or "fallback"
    # Local-only TTS (piper). May be unavailable until installed — the
    # status endpoint reports why, and the cockpit stays silent.
    app.state.tts = select_tts(config)

    _register_routes(app)
    _mount_assets(app)
    _mount_frontend(app)
    log.info(
        "cockpit API ready — mode=%s engine=%s real_patient=%s capture=%s",
        config.mode,
        "reasoning" if llm is not None else "fallback",
        real,
        "patient-dataset" if real else ("dev" if dev_capture else "off"),
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
            banner=schemas.BannerOut(**r.banner()),
            history=[
                schemas.HistoryEntryOut(
                    kind=e["kind"],
                    text=e.get("text", ""),
                    answer=e.get("answer"),
                    rationale=e.get("rationale", ""),
                )
                for e in r.history
                # Internal board markers are not conversation; diagnostics ARE
                # shown (an honest trace of what the caregiver saw fail).
                if e["kind"] not in ("reseed", "restart")
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

    @app.get("/api/models", response_model=schemas.ModelsOut)
    async def list_models() -> schemas.ModelsOut:
        """Pulled Ollama models available for human-trial model selection.

        Meaningful only when the active backend is a local Ollama backend; the
        cockpit hides the selector otherwise (can_select=false).
        """
        backend = state.llm
        current = getattr(backend, "model", None)
        if not isinstance(backend, OllamaBackend):
            return schemas.ModelsOut(models=[], current=current, can_select=False)
        models: list[str] = []
        try:
            async with httpx2.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{backend.base_url}/api/tags")
                resp.raise_for_status()
                tags = resp.json().get("models", [])
            models = sorted(
                m["name"]
                for m in tags
                if isinstance(m.get("name"), str) and "embed" not in m["name"]
            )
        except httpx2.HTTPError:
            models = [current] if current else []
        return schemas.ModelsOut(models=models, current=current, can_select=True)

    @app.post("/api/model", response_model=schemas.ModelsOut)
    async def select_model(body: schemas.ModelSelectIn) -> schemas.ModelsOut:
        """Switch the active model at runtime (for human-interaction trials).

        Mutating the shared backend's model swaps it for every session's next
        call — the Reasoner reads ``backend.model`` per call — so it takes effect
        on the next question without a restart.
        """
        backend = state.llm
        if not isinstance(backend, OllamaBackend):
            raise HTTPException(409, "model selection requires a local Ollama backend")
        backend.model = body.model
        state.model_label = body.model
        log.info("active model switched to %s (human-trial selection)", body.model)
        # Warm the new model before returning so the next reasoning turn isn't a
        # cold load that overruns the per-call timeout and drops the round to
        # fallback. Best-effort — a failed preload just means the first turn pays
        # the load cost as before.
        loaded = await backend.preload()
        log.info("preload of %s: %s", body.model, "ok" if loaded else "failed")
        return await list_models()

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
            state.topics,
            llm=state.llm,
            config=state.config,
            profile=state.profile,
            # So the confirmed-yes log carries the id its recording is filed
            # under, and the two artifacts can be joined (W2-Q).
            session_id=sid,
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
        # Minted BEFORE the round so the round, its recording and its
        # confirmed-yes entries all carry the same id (W2-Q). They used to be
        # generated separately, leaving the yes-log's "r3" unjoinable.
        rid = uuid.uuid4().hex
        try:
            rnd = session.start_round(
                body.topic_id, seed_context=body.seed_context, round_id=rid
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        event = await rnd.open()
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
    async def add_context(
        sid: str, rid: str, body: schemas.ContextIn
    ) -> schemas.RoundStateOut:
        handle = _handle(sid, rid)
        try:
            # Re-proposes the pending query against the new context, so the
            # on-screen question refreshes to account for it.
            handle.last_event = await handle.round.add_context(body.text)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/retry", response_model=schemas.RoundStateOut
    )
    async def retry(sid: str, rid: str) -> schemas.RoundStateOut:
        """Re-run the reasoner after a diagnostic (the failure card's Retry)."""
        handle = _handle(sid, rid)
        try:
            handle.last_event = await handle.round.retry()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/accept", response_model=schemas.RoundStateOut
    )
    async def accept(sid: str, rid: str) -> schemas.RoundStateOut:
        """✓ on the banner: conclude the round with the current draft.

        The caregiver is the stopping policy — the cockpit then shows the
        explicit confirmation modal (final utterance, spoken, one "New
        round" action). 409 when there is nothing to accept yet.
        """
        handle = _handle(sid, rid)
        try:
            handle.last_event = handle.round.accept()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        _maybe_record(state, handle)
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/edit", response_model=schemas.RoundStateOut
    )
    async def edit(sid: str, rid: str, body: schemas.EditIn) -> schemas.RoundStateOut:
        """✗ on the banner: a real-time edit against the live draft.

        The note is interpreted deterministically: slot dismissals mute the
        slot, notes matching a woven value ban it (strike-through), anything
        else becomes ordinary guiding context. The pending question is
        re-proposed against the edited board.
        """
        handle = _handle(sid, rid)
        try:
            handle.last_event = await handle.round.edit(body.text)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/replace", response_model=schemas.RoundStateOut
    )
    async def replace_segment(
        sid: str, rid: str, body: schemas.ReplaceIn
    ) -> schemas.RoundStateOut:
        """The synthesis editor: a clicked segment's precise edit.

        No note parsing — the segment identifies the (category, value)
        exactly. Refine-or-replace semantics; new == "" mutes the category
        ("remove this detail").
        """
        handle = _handle(sid, rid)
        try:
            handle.last_event = await handle.round.replace(
                body.category, body.old, body.new
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/restate", response_model=schemas.RoundStateOut
    )
    async def restate(sid: str, rid: str) -> schemas.RoundStateOut:
        """⟳ — re-say the draft slightly differently (same content).

        Draft-cache only: the board, history, and pending question are
        untouched; failure is soft (409) and leaves the draft as it was.
        """
        handle = _handle(sid, rid)
        try:
            await handle.round.restate()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _round_state(handle)

    @app.post(
        "/api/sessions/{sid}/rounds/{rid}/flip", response_model=schemas.RoundStateOut
    )
    async def flip(sid: str, rid: str) -> schemas.RoundStateOut:
        """Re-render the pending question in its opposite connotation.

        The opposition button — an action, not an answer: the same question
        comes back mirrored (who-does-for-whom swapped, or the key detail
        reversed) and the round keeps waiting for an answer. Failure is soft
        (409): the pending question stays as it was.
        """
        handle = _handle(sid, rid)
        try:
            handle.last_event = await handle.round.flip()
        except RuntimeError as exc:  # includes ReasonerError — pending unchanged
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
    async def round_events(
        sid: str, rid: str, request: Request
    ) -> StreamingResponse:
        """SSE progress channel for one round.

        Streams short reasoner phase events ("thinking", "re-asking") so
        the cockpit can show live progress while a request is in flight.
        The authoritative round state still comes from the REST responses;
        this channel is purely progress.

        Shutdown-safe: each iteration polls a short timeout. A keep-alive
        comment fires every 5s of idle so intermediaries don't close the
        connection. At server shutdown the lifespan handler pushes a
        sentinel into the queue, which breaks the loop and lets uvicorn
        finish draining within `timeout_graceful_shutdown` seconds. The
        same loop also exits promptly if the client disconnects.

        Headers disable caching and proxy buffering — without these, the
        Vite dev proxy and nginx-style intermediaries hold messages until
        the connection closes, defeating the live channel.
        """
        handle = _handle(sid, rid)
        queue: asyncio.Queue = asyncio.Queue()
        handle.subscribers.append(queue)
        shutdown_event: asyncio.Event = getattr(
            app.state, "shutdown_event", asyncio.Event()
        )

        async def stream():
            try:
                yield _sse({"phase": "connected"})
                while not shutdown_event.is_set():
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=5.0)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if item is _SHUTDOWN_SENTINEL:
                        break
                    yield _sse(item)
            except asyncio.CancelledError:
                # uvicorn cancels in-flight requests during forced shutdown;
                # let the cancellation propagate after cleanup runs.
                raise
            finally:
                if queue in handle.subscribers:
                    handle.subscribers.remove(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/recording", response_model=schemas.RecordingStatusOut)
    def recording_status() -> schemas.RecordingStatusOut:
        return _recording_status(state)

    @app.post("/api/recording/pause", response_model=schemas.RecordingStatusOut)
    def set_recording_paused(body: schemas.PauseIn) -> schemas.RecordingStatusOut:
        state.recording_paused = body.paused
        return _recording_status(state)

    @app.get("/api/recordings", response_model=list[schemas.RecordingFileOut])
    def list_recordings() -> list[schemas.RecordingFileOut]:
        """List recorded sessions for the review dashboard's server picker.

        Empty unless a real patient profile is loaded (recordings only
        exist for a real patient). Saved exports load via upload instead.
        """
        recorder = state.recorder
        if recorder is None:
            return []
        return [schemas.RecordingFileOut(**e) for e in recorder.list_sessions()]

    @app.get("/api/recordings/{session_id}")
    def read_recording(session_id: str) -> list[dict]:
        """Parsed round records for one recorded session (review playback)."""
        recorder = state.recorder
        if recorder is None:
            raise HTTPException(404, "no recordings (no real patient profile)")
        try:
            return recorder.read_session(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"unknown recording: {session_id}") from exc

    @app.get("/api/sessions/{sid}/export")
    def export_session(sid: str, format: str = "jsonl") -> Response:
        """Download the session's conversation.

        Mirrors the recording dataset: the default JSONL format is the
        same per-round record schema the recorder writes
        (`build_round_record`), so exported and recorded data are one
        uniform training corpus. `format=md` gives a human-readable view
        rendered from the same records.

        Works regardless of the recording gate — and carries only the
        conversation, never the profile or knowledge graph. See
        recording/transcript.py.
        """
        _session(sid)  # 404 if unknown
        records: list[dict] = []
        for handle in state.rounds.values():
            if handle.session_id != sid:
                continue
            r = handle.round
            records.append(
                build_round_record(
                    session_id=handle.session_id,
                    round_id=handle.round_id,
                    topic_id=r.topic.id,
                    engine=r.engine,
                    history=r.history,
                    outcome=r.outcome,
                    final_utterance=r.final_utterance,
                    model=state.model_label,
                    emotional_state=r.emotional_state,
                    board=r.board_record(),
                    seed_context=r.seed_context,
                    seed_ms=r.seed_ms,
                    pending_question=r.pending_question,
                    clarifications=r.clarifications,
                )
            )
        if format == "md":
            body = transcript.to_markdown(sid, records)
            media, ext = "text/markdown", "md"
        else:  # jsonl — mirrors the recording file format
            body = transcript.to_jsonl(records)
            media, ext = "application/x-ndjson", "jsonl"
        filename = f"my20q-conversation-{sid[:8]}.{ext}"
        return Response(
            content=body,
            media_type=f"{media}; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/tts/status", response_model=schemas.TTSStatusOut)
    def tts_status() -> schemas.TTSStatusOut:
        """Report whether local (piper) speech is ready, and why/why not."""
        engine = state.tts
        if engine is None:
            return schemas.TTSStatusOut(available=False, reason="TTS disabled")
        return schemas.TTSStatusOut(
            available=engine.available, voice=engine.voice, reason=engine.reason
        )

    @app.post("/api/tts")
    async def synthesize_tts(body: schemas.TTSIn) -> Response:
        """Render text to WAV via local piper.

        503 when piper is unavailable (not installed / no model) — the
        cockpit treats that as 'audio off' and stays silent. There is no
        cloud fallback by design. Patient-facing strings are already
        sanitized upstream; this only voices what the cockpit shows.
        """
        engine = state.tts
        if engine is None or not engine.available:
            reason = "TTS disabled" if engine is None else engine.reason
            raise HTTPException(503, f"TTS unavailable: {reason}")
        try:
            audio = await run_in_threadpool(engine.synthesize, for_speech(body.text))
        except TTSUnavailable as exc:
            raise HTTPException(503, f"TTS failed: {exc}") from exc
        return Response(content=audio, media_type="audio/wav")

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
