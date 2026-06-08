"""Tests for the round engine — fallback and the hypothesis controller."""

from __future__ import annotations

import json

from my20q.agent.dialogue import (
    MAX_CONSEC_REASON_FAILURES,
    MIN_YES_FOR_SYNTHESIS,
    STALL_QUERIES,
    Answer,
    Round,
    Session,
)
from my20q.agent.hypotheses import Hypothesis
from my20q.agent.prompts import seed_messages
from my20q.llm import MockBackend
from my20q.llm.base import LLMUnavailable
from my20q.topics import Topic, find_topic


def test_single_need_no_is_soft_not_elimination(topics: list[Topic]) -> None:
    # "no" is a SOFT down-weight, never an elimination — hard elimination
    # collapsed the field so fast that synthesis fired before real confirmation.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 4)]
    rnd._history = [{"kind": "query", "text": "q", "answer": "no", "yes_ids": ["h2"]}]
    active, weights = rnd._replay_belief()
    assert "h2" in weights  # still in play, just less likely
    assert weights["h2"] < weights["h1"]


def test_multi_need_no_does_not_eliminate(topics: list[Topic]) -> None:
    # A "no" to a question spanning several needs is a soft down-weight, not an
    # elimination — the question may simply have been fuzzy.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 4)]
    rnd._history = [{"kind": "query", "text": "q", "answer": "no", "yes_ids": ["h1", "h2"]}]
    _, weights = rnd._replay_belief()
    assert {"h1", "h2"} <= set(weights)


def test_is_stalled_detects_no_progress(topics: list[Topic]) -> None:
    # STALL_QUERIES uninformative queries (each touching every need => uniform
    # update) leave the belief unmoved -> stalled.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 4)]
    rnd._history = [
        {"kind": "query", "text": f"q{i}", "answer": "yes", "yes_ids": ["h1", "h2", "h3"]}
        for i in range(STALL_QUERIES)
    ]
    _, weights = rnd._replay_belief()
    assert rnd._is_stalled(weights) is True


def test_is_stalled_false_while_converging(topics: list[Topic]) -> None:
    # A leader emerging (confidence rising as a need is repeatedly affirmed) is
    # progress, not a stall.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 9)]
    rnd._history = [
        {"kind": "query", "text": f"q{i}", "answer": "yes", "yes_ids": ["h1"]}
        for i in range(STALL_QUERIES)
    ]
    _, weights = rnd._replay_belief()
    assert rnd._is_stalled(weights) is False


def test_rejected_synthesis_eliminates_the_need(topics: list[Topic]) -> None:
    # A rejected synthesis must remove that need from the belief for good — a
    # soft down-weight let a later "yes" revive it and the round looped
    # re-proposing the same utterance (the "circular" failure mode).
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 4)]
    rnd._history = [
        {"kind": "query", "text": "q", "answer": "yes", "yes_ids": ["h2"]},
        {"kind": "synthesis", "text": "guess", "answer": "no", "hyp_id": "h2"},
        # Even a later "yes" pointing back at h2 must not revive it.
        {"kind": "query", "text": "q2", "answer": "yes", "yes_ids": ["h2"]},
    ]
    active, weights = rnd._replay_belief()
    assert "h2" not in weights
    assert all(h.id != "h2" for h in active)

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
        if "best SPLITS" in system:
            i = min(state["ask"], len(asks) - 1)
            state["ask"] += 1
            question, yes_ids = asks[i]
            return json.dumps(
                {"question": question, "yes_ids": yes_ids, "preface": "", "rationale": "split"}
            )
        if "sharpen" in system:  # clarify / deepen the leading need
            state["clarify"] = state.get("clarify", 0) + 1
            return json.dumps(
                {"question": f"Is it about detail {state['clarify']}?",
                 "preface": "", "rationale": "deepen"}
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


async def test_transient_reasoner_failure_recovers_next_turn(
    topics: list[Topic],
) -> None:
    # A single transient failure (e.g. a model still cold-loading after a
    # runtime switch) must NOT permanently kill the round — it falls back for
    # that turn, then reasoning resumes on the next turn.
    state = {"calls": 0}

    def responder(messages: list) -> str:
        state["calls"] += 1
        if state["calls"] == 1:  # first reasoning call (the seed) blips out
            raise LLMUnavailable("cold load timeout")
        system = messages[0]["content"]
        if "candidate NEEDS to test" in system:
            return json.dumps({"hypotheses": SEED_NEEDS})
        if "best SPLITS" in system:
            return json.dumps(
                {"question": "Is it about a drink?", "yes_ids": ["h1"],
                 "preface": "", "rationale": "x"}
            )
        return json.dumps({"utterance": "I would like a glass of water."})

    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend(responder=responder))
    ev = await rnd.open()
    assert ev.engine == "fallback" and ev.kind == "query"  # transient degrade
    ev = await rnd.answer(Answer.NO)
    assert ev.engine == "reasoning" and ev.kind == "query"  # recovered


