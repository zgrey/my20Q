"""Tests for the recording dataset writer and the Job-B metric."""

from __future__ import annotations

import json

from my20q.agent.dialogue import Answer, Round
from my20q.recording import Recorder, job_b_score
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


async def test_abandon_finalizes_a_live_round(topics: list[Topic]) -> None:
    topic = find_topic(topics, "my_people")
    assert topic is not None
    rnd = Round(topic, llm=None)
    await rnd.open()
    assert not rnd.is_terminal
    rnd.abandon()
    assert rnd.outcome == "abandoned"
    assert rnd.is_terminal
