"""Tests for the round engine — fallback and the hypothesis controller."""

from __future__ import annotations

import json

from my20q.agent.dialogue import Answer, Round, Session
from my20q.agent.prompts import seed_messages
from my20q.llm import MockBackend
from my20q.topics import Topic, find_topic

SEED_NEEDS = [
    "I am thirsty and want a glass of water",
    "My foot hurts",
    "I feel lonely",
    "I want to call my daughter",
]


def _topic(topics: list[Topic], topic_id: str) -> Topic:
    t = find_topic(topics, topic_id)
    assert t is not None
    return t


def _controller_backend(
    *,
    seed: list[str] = SEED_NEEDS,
    asks: list[tuple[str, list[str]]],
    utterance: str = "I would like a glass of water.",
    expand: list[str] | None = None,
    boost: list[str] | None = None,
) -> MockBackend:
    """A MockBackend that plays the seed/ask/expand/synthesize protocol.

    Branches on the system prompt: returns the seed set, the `expand` needs +
    `boost` ids for a context note, the next `asks`, or the utterance.
    """
    state = {"ask": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "candidate NEEDS to test" in system:
            return json.dumps({"hypotheses": seed})
        if "HIGH-TRUST context" in system:
            return json.dumps({"hypotheses": expand or [], "boost_ids": boost or []})
        if "Think it through" in system:  # deliberate (free-form) — return a draft
            return asks[min(state["ask"], len(asks) - 1)][0]
        if "Convert a drafted" in system:  # format the draft into JSON
            i = min(state["ask"], len(asks) - 1)
            state["ask"] += 1
            question, yes_ids = asks[i]
            return json.dumps(
                {"question": question, "yes_ids": yes_ids, "preface": "", "rationale": "split"}
            )
        return json.dumps({"utterance": utterance})  # synthesize

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


# -------------------------------------------------- hypothesis controller


async def test_reasoning_round_converges_via_belief(topics: list[Topic]) -> None:
    backend = _controller_backend(
        asks=[
            ("Is it something you want right now?", ["h1", "h2"]),
            ("Are you feeling thirsty?", ["h1"]),
            ("Is it about a drink?", ["h1"]),
        ]
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=20)
    ev = await rnd.open()
    assert ev.kind == "query" and ev.engine == "reasoning"
    # The honest tile gets the full live belief.
    assert len(ev.hypotheses) == len(SEED_NEEDS)
    assert ev.hypotheses[0]["need"] in SEED_NEEDS

    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "query"
    # The split is persisted for belief replay / undo.
    assert rnd.history[0]["yes_ids"] == ["h1", "h2"]

    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesized"
    assert "water" in rnd.final_utterance


async def test_undo_recomputes_belief(topics: list[Topic]) -> None:
    backend = _controller_backend(
        asks=[("Is it something you want?", ["h1", "h2"]), ("Is it a drink?", ["h1"])]
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=20)
    await rnd.open()
    await rnd.answer(Answer.YES)
    assert len(rnd.history) == 1
    ev = await rnd.undo()
    assert len(rnd.history) == 0
    assert ev.kind == "query" and len(ev.hypotheses) == len(SEED_NEEDS)


async def test_budget_forces_synthesis_then_abandons(topics: list[Topic]) -> None:
    backend = _controller_backend(asks=[("Is it something you want?", ["h1", "h2"])])
    rnd = Round(_topic(topics, "general"), llm=backend, max_queries=1)
    ev = await rnd.open()
    assert ev.kind == "query"
    ev = await rnd.answer(Answer.NO)  # budget hit -> forced synthesis
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # rejected -> abandoned
    assert ev.kind == "abandoned"
    assert rnd.outcome == "abandoned"


async def test_rejected_synthesis_keeps_going(topics: list[Topic]) -> None:
    backend = _controller_backend(
        asks=[
            ("Is it something you want?", ["h1", "h2"]),
            ("Are you thirsty?", ["h1"]),
            ("Is it about a person?", ["h4"]),
        ]
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=20)
    await rnd.open()
    await rnd.answer(Answer.YES)
    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # rejects the proposal — round continues
    assert rnd.outcome is None
    assert ev.kind in ("query", "synthesis")


async def test_add_context_steers_next_question(topics: list[Topic]) -> None:
    backend = _controller_backend(
        asks=[("Is it something you want?", ["h1", "h2"]), ("Is it a drink?", ["h1"])]
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend)
    first = await rnd.open()
    refreshed = await rnd.add_context("She keeps pointing at the empty cup.")
    assert refreshed.kind == "query"
    assert "context" in [h["kind"] for h in rnd.history]
    assert refreshed.text != first.text


async def test_add_context_expands_belief(topics: list[Topic]) -> None:
    backend = _controller_backend(
        seed=["My foot hurts", "I feel lonely", "I want to stand up", "I feel tired"],
        asks=[("Is it about your body?", ["h1"]), ("Is it a drink?", ["h5"])],
        expand=["I am thirsty and want a drink"],
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=20)
    ev = await rnd.open()
    assert len(ev.hypotheses) == 4
    ev = await rnd.add_context("She keeps pointing at the empty cup.")
    # The note's need was added and — being high-trust — leads the belief.
    assert len(ev.hypotheses) == 5
    assert ev.hypotheses[0]["need"] == "I am thirsty and want a drink"
    assert "added" in rnd.history[-1]  # recorded so undo can reconstruct


async def test_context_boosts_an_existing_candidate(topics: list[Topic]) -> None:
    # The note adds no new need but confirms an existing one (h1, thirst) — the
    # high-trust signal makes it lead even though it was a uniform seed.
    backend = _controller_backend(
        asks=[("Is it about your body?", ["h2"])], expand=[], boost=["h1"]
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=20)
    await rnd.open()
    ev = await rnd.add_context("She keeps reaching for her water cup.")
    assert len(ev.hypotheses) == len(SEED_NEEDS)  # nothing new added
    assert ev.hypotheses[0]["need"] == SEED_NEEDS[0]  # thirst now leads
    assert ev.hypotheses[0]["weight"] > 0.5
    assert rnd.history[-1]["boost"] == ["h1"]


async def test_undo_removes_context_hypotheses(topics: list[Topic]) -> None:
    backend = _controller_backend(
        seed=["My foot hurts", "I feel lonely", "I want to stand up", "I feel tired"],
        asks=[
            ("Is it about your body?", ["h1"]),
            ("Is it a drink?", ["h5"]),
            ("Is it your body?", ["h1"]),
        ],
        expand=["I am thirsty and want a drink"],
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=20)
    await rnd.open()
    ev = await rnd.add_context("pointing at the cup")
    assert len(ev.hypotheses) == 5
    ev = await rnd.undo()  # pops the context entry -> the added need disappears
    assert len(ev.hypotheses) == 4
    assert all("thirsty" not in h["need"] for h in ev.hypotheses)


async def test_malformed_seed_degrades_to_fallback(topics: list[Topic]) -> None:
    backend = MockBackend(responder=lambda _m: "this is not json")
    rnd = Round(_topic(topics, "physical_health"), llm=backend)
    ev = await rnd.open()
    assert ev.engine == "fallback" and ev.kind == "query"


def test_session_tracks_topic_sequence(topics: list[Topic]) -> None:
    session = Session(topics, llm=None)
    session.start_round("my_people")
    session.start_round("physical_health")
    assert session.topic_sequence == ["my_people", "physical_health"]


def test_seed_messages_includes_emotional_reading() -> None:
    messages = seed_messages(
        "My feelings",
        emotional_state={"sad_happy": -0.6, "anxious_calm": 0.4},
    )
    content = messages[1]["content"]
    assert "EMOTIONAL READING" in content
    assert "sad" in content and "happy" in content