async def test_persistent_reasoner_failure_degrades_for_good(
    topics: list[Topic],
) -> None:
    # Repeated failures (a genuinely-down LLM) eventually give up on reasoning
    # for the rest of the round; once dropped it stays fallback even if the
    # backend would now succeed.
    state = {"calls": 0}

    def responder(messages: list) -> str:
        state["calls"] += 1
        if state["calls"] <= MAX_CONSEC_REASON_FAILURES:
            raise LLMUnavailable("down")
        return json.dumps({"hypotheses": SEED_NEEDS})  # "recovers" — too late

    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend(responder=responder))
    ev = await rnd.open()  # failure 1 — transient
    for _ in range(MAX_CONSEC_REASON_FAILURES - 1):
        ev = await rnd.answer(Answer.NO)  # failures 2..N — last one goes permanent
    assert ev.engine == "fallback"
    calls_after_giveup = state["calls"]
    ev = await rnd.answer(Answer.NO)
    assert ev.engine == "fallback"  # stays fallback
    assert state["calls"] == calls_after_giveup  # reasoner dropped — not called


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
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=0)
    ev = await rnd.open()
    assert ev.kind == "query" and ev.engine == "reasoning"
    # The honest tile gets the full live belief.
    assert len(ev.hypotheses) == len(SEED_NEEDS)
    assert ev.hypotheses[0]["need"] in SEED_NEEDS

    ev = await rnd.answer(Answer.YES)
    yeses = 1
    assert ev.kind == "query"  # NOT synthesized yet — far short of the yes gate
    assert rnd.history[0]["yes_ids"] == ["h1", "h2"]

    # Keep confirming; an utterance must not appear before MIN_YES_FOR_SYNTHESIS.
    while ev.kind == "query" and yeses < 12:
        ev = await rnd.answer(Answer.YES)
        yeses += 1
    assert ev.kind == "synthesis"
    assert yeses >= MIN_YES_FOR_SYNTHESIS  # the positive-evidence gate held

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


async def test_safety_ceiling_stops_without_forcing_synthesis(topics: list[Topic]) -> None:
    # A positive max_queries is a hard SAFETY ceiling — it ends the round, it does
    # NOT force a half-baked utterance. Synthesis is readiness-driven only.
    backend = _controller_backend(asks=[("Is it something you want?", ["h1", "h2"])])
    rnd = Round(_topic(topics, "general"), llm=backend, max_queries=1)
    ev = await rnd.open()
    assert ev.kind == "query"
    ev = await rnd.answer(Answer.NO)  # 1 query asked == ceiling -> stop, no synthesis
    assert ev.kind == "abandoned"
    assert rnd.outcome == "abandoned"


async def test_unlimited_budget_keeps_questioning(topics: list[Topic]) -> None:
    # The default (max_queries=0) is unlimited: a non-converging answer keeps the
    # round collecting context rather than abandoning on a question count.
    backend = _controller_backend(
        asks=[
            ("Is it something you want?", ["h1", "h2"]),
            ("Are you thirsty?", ["h1"]),
        ]
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend)  # default unlimited
    assert rnd.max_queries == 0
    await rnd.open()
    ev = await rnd.answer(Answer.NO)
    assert rnd.outcome is None  # not abandoned by count
    assert ev.kind in ("query", "synthesis")


async def test_rejected_synthesis_keeps_going(topics: list[Topic]) -> None:
    backend = _controller_backend(
        asks=[
            ("Is it something you want?", ["h1", "h2"]),
            ("Are you thirsty?", ["h1"]),
            ("Is it about a person?", ["h4"]),
        ]
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend, max_queries=0)
    ev = await rnd.open()
    yeses = 0
    while ev.kind == "query" and yeses < 12:  # confirm past the yes gate
        ev = await rnd.answer(Answer.YES)
        yeses += 1
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
