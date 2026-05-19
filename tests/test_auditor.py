"""Tests for the format auditor and the reasoner's re-prompt loop."""

from __future__ import annotations

import json

from my20q.agent.auditor import audit_query
from my20q.agent.dialogue import Round
from my20q.llm import MockBackend
from my20q.topics import Topic, find_topic


def test_audit_passes_a_plain_yes_no() -> None:
    assert audit_query("Is this about your daughter?").ok


def test_audit_flags_missing_question_mark() -> None:
    assert not audit_query("Tell me about your day").ok


def test_audit_flags_either_or() -> None:
    result = audit_query("Is the thing inside or outside your house?")
    assert not result.ok
    assert "either/or" in result.reason


def test_audit_flags_open_wh_question() -> None:
    assert not audit_query("What do you need right now?").ok


async def test_reasoner_reprompts_until_query_passes(topics: list[Topic]) -> None:
    bad = json.dumps(
        {"action": "query", "content": "Is it inside or outside?", "rationale": "x"}
    )
    good = json.dumps(
        {"action": "query", "content": "Is it inside the house?", "rationale": "x"}
    )
    state = {"i": 0}

    def responder(_msgs: list) -> str:
        out = [bad, good][min(state["i"], 1)]
        state["i"] += 1
        return out

    topic = find_topic(topics, "physical_health")
    assert topic is not None
    rnd = Round(topic, llm=MockBackend(responder=responder))

    event = await rnd.open()
    assert event.kind == "query"
    assert event.text == "Is it inside the house?"  # the re-prompted, clean query
    assert "re-asked" in event.rationale
