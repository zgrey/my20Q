"""Tests for the round engine — fallback and the hypothesis controller."""

from __future__ import annotations

import json
import random

from my20q.agent.dialogue import (
    MAX_CONSEC_REASON_FAILURES,
    MIN_YES_FOR_SYNTHESIS,
    SOFT_RESET_NO_STREAK,
    Answer,
    Round,
    Session,
)
from my20q.agent.hypotheses import Hypothesis
from my20q.agent.prompts import ask_messages, seed_messages
from my20q.config import ReasoningTuning
from my20q.llm import MockBackend
from my20q.llm.base import LLMUnavailable
from my20q.recording.yes_memory import YesMemory
from my20q.topics import Topic, find_topic


class _FixedRandom(random.Random):
    """A deterministic RNG whose random() always returns a fixed value (tests)."""

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:
        """Return the pinned value instead of a real draw."""
        return self._value


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


def test_rejected_synthesis_keeps_need_for_rephrase(topics: list[Topic]) -> None:
    # A rejected synthesis NO LONGER eliminates its need — the round rephrases the
    # SAME need instead, so it must survive in the belief (a "no" to an utterance
    # does not subtract from the need it was built from).
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 4)]
    rnd._history = [
        {"kind": "query", "text": "q", "answer": "yes", "yes_ids": ["h2"]},
        {"kind": "synthesis", "text": "guess", "answer": "no", "hyp_id": "h2"},
    ]
    active, weights = rnd._replay_belief()
    assert "h2" in weights and any(h.id == "h2" for h in active)  # survives
    assert weights["h2"] == 1.0  # the synthesis "no" did not touch the belief

SEED_NEEDS = [
    "I am thirsty and want a glass of water",
    "My foot hurts",
    "I feel lonely",
    "I want to call my daughter",
]

# Distinct subjects so the mock's drill questions never trip the redundancy audit
# (used by physical_health / general rounds, so body words are on-topic).
_DISTINCT_SUBJECTS = [
    "water", "food", "resting", "the blanket", "your chair", "a snack",
    "moving around", "the lights", "the noise", "sleep", "sitting up", "warmth",
]


def _topic(topics: list[Topic], topic_id: str) -> Topic:
    t = find_topic(topics, topic_id)
    assert t is not None
    return t


def test_exploratory_new_need_spawns_candidate_on_yes(topics: list[Topic]) -> None:
    # A question that explored a brand-new need (not in the seeds) becomes a real
    # candidate once the person says yes/kinda — exploration escapes the seed set.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis("h1", "need 1"), Hypothesis("h2", "need 2")]
    rnd._history = [
        {"kind": "query", "text": "Are you scared?", "answer": "yes",
         "yes_ids": ["n1"], "new_need": "I feel scared and confused"},
    ]
    active, scores = rnd._replay_belief()
    assert any(h.id == "n1" and h.need == "I feel scared and confused" for h in active)
    assert scores["n1"] > 0


def test_exploratory_new_need_dropped_on_no(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis("h1", "need 1"), Hypothesis("h2", "need 2")]
    rnd._history = [
        {"kind": "query", "text": "Are you scared?", "answer": "no",
         "yes_ids": ["n1"], "new_need": "I feel scared"},
    ]
    active, scores = rnd._replay_belief()
    assert all(h.id != "n1" for h in active)  # a "no" never spawns the new need
    assert "n1" not in scores


def test_consec_no_streak_counts_tail(topics: list[Topic]) -> None:
    # The soft-reset trigger counts consecutive "no" answers from the tail: a
    # "yes"/"kinda" ends the run; "not sure" and caregiver context are transparent.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._history = [
        {"kind": "query", "text": "q1", "answer": "yes", "yes_ids": ["h1"]},
        {"kind": "query", "text": "q2", "answer": "no", "yes_ids": ["h1"]},
        {"kind": "context", "text": "note", "answer": None},
        {"kind": "query", "text": "q3", "answer": "not_sure", "yes_ids": ["h1"]},
        {"kind": "query", "text": "q4", "answer": "no", "yes_ids": ["h1"]},
    ]
    # q4 (no) + q2 (no); the not_sure and context between them don't break the run,
    # but the earlier "yes" does.
    assert rnd._consec_no_streak() == 2


