"""Tests for the cockpit API. Skipped entirely if fastapi is not installed."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from my20q.config import Config, ReasoningTuning
from my20q.llm import MockBackend

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from my20q.api.app import _SHUTDOWN_SENTINEL, _lifespan, _sse, create_app  # noqa: E402


def _no_llm_client() -> TestClient:
    """A client with no LLM — rounds surface diagnostics, never questions."""
    return TestClient(create_app(replace(Config.from_env(), llm_enabled=False), backend=None))


def _controller_backend(*, utterance: str) -> MockBackend:
    """A MockBackend that plays the seed/deliberate/format/synthesize protocol.

    Distinct, person-anchored questions whose slots are words the question
    says, so every code gate (audit, repeat, anchoring) passes.
    """
    people_qs = [
        ("Do you want to call your son?", {"who": "your son", "how": "call"}),
        ("Do you want your son to visit you?", {"who": "your son", "how": "visit"}),
        ("Do you need your son to bring something?", {"who": "your son", "how": "bring something"}),
        ("Is it about thanking your son?", {"who": "your son", "how": "thanking"}),
        ("Do you want to tell your son some news?", {"who": "your son", "how": "tell news"}),
        ("Is it about a photo of your son?", {"what": "a photo", "who": "your son"}),
        ("Do you miss your son today?", {"who": "your son", "why": "miss him"}),
        ("Should your son fix something for you?", {"who": "your son", "how": "fix something"}),
    ]
    state = {"n": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:  # seed
            return json.dumps(
                {"who": ["your son", "a friend"], "what": ["a phone call", "a visit"],
                 "how": ["call", "visit"], "why": ["missing them"]}
            )
        if "OPPOSITE button" in system:  # the opposition button's one-shot
            return json.dumps(
                {"question": "Do you want your son to call you?",
                 "slots": {"who": "your son"}}
            )
        if "DOUBLE-CHECK" in system:  # verify-on-lock one-shot
            return json.dumps({"question": "Is it your son you mean?"})
        if "pin down the ONE specific" in system:  # deliberate
            return "thinking about who and what..."
        if "Convert a drafted question" in system:  # format
            q, slots = people_qs[state["n"] % len(people_qs)]
            state["n"] += 1
            return json.dumps(
                {"question": q, "slots": slots, "preface": "", "rationale": "r"}
            )
        return json.dumps({"utterance": utterance})  # synthesize

    return MockBackend(responder=responder)


def test_health_and_topics() -> None:
    client = _no_llm_client()
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["engine"] == "fallback"

    topics = client.get("/api/topics").json()
    assert [t["id"] for t in topics][0] == "emergency"
    assert len(topics) == 5


def test_no_llm_round_serves_a_diagnostic_not_canned_questions() -> None:
    # There is no canned question bank anymore: without an LLM the round
    # surfaces a diagnostic card, answering 409s (nothing to answer), and
    # retry re-checks rather than inventing a question.
    client = _no_llm_client()
    sid = client.post("/api/sessions").json()["session_id"]
    state = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()
    rid = state["round_id"]
    assert state["event"]["kind"] == "diagnostic"
    assert state["event"]["diagnostic"]["llm_unreachable"] is True
    assert state["outcome"] is None  # alive — not abandoned by the failure

    resp = client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"})
    assert resp.status_code == 409  # no pending question to answer

    state = client.post(f"/api/sessions/{sid}/rounds/{rid}/retry").json()
    assert state["event"]["kind"] == "diagnostic"  # still no LLM — still honest


def test_emergency_topic_short_circuits() -> None:
    client = _no_llm_client()
    sid = client.post("/api/sessions").json()["session_id"]
    state = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "emergency"}
    ).json()
    assert state["event"]["kind"] == "emergency"
    assert state["event"]["emergency_screen"] is not None


def test_context_and_undo() -> None:
    client = _reasoning_client()
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]

    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/context",
        json={"text": "He pointed at the kitchen."},
    ).json()
    assert "context" in [h["kind"] for h in state["history"]]

    state = client.post(f"/api/sessions/{sid}/rounds/{rid}/undo").json()
    assert "context" not in [h["kind"] for h in state["history"]]


def test_unknown_ids_return_404() -> None:
    client = _no_llm_client()
    assert (
        client.post("/api/sessions/nope/rounds", json={"topic_id": "general"}).status_code
        == 404
    )
    assert client.get("/api/sessions/nope/rounds/nope").status_code == 404


def test_unknown_topic_returns_400() -> None:
    client = _no_llm_client()
    sid = client.post("/api/sessions").json()["session_id"]
    resp = client.post(f"/api/sessions/{sid}/rounds", json={"topic_id": "ghost"})
    assert resp.status_code == 400


def test_reasoning_round_via_injected_backend() -> None:
    # Yes answers build per-slot consensus; the BANNER carries the evolving
    # draft (never an engine-initiated proposal) and accept concludes.
    backend = _controller_backend(
        utterance="I would like to call my son this afternoon.",
    )
    client = TestClient(create_app(Config.from_env(), backend=backend))
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "my_people"}
    ).json()["round_id"]

    state = client.get(f"/api/sessions/{sid}/rounds/{rid}").json()
    assert state["engine"] == "reasoning"
    assert state["event"]["kind"] == "query"
    facets = state["event"]["facets"]
    assert [f["category"] for f in facets] == ["who", "what", "when", "where", "why", "how"]
    assert any(f["focus"] for f in facets)  # the targeted slot is marked
    who = facets[0]
    assert {"value": "your son", "score": 0.0, "parent": None} in who["contenders"]
    assert state["banner"]["state"] == "pending"  # no signal yet

    for _ in range(3):
        state = client.post(
            f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"}
        ).json()
        assert state["event"]["kind"] == "query"  # never proposes on its own

    banner = state["banner"]
    assert banner["state"] == "draft" and banner["text"]
    assert any(p["category"] == "who" for p in banner["parts"])

    state = client.post(f"/api/sessions/{sid}/rounds/{rid}/accept").json()
    assert state["outcome"] == "synthesized"
    assert state["event"]["kind"] == "synthesized"
    assert state["final_utterance"]


def test_accept_endpoint_409s_with_nothing_to_accept() -> None:
    client = _reasoning_client()
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]
    # No positive signal yet — the ✓ has nothing to conclude with.
    assert client.post(f"/api/sessions/{sid}/rounds/{rid}/accept").status_code == 409


def test_edit_endpoint_bans_and_mutes() -> None:
    client = _reasoning_client()
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]
    client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"})

    # ✗ + a note matching a woven value → strike-through ban.
    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/edit", json={"text": "no, not a call"}
    ).json()
    assert {"category": "how", "value": "call"} in state["banner"]["banned"]

    # ✗ + a slot dismissal → dim + strike mute.
    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/edit",
        json={"text": "the timing does not matter"},
    ).json()
    assert state["banner"]["muted"] == ["when"]
    assert state["outcome"] is None  # edits never end the round


def test_replace_and_restate_endpoints() -> None:
    client = _reasoning_client()
    sid = client.post("/api/sessions").json()["session_id"]
    # my_people: its core (who+how) matches the mock's question slots, so
    # the banner populates from the first yes.
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "my_people"}
    ).json()["round_id"]
    client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"})

    # A clicked segment's precise edit — an extension deepens the draft.
    state = client.post(
        f"/api/sessions/{sid}/rounds/{rid}/replace",
        json={"category": "how", "old": "call", "new": "call on the phone"},
    ).json()
    assert any(
        p["value"] == "call on the phone" for p in state["banner"]["parts"]
    )
    assert state["banner"]["banned"] == []  # a refinement strikes nothing

    # ⟳ restate: the draft re-words, nothing else moves.
    queries_before = state["query_count"]
    state = client.post(f"/api/sessions/{sid}/rounds/{rid}/restate").json()
    assert state["banner"]["text"] == "I would like a glass of water."
    assert state["query_count"] == queries_before
    assert state["outcome"] is None


def test_flip_endpoint_replaces_the_pending_question() -> None:
    # The opposition button: an action, not an answer — the same question
    # comes back mirrored and the round keeps waiting.
    client = _reasoning_client()
    sid = client.post("/api/sessions").json()["session_id"]
    state = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "my_people"}
    ).json()
    rid = state["round_id"]
    first = state["event"]["text"]
    assert first == "Do you want to call your son?"

    state = client.post(f"/api/sessions/{sid}/rounds/{rid}/flip").json()
    assert state["event"]["kind"] == "query"
    assert state["event"]["text"] == "Do you want your son to call you?"
    assert state["event"]["flipped_from"] == first
    assert state["history"] == []  # nothing was answered by the flip
    assert state["query_count"] == 0


def test_flip_without_a_pending_question_409s() -> None:
    client = _no_llm_client()
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]
    # The no-LLM round shows a diagnostic — there is no question to flip,
    # and the failure is soft (409), never a crash or a changed round.
    resp = client.post(f"/api/sessions/{sid}/rounds/{rid}/flip")
    assert resp.status_code == 409


# The live SSE stream is exercised by the cockpit; a TestClient cannot tear
# down an infinite streaming response cleanly, so the unit suite checks the
# route registration and the payload format instead.
def test_sse_event_route_is_registered() -> None:
    app = create_app(replace(Config.from_env(), llm_enabled=False), backend=None)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/sessions/{sid}/rounds/{rid}/events" in paths
    assert "/api/sessions/{sid}/rounds/{rid}/retry" in paths


def test_sse_payload_formatting() -> None:
    assert _sse({"phase": "thinking"}) == 'data: {"phase": "thinking"}\n\n'


def test_recording_disabled_without_real_profile() -> None:
    client = _no_llm_client()
    rec = client.get("/api/recording").json()
    assert rec["enabled"] is False
    assert rec["status"] == "disabled"


def test_dev_capture_records_a_synthetic_round(tmp_path) -> None:
    """Dev capture gives a SYNTHETIC trial a round record to audit.

    The patient dataset is correctly off for a synthetic run, which left a
    development trial with nothing to read back afterwards.
    """
    cfg = replace(
        Config.from_env(),
        llm_enabled=False,
        profile_path=None,
        dev_capture=True,
        dev_capture_dir=tmp_path / "dev",
    )
    client = TestClient(create_app(cfg, backend=None))

    rec = client.get("/api/recording").json()
    assert rec["enabled"] is True
    assert rec["dev"] is True  # …and it says so, so the light can differ

    sid = client.post("/api/sessions").json()["session_id"]
    client.post(f"/api/sessions/{sid}/rounds", json={"topic_id": "emergency"})
    assert client.get("/api/recording").json()["rounds"] == 1
    assert list((tmp_path / "dev").rglob("*.jsonl"))


def test_dev_capture_refuses_when_a_real_profile_is_loaded(tmp_path) -> None:
    """The mirror guard: dev capture implies SYNTHETIC, without exception.

    Otherwise the flag would be a second, weaker-guarded path by which real
    patient content could reach a directory outside the patient dataset — the
    exact thing the privacy invariant exists to prevent. A real profile keeps
    the patient dataset as its ONLY channel, whatever the flag says.
    """
    profile = tmp_path / "real.yaml"
    profile.write_text("id: test_patient\ndisplay_name: Test\n", encoding="utf-8")
    cfg = replace(
        Config.from_env(),
        llm_enabled=False,
        profile_path=profile,
        recording_dir=tmp_path / "data",
        dev_capture=True,                      # …asked for, and refused
        dev_capture_dir=tmp_path / "dev",
    )
    client = TestClient(create_app(cfg, backend=None))

    rec = client.get("/api/recording").json()
    assert rec["enabled"] is True
    assert rec["dev"] is False  # the PATIENT dataset, not the dev capture

    sid = client.post("/api/sessions").json()["session_id"]
    client.post(f"/api/sessions/{sid}/rounds", json={"topic_id": "emergency"})
    assert list((tmp_path / "data" / "test_patient").glob("*.jsonl"))
    assert not (tmp_path / "dev").exists()  # nothing reached the dev directory


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
    client = _no_llm_client()
    sid = client.post("/api/sessions").json()["session_id"]
    resp = client.post(
        f"/api/sessions/{sid}/emotion",
        json={"values": {"sad_happy": 0.3, "anxious_calm": -0.5}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def _reasoning_client(**cfg_overrides) -> TestClient:
    """A client whose injected reasoning backend reaches a confirmed utterance
    after a single yes (min_yes gate lowered for short tests)."""
    backend = _controller_backend(utterance="I would like a glass of water.")
    cfg = replace(
        Config.from_env(),
        reasoning=ReasoningTuning(),
        **cfg_overrides,
    )
    return TestClient(create_app(cfg, backend=backend))


def test_export_session_jsonl_mirrors_recording_format() -> None:
    client = _reasoning_client()
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]
    client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"})
    client.post(f"/api/sessions/{sid}/rounds/{rid}/accept")  # ✓ the banner draft

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
    # query entries carry their asserted slots for trial autopsies
    q = record["queries"][0]
    assert q["kind"] == "query" and q["slots"]

    md = client.get(f"/api/sessions/{sid}/export", params={"format": "md"})
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert "my20Q conversation" in md.text
    assert "Round 1" in md.text


def test_export_unknown_session_404() -> None:
    client = _no_llm_client()
    assert client.get("/api/sessions/nope/export").status_code == 404


def test_recordings_empty_without_real_profile() -> None:
    client = _no_llm_client()
    assert client.get("/api/recordings").json() == []
    assert client.get("/api/recordings/anything").status_code == 404


def test_recordings_list_and_read_for_real_profile(tmp_path) -> None:
    profile = tmp_path / "real.yaml"
    profile.write_text("id: test_patient\ndisplay_name: T\n", encoding="utf-8")
    # Real patient -> recorder active. A reasoning backend reaches a confirmed
    # (recordable) round; the yes-memory file lives under memory/ so it does not
    # masquerade as a session recording.
    client = _reasoning_client(profile_path=profile, recording_dir=tmp_path / "data")

    # record a round so there is a session file to review
    sid = client.post("/api/sessions").json()["session_id"]
    rid = client.post(
        f"/api/sessions/{sid}/rounds", json={"topic_id": "physical_health"}
    ).json()["round_id"]
    client.post(f"/api/sessions/{sid}/rounds/{rid}/answer", json={"answer": "yes"})
    client.post(f"/api/sessions/{sid}/rounds/{rid}/accept")  # ✓ the banner draft

    listing = client.get("/api/recordings").json()
    assert len(listing) == 1
    assert listing[0]["session_id"] == sid
    assert listing[0]["rounds"] == 1

    records = client.get(f"/api/recordings/{sid}").json()
    assert records[0]["outcome"] == "synthesized"
    assert "queries" in records[0]

    # path traversal is rejected
    assert client.get("/api/recordings/..%2f..%2fsecret").status_code == 404


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
