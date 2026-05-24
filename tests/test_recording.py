"""Tests for the recording dataset writer and the Job-B metric."""

from __future__ import annotations

import json

from my20q.agent.dialogue import Answer, Round
from my20q.recording import Recorder, job_b_score, transcript
from my20q.topics import Topic, find_topic


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
    rnd = Round(topic, llm=None)
    await rnd.open()
    await rnd.answer(Answer.YES)  # fallback query -> synthesis
    await rnd.answer(Answer.YES)  # confirm -> synthesized

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


def test_transcript_markdown_renders_rounds_and_context() -> None:
    rounds = [
        {
            "round_id": "r1",
            "topic_id": "my_people",
            "topic_label": "My people",
            "engine": "reasoning",
            "outcome": "synthesized",
            "final_utterance": "I would like to call my son.",
            "history": [
                {"kind": "query", "text": "Is this about someone?", "answer": "yes"},
                {"kind": "context", "text": "He pointed at a photo.", "answer": None},
                {"kind": "synthesis", "text": "Call my son.", "answer": "yes"},
            ],
            "emotional_state": {"sad_happy": -0.4, "anxious_calm": 0.0},
            "job_b": 11.0,
        }
    ]
    payload = transcript.session_payload("session-abcdef12", rounds)
    md = transcript.to_markdown(payload)
    assert "# my20Q conversation" in md
    assert "Round 1 — My people (reasoning)" in md
    assert "caregiver context:_ He pointed at a photo." in md
    assert "“I would like to call my son.”" in md
    assert "sad_happy -0.40" in md  # zero-valued pairs are dropped
    assert "anxious_calm" not in md


def test_transcript_handles_empty_session() -> None:
    payload = transcript.session_payload("s0", [])
    md = transcript.to_markdown(payload)
    assert "no rounds" in md
    assert transcript.to_json(payload).strip().startswith("{")


async def test_abandon_finalizes_a_live_round(topics: list[Topic]) -> None:
    topic = find_topic(topics, "my_people")
    assert topic is not None
    rnd = Round(topic, llm=None)
    await rnd.open()
    assert not rnd.is_terminal
    rnd.abandon()
    assert rnd.outcome == "abandoned"
    assert rnd.is_terminal
