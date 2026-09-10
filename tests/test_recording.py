"""Tests for the recording dataset writer and the Job-B metric."""

from __future__ import annotations

import json

from my20q.agent.dialogue import Answer, Round
from my20q.llm import MockBackend
from my20q.recording import Recorder, job_b_score, transcript
from my20q.topics import Topic, find_topic


def _reasoning_backend() -> MockBackend:
    """A MockBackend that seeds the board, cycles questions, and weaves."""
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(
                {"what": ["a drink", "a rest"], "how": ["bring it"], "why": ["thirsty"]}
            )
        if "pin down the ONE specific" in system:  # deliberate
            return "thinking..."
        if "Convert a drafted question" in system:  # format
            word = ["a drink", "a rest", "bring it"][state["q"] % 3]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {"what": word},
                 "preface": "", "rationale": "x"}
            )
        return json.dumps({"utterance": "I would like a glass of water."})

    return MockBackend(responder=responder)


def test_job_b_rewards_a_confirmed_round() -> None:
    history = [
        {"kind": "query", "text": "q1", "answer": "yes"},
        {"kind": "query", "text": "q2", "answer": "kinda"},
        {"kind": "synthesis", "text": "s", "answer": "yes"},
    ]
    assert job_b_score(history, "synthesized") > 10  # the W_final term dominates


def test_job_b_penalizes_an_abandoned_round() -> None:
    history = [{"kind": "query", "text": "q1", "answer": "no"}]
    assert job_b_score(history, "abandoned") < 0


async def test_recorder_writes_a_round(tmp_path, topics: list[Topic]) -> None:
    topic = find_topic(topics, "physical_health")
    assert topic is not None
    # Only a reasoning round reaches a confirmed utterance — the caregiver
    # accepts the live banner draft (the engine never proposes on its own).
    rnd = Round(topic, llm=_reasoning_backend())
    await rnd.open()
    await rnd.answer(Answer.YES)  # signal on the board → the banner drafts
    rnd.accept()  # ✓ — conclude with the draft
    assert rnd.outcome == "synthesized"

    recorder = Recorder(tmp_path, "patient_x")
    recorder.record_round(
        session_id="s1",
        round_id="r1",
        topic_id=topic.id,
        engine=rnd.engine,
        history=rnd.history,
        outcome=rnd.outcome,
        final_utterance=rnd.final_utterance,
        model="fallback",
    )
    assert recorder.dataset_bytes() > 0
    assert recorder.rounds_recorded() == 1

    line = (tmp_path / "patient_x" / "s1.jsonl").read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["outcome"] == "synthesized"
    assert record["topic_id"] == "physical_health"
    assert "job_b" in record


def _sample_record() -> dict:
    """A round record in the shared recording schema."""
    from my20q.recording import build_round_record

    return build_round_record(
        session_id="session-abcdef12",
        round_id="r1",
        topic_id="my_people",
        engine="reasoning",
        history=[
            {"kind": "query", "text": "Is this about someone?", "answer": "yes"},
            {"kind": "context", "text": "He pointed at a photo.", "answer": None},
            {"kind": "synthesis", "text": "Call my son.", "answer": "yes"},
        ],
        outcome="synthesized",
        final_utterance="I would like to call my son.",
        model="fallback",
        emotional_state={"sad_happy": -0.4, "anxious_calm": 0.0},
    )


