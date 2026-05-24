"""Tests for the retooled round engine — fallback and reasoning modes."""

from __future__ import annotations

import json

from my20q.agent.dialogue import Answer, Round, Session
from my20q.agent.prompts import reason_messages
from my20q.llm import MockBackend
from my20q.topics import Topic, find_topic


def _topic(topics: list[Topic], topic_id: str) -> Topic:
    t = find_topic(topics, topic_id)
    assert t is not None
    return t


def _action(kind: str, content: str, rationale: str = "because") -> str:
    return json.dumps({"action": kind, "content": content, "rationale": rationale})


def _scripted(*responses: str) -> MockBackend:
    """A MockBackend returning the given strings in order, then repeating
    the last one for any further calls."""
    state = {"i": 0}

    def responder(_msgs: list) -> str:
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return responses[i]

    return MockBackend(responder=responder)


# ----------------------------------------------------------- fallback mode


async def test_fallback_walks_question_bank(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=None)
    ev = await rnd.open()
    assert ev.kind == "query" and ev.engine == "fallback"
    first = ev.text
    ev = await rnd.answer(Answer.NO)
    assert ev.kind == "query" and ev.text != first


async def test_fallback_yes_synthesizes_then_confirms(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=None)
    await rnd.open()
    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesized"
    assert rnd.is_terminal and rnd.final_utterance


async def test_emergency_topic_short_circuits(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "emergency"), llm=None)
    ev = await rnd.open()
    assert ev.kind == "emergency"
    assert ev.emergency_screen is not None
    assert rnd.outcome == "emergency"


# ---------------------------------------------------------- reasoning mode


async def test_reasoning_round_reaches_synthesis(topics: list[Topic]) -> None:
    backend = _scripted(
        _action("query", "Is this about a family member you would like to contact?"),
        _action("query", "Would you like us to telephone them right now?"),
        _action("synthesis", "I would like to call my daughter to share some news."),
    )
    rnd = Round(_topic(topics, "my_people"), llm=backend, max_queries=20)
    ev = await rnd.open()
    assert ev.kind == "query" and ev.engine == "reasoning"
    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "query"
    ev = await rnd.answer(Answer.KINDA)
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesized"
    assert "daughter" in rnd.final_utterance


async def test_undo_rewinds_one_entry(topics: list[Topic]) -> None:
    backend = _scripted(
        _action("query", "Is this about a family member you would like to contact?"),
        _action("query", "Would you like us to telephone them right now?"),
        _action("query", "Do you want to tell them something specific today?"),
    )
    rnd = Round(_topic(topics, "my_people"), llm=backend, max_queries=20)
    await rnd.open()
    await rnd.answer(Answer.YES)
    await rnd.answer(Answer.NO)
    assert len(rnd.history) == 2
    ev = await rnd.undo()
    assert len(rnd.history) == 1
    assert ev.kind == "query"


async def test_budget_exhaustion_forces_synthesis_then_abandons(
    topics: list[Topic],
) -> None:
    backend = _scripted(
        _action("query", "Is this a question about being comfortable right now?"),
        _action("synthesis", "I would like to be more comfortable."),
    )
    rnd = Round(_topic(topics, "general"), llm=backend, max_queries=1)
    ev = await rnd.open()
    assert ev.kind == "query"
    ev = await rnd.answer(Answer.NO)  # budget hit -> forced synthesis
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # forced synthesis rejected -> abandoned
    assert ev.kind == "abandoned"
    assert rnd.outcome == "abandoned"


async def test_add_context_refreshes_the_pending_query(topics: list[Topic]) -> None:
    backend = _scripted(
        _action("query", "Is this about a family member you would like to contact?"),
        _action("query", "Would you like to send them a written message instead?"),
    )
    rnd = Round(_topic(topics, "my_people"), llm=backend)
    first = await rnd.open()
    # Adding context re-proposes — the on-screen query refreshes (and the
    # context lands in history).
    refreshed = await rnd.add_context("She mentioned her granddaughter earlier.")
    assert refreshed.kind == "query"
    assert refreshed.text != first.text
    assert "context" in [h["kind"] for h in rnd.history]


async def test_empty_context_leaves_query_unchanged(topics: list[Topic]) -> None:
    backend = _scripted(_action("query", "Is this about contacting someone?"))
    rnd = Round(_topic(topics, "my_people"), llm=backend)
    first = await rnd.open()
    same = await rnd.add_context("   ")
    assert same.text == first.text
    assert "context" not in [h["kind"] for h in rnd.history]


async def test_rationale_persists_in_history(topics: list[Topic]) -> None:
    backend = _scripted(
        _action("query", "Is this about contacting someone?", rationale="exploring contact"),
    )
    rnd = Round(_topic(topics, "my_people"), llm=backend)
    await rnd.open()
    await rnd.answer(Answer.NO)
    query_entry = next(h for h in rnd.history if h["kind"] == "query")
    assert query_entry["rationale"] == "exploring contact"


async def test_malformed_llm_degrades_to_fallback(topics: list[Topic]) -> None:
    backend = MockBackend(responder=lambda _m: "this is not json")
    rnd = Round(_topic(topics, "physical_health"), llm=backend)
    ev = await rnd.open()
    assert ev.engine == "fallback" and ev.kind == "query"


def test_session_tracks_topic_sequence(topics: list[Topic]) -> None:
    session = Session(topics, llm=None)
    session.start_round("my_people")
    session.start_round("physical_health")
    assert session.topic_sequence == ["my_people", "physical_health"]


def test_reason_messages_includes_emotional_reading() -> None:
    messages = reason_messages(
        "My feelings",
        [],
        1,
        20,
        emotional_state={"sad_happy": -0.6, "anxious_calm": 0.4},
    )
    content = messages[1]["content"]
    assert "EMOTIONAL READING" in content
    assert "sad" in content and "happy" in content