def test_soft_reset_dumps_kinda_rewards(topics: list[Topic]) -> None:
    # Under a soft reset the "kinda" rewards are withheld so a lukewarm-but-wrong
    # guess no longer holds the lead — the candidate stays in play at 0.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis("h1", "need 1"), Hypothesis("h2", "need 2")]
    rnd._history = [{"kind": "query", "text": "q", "answer": "kinda", "yes_ids": ["h1"]}]
    _, normal = rnd._replay_belief()
    assert normal["h1"] == 0.5  # warmed by the kinda
    _, dumped = rnd._replay_belief(drop_kinda=True)
    assert dumped["h1"] == 0.0  # warm reward dumped; still present (in play)


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
        if "pin down the ONE specific" in system:  # ask / drill
            i = min(state["ask"], len(asks) - 1)
            _, yes_ids = asks[i]
            # Distinct content words each turn so the redundancy audit passes even
            # past the end of the fixed `asks` list.
            word = _DISTINCT_SUBJECTS[state["ask"] % len(_DISTINCT_SUBJECTS)]
            state["ask"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "yes_ids": yes_ids,
                 "preface": "", "rationale": "drill"}
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


async def test_fallback_only_questions_never_ends(topics: list[Topic]) -> None:
    # Fallback mode (no LLM) now ONLY asks questions — it never synthesizes and
    # never ends the round. Only a "yes" to an utterance ends a round, and only the
    # reasoner produces utterances, so a fallback round just keeps questioning.
    rnd = Round(_topic(topics, "physical_health"), llm=None)
    ev = await rnd.open()
    assert ev.kind == "query" and ev.engine == "fallback"
    for _ in range(3):
        ev = await rnd.answer(Answer.YES)  # "yes" to a fallback QUESTION
        assert ev.kind == "query"  # never a synthesis / round end
        assert rnd.outcome is None and not rnd.is_terminal


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
        if "pin down the ONE specific" in system:
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


async def test_persistent_reasoner_failure_keeps_retrying(
    topics: list[Topic],
) -> None:
    # Reasoning is NEVER permanently abandoned: even after many consecutive
    # failures (well past the old give-up threshold) the reasoner is retried every
    # turn, the round never ends on a failure, and a recovery resumes reasoning.
    recover_at = MAX_CONSEC_REASON_FAILURES + 3
    state = {"calls": 0}

    def responder(messages: list) -> str:
        state["calls"] += 1
        if state["calls"] < recover_at:
            raise LLMUnavailable("down")
        system = messages[0]["content"]
        if "candidate NEEDS to test" in system:
            return json.dumps({"hypotheses": SEED_NEEDS})
        if "pin down the ONE specific" in system:
            return json.dumps(
                {"question": "Is it about a drink?", "yes_ids": ["h1"],
                 "preface": "", "rationale": "x"}
            )
        return json.dumps({"utterance": "I would like a glass of water."})

    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend(responder=responder))
    ev = await rnd.open()  # seed fails -> fallback question
    assert ev.engine == "fallback" and not rnd.is_terminal
    # Drive several turns through the persistent failure — never terminal, never
    # permanently dropped (well past MAX_CONSEC_REASON_FAILURES).
    while state["calls"] < recover_at - 1:
        ev = await rnd.answer(Answer.NO)
        assert ev.engine == "fallback" and not rnd.is_terminal
    # Backend recovers -> reasoning resumes (the reasoner was kept all along).
    ev = await rnd.answer(Answer.NO)
    assert ev.engine == "reasoning" and ev.kind == "query"


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


