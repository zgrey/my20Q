"""Tests for the format auditor and the reasoner's re-prompt loop."""

from __future__ import annotations

import json

from my20q.agent.auditor import audit_query
from my20q.agent.reasoner import Reasoner
from my20q.llm import MockBackend


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


async def test_ask_reprompts_until_query_passes() -> None:
    # The format pass first yields an either/or (fails the audit); the reasoner
    # feeds the reason back and re-asks until it gets a clean yes/no.
    bad = json.dumps({"question": "Is it inside or outside?", "yes_ids": ["h1"], "rationale": "x"})
    good = json.dumps({"question": "Is it inside the house?", "yes_ids": ["h1"], "rationale": "x"})
    state = {"fmt": 0}

    def responder(msgs: list) -> str:
        if "Convert a drafted question" in msgs[0]["content"]:  # FORMAT pass
            out = [bad, good][min(state["fmt"], 1)]
            state["fmt"] += 1
            return out
        return "thinking about inside vs the house"  # DELIBERATE draft

    reasoner = Reasoner(MockBackend(responder=responder))
    action = await reasoner.ask(
        topic_label="My body",
        candidates=[("h1", "it is inside", 0.0), ("h2", "it is outside", 0.0)],
        history=[],
    )
    assert action.kind == "query"
    assert action.content == "Is it inside the house?"  # the re-prompted, clean query
    assert action.yes_ids == ["h1"]


async def test_ask_can_propose_a_new_need() -> None:
    # A question that explores beyond the candidate list returns new_need and gets
    # a freshly minted candidate id — exploration escapes the seed set.
    out = json.dumps(
        {"question": "Are you scared?", "yes_ids": [], "new_need": "I feel scared",
         "preface": "", "rationale": "x"}
    )
    reasoner = Reasoner(MockBackend(responder=lambda _m: out))
    action = await reasoner.ask(
        topic_label="My feelings",
        candidates=[("h1", "I am lonely", 0.0)],
        history=[],
    )
    assert action.content == "Are you scared?"
    assert action.new_need == "I feel scared"
    assert action.yes_ids == ["n1"]  # minted, not an existing candidate