def test_export_jsonl_matches_recording_record_schema() -> None:
    record = _sample_record()
    jsonl = transcript.to_jsonl([record])
    lines = [ln for ln in jsonl.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    # identical to what Recorder.record_round writes
    assert parsed == record
    assert set(parsed) >= {
        "session_id",
        "round_id",
        "topic_id",
        "engine",
        "outcome",
        "final_utterance",
        "query_count",
        "job_b",
        "queries",
        "emotional_state",
        "model",
        "recorded_at",
    }


def test_transcript_markdown_renders_rounds_and_context() -> None:
    md = transcript.to_markdown("session-abcdef12", [_sample_record()])
    assert "# my20Q conversation" in md
    assert "Round 1 — my_people (reasoning)" in md
    assert "caregiver context:_ He pointed at a photo." in md
    assert "“I would like to call my son.”" in md
    assert "sad_happy -0.40" in md  # zero-valued pairs are dropped
    assert "anxious_calm" not in md


def test_transcript_handles_empty_session() -> None:
    assert "no rounds" in transcript.to_markdown("s0", [])
    assert transcript.to_jsonl([]) == ""


async def test_abandon_finalizes_a_live_round(topics: list[Topic]) -> None:
    topic = find_topic(topics, "my_people")
    assert topic is not None
    rnd = Round(topic, llm=None)
    await rnd.open()  # surfaces a no-LLM diagnostic; the round stays live
    assert not rnd.is_terminal
    rnd.abandon()
    assert rnd.outcome == "abandoned"
    assert rnd.is_terminal


def test_round_record_drops_internal_markers() -> None:
    from my20q.recording import build_round_record

    record = build_round_record(
        session_id="s",
        round_id="r",
        topic_id="general",
        engine="reasoning",
        history=[
            {"kind": "query", "text": "q", "answer": "no", "slots": {"what": "x"}},
            {"kind": "restart", "reason": "no-streak", "board": {}},
            {"kind": "diagnostic", "text": "ask failed", "answer": None},
        ],
        outcome=None,
        final_utterance="",
        model="m",
    )
    kinds = [q["kind"] for q in record["queries"]]
    assert "restart" not in kinds  # internal board marker — not conversation
    assert "diagnostic" in kinds  # the failure the caregiver saw IS kept


# ------------------------------------------- autopsy instrumentation (W2-O)


async def test_record_carries_the_seed_context(tmp_path, topics: list[Topic]) -> None:
    # The caregiver's round-opening note steered every question in the round;
    # a record without it is a round read blind. (It used to be probed off
    # `job_b` — a float — so the dumper's branch never once fired.)
    topic = find_topic(topics, "physical_health")
    assert topic is not None
    rnd = Round(topic, llm=_reasoning_backend(), seed_context="She slept badly.")
    await rnd.open()
    await rnd.answer(Answer.YES)
    rnd.accept()

    recorder = Recorder(tmp_path, "patient_x")
    recorder.record_round(
        session_id="s1", round_id="r1", topic_id=topic.id, engine=rnd.engine,
        history=rnd.history, outcome=rnd.outcome,
        final_utterance=rnd.final_utterance, model="mock",
        seed_context=rnd.seed_context, seed_ms=rnd.seed_ms,
    )
    record = json.loads(
        (tmp_path / "patient_x" / "s1.jsonl").read_text(encoding="utf-8").strip()
    )
    assert record["seed_context"] == "She slept badly."
    # Per-query instrumentation rode along on the history entries.
    q = next(e for e in record["queries"] if e["kind"] == "query")
    assert q["timing"]["llm_calls"] >= 1
    assert "banner" in q


def test_optional_instrumentation_is_omitted_when_empty() -> None:
    # Every W2-O field is additive and absent by default, so pre-W2-O
    # recordings and no-context rounds keep exactly the old shape.
    from my20q.recording import build_round_record

    record = build_round_record(
        session_id="s", round_id="r", topic_id="general", engine="reasoning",
        history=[], outcome=None, final_utterance="", model="m",
    )
    for field in ("seed_context", "seed_ms", "pending_question", "clarifications"):
        assert field not in record


def test_clarifications_are_recorded_without_perturbing_metrics() -> None:
    # W2-T contradictions are a TOP-LEVEL field, never history entries, so the
    # round's metrics are exactly what they were before it.
    from my20q.recording import build_round_record

    history = [
        {"kind": "query", "text": "Tingling in your toes?", "answer": "yes",
         "slots": {"what": "tingling"}},
        {"kind": "query", "text": "Is it tingling?", "answer": "no",
         "slots": {"what": "tingling"}, "verify": True, "contested": True},
        {"kind": "query", "text": "Tingling in your toes, still?",
         "answer": "yes", "slots": {"what": "tingling"}, "clarify": True},
    ]
    clarifications = [
        {"category": "what", "value": "tingling",
         "verify_question": "Is it tingling?",
         "attempts": [{"text": "Tingling in your toes, still?", "answer": "yes"}],
         "outcome": "confirmed"},
    ]
    common = dict(
        session_id="s", round_id="r", topic_id="my_body", engine="reasoning",
        history=history, outcome="synthesized", final_utterance="x", model="m",
    )
    record = build_round_record(**common, clarifications=clarifications)
    baseline = build_round_record(**common)
    assert record["clarifications"][0]["outcome"] == "confirmed"
    assert record["query_count"] == baseline["query_count"] == 3
    assert record["job_b"] == baseline["job_b"]


async def test_pending_question_is_recorded_without_perturbing_metrics(
    tmp_path, topics: list[Topic]
) -> None:
    # A topic switch abandons the round with a question on screen. It is a
    # TOP-LEVEL field, never a history entry, so query_count and job_b are
    # exactly what they were before W2-O.
    topic = find_topic(topics, "physical_health")
    assert topic is not None
    rnd = Round(topic, llm=_reasoning_backend())
    await rnd.open()
    await rnd.answer(Answer.YES)
    unanswered = rnd.pending_question
    rnd.abandon()

    from my20q.recording import build_round_record

    common = dict(
        session_id="s", round_id="r", topic_id=topic.id, engine=rnd.engine,
        history=rnd.history, outcome=rnd.outcome,
        final_utterance=rnd.final_utterance, model="mock",
    )
    record = build_round_record(**common, pending_question=rnd.pending_question)
    baseline = build_round_record(**common)
    assert record["pending_question"]["text"] == unanswered["text"]
    assert record["query_count"] == baseline["query_count"] == 1
    assert record["job_b"] == baseline["job_b"]


def test_round_record_carries_the_board() -> None:
    from my20q.recording import build_round_record

    record = build_round_record(
        session_id="s",
        round_id="r",
        topic_id="general",
        engine="reasoning",
        history=[],
        outcome=None,
        final_utterance="",
        model="m",
        board={"seeds": {"what": ["a drink"]}, "final": {"what": [["a drink", 1.0]]}},
    )
    assert record["board"]["seeds"] == {"what": ["a drink"]}
    assert record["board"]["final"]["what"] == [["a drink", 1.0]]
