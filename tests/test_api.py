"""Tests for the cockpit API. Skipped entirely if fastapi is not installed."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from my20q.config import Config
from my20q.llm import MockBackend

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from my20q.api.app import _SHUTDOWN_SENTINEL, _lifespan, _sse, create_app  # noqa: E402


def _fallback_client() -> TestClient:
    """A client with no LLM — deterministic fallback engine."""
    return TestClient(create_app(replace(Config.from_env(), llm_enabled=False), backend=None))


def _scripted(*responses: str) -> MockBackend:
    state = {"i": 0}

    def responder(_msgs: list) -> str:
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return responses[i]

    return MockBackend(responder=responder)


def _action(kind: str, content: str) -> str:
    return json.dumps({"action": kind, "content": content, "rationale": "r"})


def test_health_and_topics() -> None:
    client = _fallback_client()
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["engine"] == "fallback"

    topics = client.get("/api/topics").json()
    assert [t["id"] for t in topics][0] == "emergency"
    assert len(topics) == 5


def test_fallback_round_to_synthesis() -> None:
    client = _fallback_client()
    sid = client.post("/api/sessions").json()["session_id"]
    state = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()
    rid = state["round_id"]
    assert state["event"]["kind"] == "query"
    assert state["engine"] == "fallback"

    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"}
    ).json()
    assert state["event"]["kind"] == "synthesis"
    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"}
    ).json()
    assert state["event"]["kind"] == "synthesized"
    assert state["outcome"] == "synthesized"
    assert state["final_utterance"]


def test_emergency_topic_short_circuits() -> None:
    client = _fallback_client()
    sid = client.post("/api/sessions").json()["session_id"]
    state = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "emergency"}
    ).json()
    assert state["event"]["kind"] == "emergency"
    assert state["event"]["emergency_screen"] is not None


def test_context_and_undo() -> None:
    client = _fallback_client()
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]

    client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "no"})
    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/context",
        json={"text": "He pointed at the kitchen."},
    ).json()
    assert "context" in [h["kind"] for h in state["history"]]

    state = client.post(f"/api/sessions/{sid}/rounds/{rid}/undo").json()
    assert "context" not in [h["kind"] for h in state["history"]]


def test_unknown_ids_return_404() -> None:
    client = _fallback_client()
    assert (
        client.post("/api/sessions/nope/rounds", json={"topic_id": "general"}).status_code
        == 404
    )
    assert client.get("/api/sessions/nope/rounds/nope").status_code == 404


def test_unknown_topic_returns_400() -> None:
    client = _fallback_client()
    sid = client.post("/api/sessions").json()["session_id"]
    resp = client.post(f"/api/sessions/{sid}/rounds", json={"topic_id": "ghost"})
    assert resp.status_code == 400


def test_reasoning_round_via_injected_backend() -> None:
    backend = _scripted(
        _action("query", "Is this about someone you would like to contact today?"),
        _action("synthesis", "I would like to call my son this afternoon."),
    )
    client = TestClient(create_app(Config.from_env(), backend=backend))
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "my_people"}
    ).json()["round_id"]

    state = client.get(f"/api/sessions/{sid}/rounds/{rid}").json()
    assert state["engine"] == "reasoning"
    assert state["event"]["kind"] == "query"

    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"}
    ).json()
    assert state["event"]["kind"] == "synthesis"
    assert "son" in state["event"]["text"]


# The live SSE stream is exercised by the cockpit; a TestClient cannot tear
# down an infinite streaming response cleanly, so the unit suite checks the
# route registration and the payload format instead.
def test_sse_event_route_is_registered() -> None:
    app = create_app(replace(Config.from_env(), llm_enabled=False), backend=None)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/sessions/{sid}/rounds/{rid}/events" in paths


def test_sse_payload_formatting() -> None:
    assert _sse({"phase": "thinking"}) == 'data: {"phase": "thinking"}\n\n'


def test_recording_disabled_without_real_profile() -> None:
    client = _fallback_client()
    rec = client.get("/api/recording").json()
    assert rec["enabled"] is False
    assert rec["status"] == "disabled"


def test_real_profile_records_an_emergency_round(tmp_path) -> None:
    profile = tmp_path / "real.yaml"
    profile.write_text("id: test_patient\ndisplay_name: Test Patient\n", encoding="utf-8")
    cfg = replace(
        Config.from_env(),
        llm_enabled=False,
        profile_path=profile,
        recording_dir=tmp_path / "data",
    )
    client = TestClient(create_app(cfg, backend=None))

    assert client.get("/api/recording").json()["enabled"] is True

    sid = client.post("/api/sessions").json()["session_id"]
    state = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "emergency"}
    ).json()
    assert state["event"]["kind"] == "emergency"

    # the emergency round was recorded, flagged by its outcome
    assert client.get("/api/recording").json()["rounds"] == 1
    files = list((tmp_path / "data" / "test_patient").glob("*.jsonl"))
    assert files
    record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert record["outcome"] == "emergency"


def test_emotion_endpoint_accepts_a_reading() -> None:
    client = _fallback_client()
    sid = client.post("/api/sessions").json()["session_id"]
    resp = client.post(
        f"/api/sessions/{sid}/emotion",
        json={"values": {"sad_happy": 0.3, "anxious_calm": -0.5}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_export_session_jsonl_mirrors_recording_format() -> None:
    client = _fallback_client()
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]
    client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"})
    client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"})

    # default format is JSONL — one round per line, recording record schema
    resp = client.get(f"/api/sessions/{sid}/export")
    assert resp.status_code == 200
    assert "application/x-ndjson" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    # same keys the recorder writes (see test_real_profile_records_*)
    assert record["outcome"] == "synthesized"
    assert record["topic_id"] == "physical_health"
    assert record["session_id"] == sid
    assert "queries" in record and "job_b" in record and "recorded_at" in record

    md = client.get(f"/api/sessions/{sid}/export", params={"format": "md"})
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert "my20Q conversation" in md.text
    assert "Round 1" in md.text


def test_export_unknown_session_404() -> None:
    client = _fallback_client()
    assert client.get("/api/sessions/nope/export").status_code == 404


def test_lifespan_unblocks_sse_subscribers_on_shutdown() -> None:
    """The lifespan hook injects a shutdown sentinel into every active SSE
    subscriber queue so blocked `queue.get()` calls wake up — preventing
    the Ctrl+C hang seen with long-lived EventSource connections.
    """
    import asyncio
    import types

    from fastapi import FastAPI

    app = FastAPI()
    queue: asyncio.Queue = asyncio.Queue()
    handle = types.SimpleNamespace(subscribers=[queue])
    app.state.rounds = {"r1": handle}

    async def run() -> None:
        async with _lifespan(app):
            assert not app.state.shutdown_event.is_set()
            assert queue.empty()
        assert app.state.shutdown_event.is_set()
        assert queue.get_nowait() is _SHUTDOWN_SENTINEL

    asyncio.run(run())