async def test_soft_reset_fires_after_no_streak(topics: list[Topic]) -> None:
    # More than SOFT_RESET_NO_STREAK consecutive "no" answers triggers a soft
    # reset: the next ask carries the SOFT RESET framing. It must NOT fire one
    # "no" early, and the round must stay in reasoning mode (not degrade).
    seen = {"reset": False}
    n = {"ask": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "candidate NEEDS to test" in system:
            return json.dumps({"hypotheses": SEED_NEEDS})
        if "pin down the ONE specific" in system:  # ask / drill
            if "SOFT RESET" in messages[1]["content"]:
                seen["reset"] = True
            word = _DISTINCT_SUBJECTS[n["ask"] % len(_DISTINCT_SUBJECTS)]
            n["ask"] += 1
            # Explore a brand-new need each turn so a wall of "no" never depletes
            # the seed candidates (a "no" on a minted id leaves the seeds alive).
            return json.dumps(
                {"question": f"Is it about {word}?", "yes_ids": [],
                 "new_need": f"I need {word}", "preface": "", "rationale": "drill"}
            )
        return json.dumps({"utterance": "x"})

    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend(responder=responder))
    await rnd.open()
    for _ in range(SOFT_RESET_NO_STREAK):  # exactly the threshold -> still no reset
        await rnd.answer(Answer.NO)
    assert not seen["reset"]
    await rnd.answer(Answer.NO)  # one MORE than the threshold -> reset fires
    assert seen["reset"]
    assert rnd.engine == "reasoning"  # stayed in reasoning, did not degrade


def test_ask_messages_reset_injects_reset_framing() -> None:
    # A reset ask drops the warm "kinda" framing and re-grounds in the yeses.
    content = ask_messages(
        "My feelings",
        [("h1", "I feel scared", -2.0)],
        [{"kind": "query", "text": "Are you hungry?", "answer": "yes"}],
        reset=True,
        yes_texts=["Are you hungry?"],
        kinda_texts=["Are you cold?"],
    )[1]["content"]
    assert "SOFT RESET" in content
    assert "CONFIRMED so far" in content and "Are you hungry?" in content
    assert "NEARLY RIGHT" not in content  # the kinda block is suppressed on reset


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


def test_reseed_marker_resets_belief(topics: list[Topic]) -> None:
    # A reseed marker DUMPS the accumulated belief and restarts from its seeds.
    rnd = Round(_topic(topics, "mental_health"), llm=MockBackend())
    rnd._seed_hypotheses = [Hypothesis("h1", "old need")]
    rnd._history = [
        {"kind": "query", "text": "q", "answer": "yes", "yes_ids": ["h1"]},
        {"kind": "reseed", "seeds": [{"id": "s1", "need": "fresh A"},
                                     {"id": "s2", "need": "fresh B"}]},
    ]
    active, scores = rnd._replay_belief()
    assert {h.need for h in active} == {"fresh A", "fresh B"}  # restarted
    assert "h1" not in scores  # the old belief was dumped
    assert scores == {"s1": 0.0, "s2": 0.0}  # fresh seeds, zeroed


def _explorer_backend() -> MockBackend:
    """A backend whose questions always explore a NEW need (minted id), so the
    drilling never depends on specific seed ids — survives a reseed cleanly."""
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "candidate NEEDS to test" in system:
            return json.dumps({"hypotheses": SEED_NEEDS})
        if "pin down the ONE specific" in system:
            word = _DISTINCT_SUBJECTS[state["q"] % len(_DISTINCT_SUBJECTS)]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "yes_ids": [],
                 "new_need": f"I need {word}", "preface": "", "rationale": "x"}
            )
        return json.dumps({"utterance": "I would like a glass of water."})

    return MockBackend(responder=responder)


async def test_round_never_ends_until_yes_rephrases_then_reseeds(
    topics: list[Topic],
) -> None:
    # The full synthesis loop with small thresholds: question -> synthesize ->
    # rephrase -> requestion -> synthesize -> after 2 failed attempts, DUMP+reseed.
    # The round NEVER ends until a "yes" to an utterance.
    tuning = ReasoningTuning(
        min_yes_for_synthesis=2,
        new_yes_for_resynthesis=1,
        rephrase_limit=1,
        synth_attempts_before_reseed=2,
    )
    rnd = Round(_topic(topics, "physical_health"), llm=_explorer_backend(), tuning=tuning)
    await rnd.open()
    ev = await rnd.answer(Answer.YES)  # 1 yes -> still questioning
    assert ev.kind == "query"
    ev = await rnd.answer(Answer.YES)  # 2 yeses -> synthesis attempt #1
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # reject -> rephrase
    assert ev.kind == "synthesis" and rnd.outcome is None
    ev = await rnd.answer(Answer.NO)  # reject again -> attempt #1 exhausted -> question
    assert ev.kind == "query" and rnd.outcome is None
    ev = await rnd.answer(Answer.YES)  # 1 NEW yes -> synthesis attempt #2
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # reject -> rephrase
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # reject -> attempt #2 exhausted -> RESEED
    assert ev.kind == "query" and rnd.outcome is None
    assert any(h["kind"] == "reseed" for h in rnd.history)  # context was dumped
    # The ONLY way to end is a "yes" to an utterance. Post-reseed needs `new_yes`
    # (a synthesis was already attempted this round), so one yes reaches the proposal.
    ev = await rnd.answer(Answer.YES)  # 1 yes post-reseed -> synthesis
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.YES)  # YES to the utterance -> ends
    assert ev.kind == "synthesized" and rnd.is_terminal


async def test_round_records_yes_into_shared_memory(topics: list[Topic]) -> None:
    # Each confirmed-yes query lands in the (session-shared) yes-memory, the durable
    # signal used to reseed and to seed later rounds in the session.
    mem = YesMemory()
    backend = _controller_backend(asks=[("Is it about a drink?", ["h1"])])
    rnd = Round(
        _topic(topics, "physical_health"), llm=backend, yes_memory=mem, round_id="r1"
    )
    await rnd.open()
    await rnd.answer(Answer.YES)
    assert mem.needs()  # the confirmed need was remembered
    assert any("water" in n.lower() for n in mem.needs())


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


def test_reasoning_tuning_from_env(monkeypatch) -> None:
    # The behavior knobs are env-overridable, clamped to sane ranges, and fall back
    # to defaults when unset.
    monkeypatch.setenv("MY20Q_MIN_YES", "7")
    monkeypatch.setenv("MY20Q_REPHRASE_LIMIT", "5")
    monkeypatch.setenv("MY20Q_EXPLORE_DECAY", "1.8")  # above the range -> clamped
    t = ReasoningTuning.from_env()
    assert t.min_yes_for_synthesis == 7
    assert t.rephrase_limit == 5
    assert t.explore_decay == 1.0  # clamped into [0, 1]
    assert t.new_yes_for_resynthesis == 3  # untouched -> default


def test_explore_probability_decays_with_yeses(topics: list[Topic]) -> None:
    # Exploration probability = explore_decay ** (yeses + 1): high early, decaying
    # as yeses approach the synthesis threshold.
    rnd = Round(
        _topic(topics, "mental_health"),
        llm=MockBackend(),
        tuning=ReasoningTuning(explore_decay=2 / 3),
    )
    base = 2 / 3
    assert rnd._explore_probability(0) == base  # high at the start
    assert rnd._explore_probability(2) == base**3
    assert rnd._explore_probability(0) > rnd._explore_probability(4)  # decays


async def test_exploration_decays_as_yeses_accrue(topics: list[Topic]) -> None:
    # With the RNG pinned just under the initial rate, early questions explore;
    # once enough yeses accrue the decayed probability drops below the pin and
    # exploration stops. (min_yes is huge so the round never synthesizes.)
    seen: list[bool] = []

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "candidate NEEDS to test" in system:
            return json.dumps({"hypotheses": SEED_NEEDS})
        if "pin down the ONE specific" in system:
            seen.append("EXPLORE MODE" in messages[1]["content"])
            word = _DISTINCT_SUBJECTS[len(seen) % len(_DISTINCT_SUBJECTS)]
            return json.dumps(
                {"question": f"Is it about {word}?", "yes_ids": ["h1"],
                 "preface": "", "rationale": "x"}
            )
        return json.dumps({"utterance": "x"})

    rnd = Round(
        _topic(topics, "physical_health"),
        llm=MockBackend(responder=responder),
        tuning=ReasoningTuning(min_yes_for_synthesis=99, explore_decay=2 / 3),
        rng=_FixedRandom(0.4),  # pinned just under the initial 0.667 rate
    )
    await rnd.open()  # yeses=0 -> p=0.667 > 0.4 -> explore
    assert seen[0] is True
    await rnd.answer(Answer.YES)  # next at yeses=1 -> p=0.444 > 0.4 -> explore
    assert seen[1] is True
    await rnd.answer(Answer.YES)  # next at yeses=2 -> p=0.296 < 0.4 -> no explore
    assert seen[2] is False
