"""Tests for the round engine — the 5W1H facet controller and its recovery."""

from __future__ import annotations

import json
import random

import pytest

from my20q.agent import facets
from my20q.agent.dialogue import (
    MIN_YES_FOR_SYNTHESIS,
    SOFT_RESET_NO_STREAK,
    Answer,
    Round,
    Session,
)
from my20q.agent.prompts import deliberate_messages, seed_messages
from my20q.agent.reasoner import ReasonerAction, ReasonerError
from my20q.config import ReasoningTuning
from my20q.llm import MockBackend
from my20q.llm.base import LLMUnavailable
from my20q.recording.yes_memory import YesMemory
from my20q.topics import Topic, find_topic

#: Standard seed payload for the mock protocol (physical_health-ish).
SEED_SLOTS = {
    "who": ["my son"],
    "what": ["a drink", "a snack", "the blanket"],
    "when": [],
    "where": [],
    "why": ["thirsty"],
    "how": ["bring it", "move it"],
}

#: Distinct question subjects so the repeat gate never trips in long mock runs.
_SUBJECTS = [
    "a drink", "a snack", "the blanket", "your chair", "the lights", "the noise",
    "sleep", "sitting up", "warmth", "the window", "your glasses", "the radio",
    "music", "a walk", "the garden", "your book", "a phone call", "the photo",
]


class _FixedRandom(random.Random):
    """A deterministic RNG whose random() always returns a fixed value (tests)."""

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:
        """Return the pinned value instead of a real draw."""
        return self._value


def _topic(topics: list[Topic], topic_id: str) -> Topic:
    t = find_topic(topics, topic_id)
    assert t is not None
    return t


#: Distinct water-themed utterances so rephrases never trip the utterance
#: dup-check (every variant keeps "water" for the final-utterance asserts).
_UTTERANCES = [
    "I would like a glass of water.",
    "Could you bring me some water to drink?",
    "Please get me a cool glass of water now.",
    "Water would be lovely, may I have some?",
]


def _controller_backend(
    *,
    seed: dict[str, list[str]] | None = None,
    expand: dict[str, list[str]] | None = None,
    slot_cat: str = "what",
    flip: dict | Exception | None = None,
) -> MockBackend:
    """A MockBackend that plays the seed/deliberate/format/expand/synth protocol.

    Each formatted question uses a fresh subject (tagged into ``slot_cat``), so
    the repeat gate and the slot-anchoring gate both pass indefinitely; each
    synthesize call cycles a fresh phrasing. ``flip`` overrides the opposition
    button's one-shot reply (an Exception is raised instead of returned).
    """
    state = {"q": 0, "s": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:  # seed
            return json.dumps(seed or SEED_SLOTS)
        if "HIGH-TRUST" in system:  # expand (caregiver note)
            return json.dumps({"slots": expand or {}})
        if "OPPOSITE button" in system:  # the opposition button's one-shot
            if isinstance(flip, Exception):
                raise flip
            return json.dumps(
                flip
                or {"question": "Do you want someone to bring it to you?",
                    "slots": {"how": "bring it"}}
            )
        if "Convert a drafted question" in system:  # format
            word = _SUBJECTS[state["q"] % len(_SUBJECTS)]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {slot_cat: word},
                 "preface": "", "rationale": "drill"}
            )
        if "pin down the ONE specific" in system:  # deliberate
            return "thinking it through..."
        utterance = _UTTERANCES[state["s"] % len(_UTTERANCES)]  # synthesize
        state["s"] += 1
        return json.dumps({"utterance": utterance})

    return MockBackend(responder=responder)


# ----------------------------------------------------------- board replay


def test_no_is_soft_never_an_elimination(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink", "a snack"]}
    rnd._history = [
        {"kind": "query", "text": "Is it a drink?", "answer": "no",
         "slots": {"what": "a drink"}}
    ]
    board = rnd._replay_board()
    assert board["what"]["a drink"] == -1.0  # down-weighted, still known
    assert board["what"]["a snack"] == 0.0  # untouched — never promoted


def test_rejected_synthesis_does_not_touch_the_board(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink"]}
    rnd._history = [
        {"kind": "query", "text": "Is it a drink?", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "synthesis", "text": "guess", "answer": "no",
         "slots": {"what": "a drink"}},
    ]
    board = rnd._replay_board()
    assert board["what"]["a drink"] == 1.0  # the rejection subtracted nothing


def test_context_entry_boosts_the_board(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink"]}
    rnd._history = [
        {"kind": "context", "text": "pointing at the cup", "answer": None,
         "slots": {"what": ["a drink"], "where": ["the kitchen"]}}
    ]
    board = rnd._replay_board()
    assert board["what"]["a drink"] == 2.0  # high-trust boost
    assert board["where"]["the kitchen"] == 2.0  # minted + boosted


def test_restart_marker_replaces_the_board(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["old thing"]}
    rnd._history = [
        {"kind": "query", "text": "Is it the old thing?", "answer": "kinda",
         "slots": {"what": "old thing"}},
        {"kind": "restart", "reason": "no-streak",
         "board": {"what": [["a drink", 1.0], ["fresh idea", 0.0]]}},
    ]
    board = rnd._replay_board()
    assert "old thing" not in board["what"]  # dumped
    assert board["what"]["a drink"] == 1.0  # kept yes-signal carried over
    assert board["what"]["fresh idea"] == 0.0


def test_consec_no_streak_counts_tail(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._history = [
        {"kind": "query", "text": "q1", "answer": "yes", "slots": {"what": "x"}},
        {"kind": "query", "text": "q2", "answer": "no", "slots": {"what": "x"}},
        {"kind": "context", "text": "note", "answer": None},
        {"kind": "query", "text": "q3", "answer": "not_sure", "slots": {"what": "x"}},
        {"kind": "query", "text": "q4", "answer": "no", "slots": {"what": "x"}},
    ]
    # q4 (no) + q2 (no); the not_sure and context between them don't break the
    # run, but the earlier "yes" does.
    assert rnd._consec_no_streak() == 2


# ------------------------------------------------------------ focus policy


def _policy_round(topics: list[Topic]) -> Round:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())  # core: what+how
    rnd._seed_values = {"what": ["a drink", "a snack"], "how": ["bring it"],
                        "why": ["thirsty"]}
    return rnd


def test_focus_probes_unestablished_core_first(topics: list[Topic]) -> None:
    rnd = _policy_round(topics)
    focus, directive, pair = rnd._pick_focus(rnd._replay_board(), [])
    assert focus == "what" and directive == "probe" and pair is None


def test_focus_splits_tied_contenders(topics: list[Topic]) -> None:
    rnd = _policy_round(topics)
    rnd._history = [
        {"kind": "query", "text": "drink?", "answer": "yes", "slots": {"what": "a drink"}},
        {"kind": "query", "text": "snack?", "answer": "yes", "slots": {"what": "a snack"}},
        {"kind": "query", "text": "bring?", "answer": "yes", "slots": {"how": "bring it"}},
    ]
    focus, directive, pair = rnd._pick_focus(rnd._replay_board(), rnd._history)
    assert focus == "what" and directive == "split"
    assert set(pair) == {"a drink", "a snack"}


def test_focus_never_hammers_one_category(topics: list[Topic]) -> None:
    # The "why"-hammering guard: after MAX_CATEGORY_RUN consecutive focuses on
    # the same slot, the policy moves to an alternative even if that slot is
    # still the weakest.
    rnd = _policy_round(topics)
    seg = [
        {"kind": "query", "text": "q1", "answer": "no",
         "slots": {"what": "a drink"}, "focus": "what"},
        {"kind": "query", "text": "q2", "answer": "no",
         "slots": {"what": "a snack"}, "focus": "what"},
    ]
    rnd._history = list(seg)
    focus, directive, _ = rnd._pick_focus(rnd._replay_board(), seg)
    assert focus == "how"  # rotated away from the hammered "what"


def test_focus_enriches_modifiers_once_core_is_confident(topics: list[Topic]) -> None:
    rnd = _policy_round(topics)
    rnd._history = [
        {"kind": "query", "text": f"q{i}", "answer": "yes", "slots": slots}
        for i, slots in enumerate(
            [{"what": "a drink"}] * 3 + [{"how": "bring it"}] * 3
        )
    ]
    focus, directive, _ = rnd._pick_focus(rnd._replay_board(), [])
    assert focus in ("who", "when", "where", "why")
    assert directive == "probe"


# ------------------------------------- focus policy v3 (W1-B): retirement


def test_retired_requires_dominance_not_margin() -> None:
    # Ratio rule: who at +25.5/+10 (the 06-11 metronome) retires; the vague
    # what-leader at +6/+4 — two clean yeses ahead — must STAY drillable.
    rnd = Round(Topic(id="t", label="T"), llm=MockBackend())
    board = facets.empty_board()
    board["who"].update({"Rob": 25.5, "him": 10.0})
    board["what"].update({"keeping the house tidy": 6.0, "something": 4.0})
    board["when"].update({"later": 5.0, "today": 1.0})
    board["how"].update({"bring it": 8.0, "warm it": 4.5})
    assert rnd._retired(board, "who") is True  # dominant — settled
    assert rnd._retired(board, "what") is False  # margin 2, ratio weak
    assert rnd._retired(board, "when") is False  # leader below 3x ready
    assert rnd._retired(board, "how") is False  # runner above half
    assert rnd._retired(board, "where") is False  # empty slot never retires


def test_focus_skips_a_retired_slot(topics: list[Topic]) -> None:
    # what dominates (retired) even though it is the WEAKEST core leader —
    # the old policy would re-drill it; now the live core slot gets the turn.
    rnd = _policy_round(topics)  # physical_health, core what+how
    rnd._seed_values = {"what": ["a drink", "a snack"],
                        "how": ["bring it", "warm it"],
                        "who": ["my son"], "when": ["today"],
                        "where": ["my room"], "why": ["thirsty"]}
    rnd._history = (
        [{"kind": "query", "text": f"w{i}", "answer": "yes",
          "slots": {"what": "a drink"}} for i in range(6)]
        + [{"kind": "query", "text": "w6", "answer": "kinda",
            "slots": {"what": "a drink"}}]
        + [{"kind": "query", "text": f"h{i}", "answer": "yes",
            "slots": {"how": "bring it"}} for i in range(8)]
        # runner above half the leader keeps "how" un-retired (8 vs 4.5)
        + [{"kind": "query", "text": f"h8{i}", "answer": "yes",
            "slots": {"how": "warm it"}} for i in range(4)]
        + [{"kind": "query", "text": "h9", "answer": "kinda",
            "slots": {"how": "warm it"}}]
        # close the modifier slots so priority 3 does not fire
        + [{"kind": "context", "text": "ctx", "answer": None,
            "slots": {"who": ["my son"], "when": ["today"],
                      "where": ["my room"], "why": ["thirsty"]}}]
    )
    board = rnd._replay_board()
    # what: 6.5 vs 0 -> retired; how: 8 vs ... live runner keeps it in play
    assert rnd._retired(board, "what") is True
    assert rnd._retired(board, "how") is False
    focus, directive, _ = rnd._pick_focus(board, [])
    assert directive == "drill"
    assert focus == "how"  # the retired-but-weakest "what" is skipped


def test_focus_drills_established_modifiers_by_topic_priority(
    topics: list[Topic],
) -> None:
    # The 06-11 hole: with who/how settled, "what" (vague, established) must
    # be drillable for a people topic — and outrank when/where by the topic's
    # facet_priority, not by raw score.
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._seed_values = {"who": ["Rob", "him"], "how": ["tell them something"],
                        "what": ["keeping the house tidy", "a routine"],
                        "when": ["later"], "where": ["our home"],
                        "why": ["I need help"]}
    rnd._history = (
        [{"kind": "query", "text": f"r{i}", "answer": "yes",
          "slots": {"who": "Rob"}} for i in range(8)]
        + [{"kind": "query", "text": "r8", "answer": "yes",
            "slots": {"who": "him"}}]
        + [{"kind": "query", "text": f"t{i}", "answer": "yes",
            "slots": {"how": "tell them something"}} for i in range(9)]
        + [{"kind": "query", "text": f"x{i}", "answer": "yes",
            "slots": {"what": "keeping the house tidy"}} for i in range(6)]
        # the real 06-11 shape: 6 vs 4 — vague leader with a live rival,
        # NOT dominant, must stay drillable
        + [{"kind": "query", "text": f"x6{i}", "answer": "yes",
            "slots": {"what": "a routine"}} for i in range(4)]
        + [{"kind": "query", "text": f"m{i}", "answer": "yes", "slots": s}
           for i, s in enumerate([{"when": "later"}, {"when": "later"},
                                  {"when": "later"},
                                  {"where": "our home"}, {"where": "our home"},
                                  {"why": "I need help"}, {"why": "I need help"},
                                  {"why": "I need help"}])]
    )
    board = rnd._replay_board()
    assert rnd._retired(board, "who") is True  # 8 vs 1 — settled
    assert rnd._retired(board, "how") is True  # 9 vs 0 — settled
    focus, directive, _ = rnd._pick_focus(board, [])
    assert directive == "drill"
    # All live slots are established; my_people facet_priority ranks what
    # ahead of why/when/where regardless of raw scores (what +6 > when +3).
    assert focus == "what"


def test_focus_ladder_extends_while_working(topics: list[Topic]) -> None:
    # Two consecutive what-focused hits (yes/kinda) — the run may extend past
    # MAX_CATEGORY_RUN (the body→leg→foot→toe ladder); contrast with the
    # no/no rotation in test_focus_never_hammers_one_category.
    rnd = _policy_round(topics)
    seg = [
        {"kind": "query", "text": "q1", "answer": "yes",
         "slots": {"what": "a drink"}, "focus": "what"},
        {"kind": "query", "text": "q2", "answer": "kinda",
         "slots": {"what": "a drink"}, "focus": "what"},
        {"kind": "query", "text": "q3", "answer": "yes",
         "slots": {"how": "bring it"}},  # closes the how slot (no focus)
    ]
    # Make the tail query of the run the what-focused kinda: reorder so the
    # run is the tail.
    seg = [seg[2], seg[0], seg[1]]
    rnd._history = list(seg)
    board = rnd._replay_board()
    focus, directive, _ = rnd._pick_focus(board, seg)
    assert focus == "what"  # run of 2, but WORKING — allowed to extend
    assert directive == "drill"


def test_focus_retired_slot_reopens_on_a_tie(topics: list[Topic]) -> None:
    # Retirement never blocks a split: dominance and tie are mutually
    # exclusive, so scores re-converging reopen the slot by themselves.
    rnd = _policy_round(topics)
    rnd._seed_values = {"what": ["a drink", "a snack"], "how": ["bring it"]}
    rnd._history = (
        [{"kind": "query", "text": f"d{i}", "answer": "yes",
          "slots": {"what": "a drink"}} for i in range(8)]
        + [{"kind": "query", "text": f"s{i}", "answer": "yes",
            "slots": {"what": "a snack"}} for i in range(8)]
        + [{"kind": "query", "text": "h", "answer": "yes",
            "slots": {"how": "bring it"}}]
    )
    board = rnd._replay_board()
    assert rnd._retired(board, "what") is False  # runner caught up — reopened
    focus, directive, pair = rnd._pick_focus(board, [])
    assert focus == "what" and directive == "split"
    assert set(pair) == {"a drink", "a snack"}


def test_topic_facet_priority_loaded_and_validated(topics: list[Topic]) -> None:
    people = _topic(topics, "my_people")
    assert people.facet_priority[0] == "who"
    assert people.facet_priority[-1] == "where"  # implied in caregiving
    with pytest.raises(ValueError):
        Topic(id="x", label="X", facet_priority=["whom"])


# --------------------------------------------------- reasoning round flow


async def test_reasoning_round_converges_via_the_board(topics: list[Topic]) -> None:
    backend = _controller_backend()
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    ev = await rnd.open()
    assert ev.kind == "query" and ev.engine == "reasoning"
    # The honest tile gets the full six-category board.
    assert [f["category"] for f in ev.facets] == [
        "who", "what", "when", "where", "why", "how"
    ]
    assert any(f["focus"] for f in ev.facets)

    yeses = 0
    while ev.kind == "query" and yeses < 12:
        ev = await rnd.answer(Answer.YES)
        yeses += 1
    assert ev.kind == "synthesis"
    # Converged via consensus-readiness or the yes-gate — never on the 1st yes.
    assert 2 <= yeses <= MIN_YES_FOR_SYNTHESIS

    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesized"
    assert "water" in rnd.final_utterance
    # The query entries carry their slots/focus for replay and the dataset.
    q1 = rnd.history[0]
    assert q1["slots"] and q1["focus"]


async def test_undo_recomputes_the_board(topics: list[Topic]) -> None:
    backend = _controller_backend()
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    assert len(rnd.history) == 1
    ev = await rnd.undo()
    assert len(rnd.history) == 0
    assert ev.kind == "query"
    # All scores back to zero after the undo.
    assert all(c["score"] == 0 for f in ev.facets for c in f["contenders"])


async def test_safety_ceiling_stops_without_forcing_synthesis(topics: list[Topic]) -> None:
    backend = _controller_backend()
    rnd = Round(_topic(topics, "general"), llm=backend, max_queries=1,
                rng=_FixedRandom(0.99))
    ev = await rnd.open()
    assert ev.kind == "query"
    ev = await rnd.answer(Answer.NO)  # 1 query asked == ceiling -> stop
    assert ev.kind == "abandoned"
    assert rnd.outcome == "abandoned"


async def test_unlimited_budget_keeps_questioning(topics: list[Topic]) -> None:
    backend = _controller_backend()
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    assert rnd.max_queries == 0
    await rnd.open()
    ev = await rnd.answer(Answer.NO)
    assert rnd.outcome is None  # not abandoned by count
    assert ev.kind in ("query", "synthesis")


async def test_rejected_synthesis_keeps_going(topics: list[Topic]) -> None:
    backend = _controller_backend()
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    ev = await rnd.open()
    yeses = 0
    while ev.kind == "query" and yeses < 12:
        ev = await rnd.answer(Answer.YES)
        yeses += 1
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # rejects the proposal — round continues
    assert rnd.outcome is None
    assert ev.kind in ("query", "synthesis")


async def test_round_never_ends_until_yes_rephrases_then_restarts(
    topics: list[Topic],
) -> None:
    # The full synthesis loop with small thresholds: question -> synthesize ->
    # rephrase -> requestion -> synthesize -> after 2 failed attempts, RESTART.
    # The round NEVER ends until a "yes" to an utterance.
    tuning = ReasoningTuning(
        min_yes_for_synthesis=2,
        new_yes_for_resynthesis=1,
        rephrase_limit=1,
        synth_attempts_before_restart=2,
    )
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                tuning=tuning, rng=_FixedRandom(0.99))
    await rnd.open()
    ev = await rnd.answer(Answer.YES)  # 1 yes -> still questioning
    assert ev.kind == "query"
    ev = await rnd.answer(Answer.YES)  # 2 yeses -> synthesis attempt #1
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # reject -> rephrase
    assert ev.kind == "synthesis" and rnd.outcome is None
    ev = await rnd.answer(Answer.NO)  # reject again -> attempt #1 done -> question
    assert ev.kind == "query" and rnd.outcome is None
    ev = await rnd.answer(Answer.YES)  # 1 NEW yes -> synthesis attempt #2
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # reject -> rephrase
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # reject -> attempt #2 done -> RESTART
    assert ev.kind == "query" and rnd.outcome is None
    assert any(h["kind"] == "restart" for h in rnd.history)  # context dumped
    ev = await rnd.answer(Answer.YES)  # 1 yes post-restart -> synthesis
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.YES)  # YES to the utterance -> ends
    assert ev.kind == "synthesized" and rnd.is_terminal


async def test_restart_keeps_yes_signal_and_dumps_no(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    rnd._history = [
        {"kind": "query", "text": "Is it a drink?", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "query", "text": "Is it a snack?", "answer": "no",
         "slots": {"what": "a snack"}},
        {"kind": "query", "text": "Is it the blanket?", "answer": "kinda",
         "slots": {"what": "the blanket"}},
    ]
    await rnd._restart("test")
    board = rnd._replay_board()
    assert board["what"]["a drink"] == 1.0  # round-specific yes kept
    assert board["what"].get("a snack", 0.0) == 0.0  # the "no" influence dumped
    assert board["what"].get("the blanket", 0.0) == 0.0  # "kinda" dumped too


async def test_no_streak_triggers_restart(topics: list[Topic]) -> None:
    # stall_window=0 isolates the streak trigger (the stall trigger would
    # otherwise fire first on a pure-no run — see the stall test below).
    tuning = ReasoningTuning(stall_window=0)
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                tuning=tuning, rng=_FixedRandom(0.99))
    await rnd.open()
    for _ in range(SOFT_RESET_NO_STREAK):  # exactly the threshold — no restart
        await rnd.answer(Answer.NO)
    assert not any(h["kind"] == "restart" for h in rnd.history)
    await rnd.answer(Answer.NO)  # one MORE -> the restart recovery fires
    assert any(h["kind"] == "restart" for h in rnd.history)
    assert rnd.engine == "reasoning"  # stayed in reasoning throughout


async def test_stalled_progress_triggers_restart(topics: list[Topic]) -> None:
    # Sparse kindas break the no-streak but NOT the stall trigger: 8 answered
    # queries with no pair reaching a confirmed score is a dead-end round.
    tuning = ReasoningTuning(stall_window=8, soft_reset_no_streak=99)
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                tuning=tuning, rng=_FixedRandom(0.99))
    await rnd.open()
    pattern = [Answer.NO, Answer.NO, Answer.NO, Answer.KINDA] * 2
    for a in pattern[:-1]:
        await rnd.answer(a)
    assert not any(h["kind"] == "restart" for h in rnd.history)
    await rnd.answer(pattern[-1])  # 8th answered query, zero progress -> restart
    assert any(h["kind"] == "restart" for h in rnd.history)


async def test_round_records_yes_into_memory(topics: list[Topic]) -> None:
    mem = YesMemory()
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                yes_memory=mem, round_id="r1", rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    assert mem.needs()  # the confirmed slot value was logged
    assert mem.items[0]["round_id"] == "r1"


async def test_add_context_steers_and_boosts(topics: list[Topic]) -> None:
    backend = _controller_backend(expand={"what": ["a drink"]})
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    first = await rnd.open()
    ev = await rnd.add_context("She keeps pointing at the empty cup.")
    assert ev.kind == "query"
    assert "context" in [h["kind"] for h in rnd.history]
    assert ev.text != first.text
    what = next(f for f in ev.facets if f["category"] == "what")
    assert what["contenders"][0] == {"value": "a drink", "score": 2.0}


async def test_undo_removes_context_boost(topics: list[Topic]) -> None:
    backend = _controller_backend(expand={"where": ["the kitchen"]})
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    await rnd.open()
    ev = await rnd.add_context("pointing toward the kitchen")
    where = next(f for f in ev.facets if f["category"] == "where")
    assert where["contenders"]
    ev = await rnd.undo()  # pops the context entry -> the boost disappears
    where = next(f for f in ev.facets if f["category"] == "where")
    assert not where["contenders"]


# ------------------------------------------ failure -> recovery -> diagnostic


async def test_no_llm_round_surfaces_a_diagnostic(topics: list[Topic]) -> None:
    # No canned questions: without an LLM the round says so, honestly.
    rnd = Round(_topic(topics, "physical_health"), llm=None)
    ev = await rnd.open()
    assert ev.kind == "diagnostic" and ev.engine == "fallback"
    assert ev.diagnostic is not None
    assert not rnd.is_terminal  # the round is alive; retry is possible


async def test_emergency_topic_short_circuits(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "emergency"), llm=None)
    ev = await rnd.open()
    assert ev.kind == "emergency"
    assert ev.emergency_screen is not None
    assert rnd.outcome == "emergency"


async def test_unreachable_llm_yields_diagnostic_then_recovers(
    topics: list[Topic],
) -> None:
    # Transient outage: the seed call blips -> diagnostic (no canned question),
    # the caregiver retries -> reasoning resumes.
    state = {"calls": 0}
    inner = _controller_backend()

    def responder(messages: list) -> str:
        state["calls"] += 1
        if state["calls"] == 1:  # first call (the seed) blips out
            raise LLMUnavailable("cold load timeout")
        return inner.responder(messages)

    rnd = Round(_topic(topics, "physical_health"),
                llm=MockBackend(responder=responder), rng=_FixedRandom(0.99))
    ev = await rnd.open()
    assert ev.kind == "diagnostic"
    assert ev.diagnostic["llm_unreachable"] is True
    assert ev.diagnostic["restart_attempted"] is False  # pointless when down
    ev = await rnd.retry()
    assert ev.kind == "query" and ev.engine == "reasoning"  # recovered


async def test_fail_loop_triggers_restart_recovery(topics: list[Topic]) -> None:
    # The model gets stuck repeating one question. The repeat gate exhausts the
    # ask -> the engine dumps the no/kinda context (restart) and re-asks; the
    # recovered question carries the restart note in its rationale. The mock
    # un-sticks only once the DELIBERATE prompt shows the restart marker (the
    # dumped-context history), mirroring a model freed by the context dump.
    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "pin down the ONE specific" in system:  # deliberate (sees history)
            return (
                "UNSTUCK" if "[restart" in messages[1]["content"] else "still stuck"
            )
        if "Convert a drafted question" in system:  # format (sees the draft)
            if "UNSTUCK" in messages[1]["content"]:
                return json.dumps(
                    {"question": "Is it about the window?",
                     "slots": {"what": "the window"}, "preface": "",
                     "rationale": "fresh"}
                )
            return json.dumps(
                {"question": "Is it about a drink?", "slots": {"what": "a drink"},
                 "preface": "", "rationale": "stuck"}
            )
        return json.dumps({"utterance": "x"})

    rnd = Round(_topic(topics, "physical_health"),
                llm=MockBackend(responder=responder), rng=_FixedRandom(0.99))
    ev = await rnd.open()
    assert ev.text == "Is it about a drink?"
    ev = await rnd.answer(Answer.NO)
    # The stuck model repeated itself -> restart recovery -> fresh question.
    assert ev.kind == "query"
    assert ev.text == "Is it about the window?"
    assert "(recovered after a context restart)" in ev.rationale
    assert any(h["kind"] == "restart" for h in rnd.history)


async def test_persistent_failure_surfaces_diagnostic_and_retry_works(
    topics: list[Topic],
) -> None:
    # Even the restart recovery fails -> a diagnostic event with the reason;
    # the round is NOT terminal and answer() without a pending action raises.
    state = {"stuck": True}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "Convert a drafted question" in system:
            if state["stuck"]:
                return json.dumps(
                    {"question": "Is it about a drink?",
                     "slots": {"what": "a drink"}, "preface": "", "rationale": "x"}
                )
            return json.dumps(
                {"question": "Is it about the radio?",
                 "slots": {"what": "the radio"}, "preface": "", "rationale": "x"}
            )
        if "pin down the ONE specific" in system:
            return "hmm"
        return json.dumps({"utterance": "x"})

    rnd = Round(_topic(topics, "physical_health"),
                llm=MockBackend(responder=responder), rng=_FixedRandom(0.99))
    await rnd.open()
    ev = await rnd.answer(Answer.NO)  # stuck through restart too -> diagnostic
    assert ev.kind == "diagnostic"
    assert ev.diagnostic["restart_attempted"] is True
    assert "diagnostic" in [h["kind"] for h in rnd.history]
    assert not rnd.is_terminal
    # No pending action while diagnosed — answering is a state error.
    try:
        await rnd.answer(Answer.YES)
        raise AssertionError("answer() must fail with no pending action")
    except RuntimeError:
        pass
    state["stuck"] = False  # the model comes back
    ev = await rnd.retry()
    assert ev.kind == "query" and ev.text == "Is it about the radio?"


# ---------------------------------------- anti-farming, kinda-loop, pinning


async def test_farming_yeses_do_not_advance_the_synthesis_gate(
    topics: list[Topic],
) -> None:
    # Re-confirming an established pair earns points but NOT gate progress —
    # one trial satisfied the resynthesis gate with zero-information yeses and
    # looped 16 near-identical utterances.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    rnd._history = [
        {"kind": "query", "text": f"q{i}", "answer": "yes",
         "slots": {"what": "a drink"}, "informative": i == 0}
        for i in range(4)
    ]
    # 4 yeses on the board, but only the first carried information.
    assert rnd._yes_since_last_synth(rnd._segment()) == 1


async def test_kinda_rejection_allows_only_one_rephrase(topics: list[Topic]) -> None:
    # kinda = a content gap; wording shuffles can't fill it. One rephrase,
    # then back to questioning (a "no" keeps the full rephrase budget).
    tuning = ReasoningTuning(min_yes_for_synthesis=1, new_yes_for_resynthesis=1,
                             rephrase_limit=3, stall_window=0)
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                tuning=tuning, rng=_FixedRandom(0.99))
    await rnd.open()
    ev = await rnd.answer(Answer.YES)  # 1 informative yes -> synthesis
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.KINDA)  # close -> ONE rephrase allowed
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.KINDA)  # still kinda -> back to questioning
    assert ev.kind == "query"


async def test_pin_focus_targets_weakest_slot_after_rejection(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink"], "how": ["bring it"]}
    rnd._history = [
        {"kind": "query", "text": "drink?", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "query", "text": "more drink?", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "query", "text": "bring?", "answer": "kinda",
         "slots": {"how": "bring it"}},
        {"kind": "synthesis", "text": "Bring me a drink.", "answer": "kinda",
         "slots": {"what": "a drink", "how": "bring it"}},
    ]
    focus, directive, _ = rnd._pick_focus(rnd._replay_board(), rnd._history)
    # what is confident (2.0); how (0.5) is the utterance's weak detail.
    assert (focus, directive) == ("how", "pin")


async def test_duplicate_rephrase_bails_to_questioning(topics: list[Topic]) -> None:
    # A rephrase that near-dups a rejected utterance would earn the same
    # rejection — the round re-questions instead of re-proposing it.
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "Convert a drafted question" in system:
            word = _SUBJECTS[state["q"] % len(_SUBJECTS)]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {"what": word},
                 "preface": "", "rationale": "x"}
            )
        if "pin down the ONE specific" in system:
            return "thinking"
        return json.dumps({"utterance": "I would like a glass of water."})

    tuning = ReasoningTuning(min_yes_for_synthesis=1, new_yes_for_resynthesis=1,
                             rephrase_limit=3, stall_window=0)
    rnd = Round(_topic(topics, "physical_health"),
                llm=MockBackend(responder=responder), tuning=tuning,
                rng=_FixedRandom(0.99))
    await rnd.open()
    ev = await rnd.answer(Answer.YES)
    assert ev.kind == "synthesis"
    ev = await rnd.answer(Answer.NO)  # rephrase would repeat verbatim -> question
    assert ev.kind == "query"


# ------------------------------------------------------- the direction layer


def _people_backend() -> MockBackend:
    """my_people mock whose questions are direction-classifiable."""
    questions = [
        ("Do you want to bring Rob a drink?", {"who": "Rob", "what": "a drink"}),
        ("Do you want to tell Rob some news?", {"who": "Rob", "what": "news"}),
        ("Do you want Rob to clean the kitchen?",
         {"who": "Rob", "how": "clean the kitchen"}),
        ("Do you want Rob to bring you a blanket?",
         {"who": "Rob", "how": "bring a blanket"}),
        ("Do you want to visit Rob soon?", {"who": "Rob", "when": "soon"}),
        ("Do you want Rob to fix the radio?", {"who": "Rob", "how": "fix the radio"}),
    ]
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(
                {"who": ["Rob", "Julie"], "what": ["a visit", "a phone call"],
                 "how": ["call them"], "why": ["missing them"]}
            )
        if "OPPOSITE button" in system:  # mirror of the first question
            return json.dumps(
                {"question": "Do you want Rob to bring you a drink?",
                 "slots": {"who": "Rob"}}
            )
        if "Convert a drafted question" in system:
            q, slots = questions[state["q"] % len(questions)]
            state["q"] += 1
            return json.dumps(
                {"question": q, "slots": slots, "preface": "", "rationale": "x"}
            )
        if "pin down the ONE specific" in system:
            return "thinking"
        return json.dumps({"utterance": "I need Rob to clean the kitchen."})

    return MockBackend(responder=responder)


async def test_direction_buckets_are_seeded_for_people_topics(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=_people_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    board = rnd._replay_board()
    how_values = {v.casefold() for v in board["how"]}
    for bucket in facets.DIRECTION_BUCKETS.values():
        assert bucket.casefold() in how_values


async def test_direction_is_classified_and_recorded(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=_people_backend(),
                rng=_FixedRandom(0.99))
    ev = await rnd.open()
    assert ev.text == "Do you want to bring Rob a drink?"
    await rnd.answer(Answer.NO)
    assert rnd.history[0]["direction"] == "me_for_them"


async def test_no_on_one_direction_nudges_the_mirror(topics: list[Topic]) -> None:
    # The sign flip: with the who-anchor positive, a "no" on a me-for-them
    # question is soft evidence FOR them-for-me.
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._seed_values = rnd._inject_direction_buckets({"who": ["Rob"]})
    rnd._history = [
        {"kind": "query", "text": "Is it about Rob?", "answer": "yes",
         "slots": {"who": "Rob"}},
        {"kind": "query", "text": "Do you want to bring Rob a drink?",
         "answer": "no", "slots": {"who": "Rob", "what": "a drink"},
         "direction": "me_for_them"},
    ]
    board = rnd._replay_board()
    assert board["who"]["Rob"] == 1.0  # protected by asymmetric crediting
    assert board["what"]["a drink"] == -1.0  # the guess took the hit
    mirror = facets.DIRECTION_BUCKETS["them_for_me"]
    assert board["how"][mirror] == 0.5  # the flip nudge


async def test_flip_needs_a_positive_who_anchor(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._seed_values = rnd._inject_direction_buckets({"who": ["Rob"]})
    rnd._history = [
        {"kind": "query", "text": "Do you want to bring Rob a drink?",
         "answer": "no", "slots": {"who": "Rob", "what": "a drink"},
         "direction": "me_for_them"},
    ]
    board = rnd._replay_board()
    mirror = facets.DIRECTION_BUCKETS["them_for_me"]
    assert board["how"][mirror] == 0.0  # who unconfirmed -> no flip license


async def test_yes_credits_the_asserted_direction_bucket(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._seed_values = rnd._inject_direction_buckets({"who": ["Rob"]})
    rnd._history = [
        {"kind": "query", "text": "Do you want Rob to clean the kitchen?",
         "answer": "yes", "slots": {"who": "Rob", "how": "clean the kitchen"},
         "direction": "them_for_me"},
    ]
    board = rnd._replay_board()
    bucket = facets.DIRECTION_BUCKETS["them_for_me"]
    assert board["how"][bucket] == 1.0


async def test_caregiver_hint_fires_until_direction_has_evidence(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                caregivers=["Rob"], rng=_FixedRandom(0.99))
    rnd._seed_values = rnd._inject_direction_buckets({"who": ["Rob", "Julie"]})
    rnd._history = [
        {"kind": "query", "text": "Is it about Rob?", "answer": "yes",
         "slots": {"who": "Rob"}},
    ]
    board = rnd._replay_board()
    assert rnd._caregiver_hint(board) == "Rob"  # who-leader is the caregiver
    # Once any direction bucket has positive evidence, the prior is spent.
    rnd._history.append(
        {"kind": "query", "text": "Do you want Rob to help you?", "answer": "kinda",
         "slots": {"who": "Rob"}, "direction": "them_for_me"}
    )
    assert rnd._caregiver_hint(rnd._replay_board()) == ""


async def test_caregiver_hint_needs_a_caregiver_match(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                caregivers=["Rob"], rng=_FixedRandom(0.99))
    rnd._seed_values = rnd._inject_direction_buckets({"who": ["Julie"]})
    rnd._history = [
        {"kind": "query", "text": "Is it about Julie?", "answer": "yes",
         "slots": {"who": "Julie"}},
    ]
    assert rnd._caregiver_hint(rnd._replay_board()) == ""  # Julie isn't one


# ------------------------------------------ the opposition button (the flip)


async def test_opposition_replaces_pending_as_action_not_answer(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    first = await rnd.open()
    ev = await rnd.flip()
    assert ev.kind == "query"
    assert ev.text == "Do you want someone to bring it to you?"
    assert ev.flipped_from == first.text
    assert ev.query_index == first.query_index  # the same turn, re-rendered
    assert rnd.query_count == 0  # nothing was answered
    assert rnd.history == []  # an action, not an answer — no entry
    assert rnd._superseded == [first.text]


async def test_opposition_records_origin_when_answered(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    first = await rnd.open()
    await rnd.flip()
    await rnd.answer(Answer.YES)
    entry = rnd.history[0]
    assert entry["kind"] == "query"
    assert entry["text"] == "Do you want someone to bring it to you?"
    assert entry["flipped_from"] == first.text  # the dataset sees the flip


async def test_opposition_keeps_the_original_in_the_repeat_gate(
    topics: list[Topic],
) -> None:
    backend = _controller_backend()
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    first = await rnd.open()
    await rnd.flip()
    await rnd.answer(Answer.NO)  # answer the flipped question → next ask
    deliberates = [
        str(c) for c in backend.calls if "pin down the ONE specific" in str(c[0])
    ]
    # The superseded original was rendered (often spoken) — it still counts
    # as asked, so the model is told not to re-ask it.
    assert first.text in deliberates[-1]


async def test_opposition_requires_a_pending_query(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    with pytest.raises(RuntimeError):
        await rnd.flip()  # before open()
    await rnd.open()
    rnd._pending = ReasonerAction(kind="synthesis", content="I would like water.")
    with pytest.raises(RuntimeError):
        await rnd.flip()  # a proposal is confirmed or rejected, never flipped

    bare = Round(_topic(topics, "physical_health"))  # no LLM → diagnostic
    await bare.open()
    with pytest.raises(RuntimeError):
        await bare.flip()  # nothing pending to flip


async def test_opposition_failure_leaves_the_pending_question_intact(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"),
                llm=_controller_backend(flip=LLMUnavailable("backend down")),
                rng=_FixedRandom(0.99))
    first = await rnd.open()
    with pytest.raises(ReasonerError):
        await rnd.flip()
    # Soft failure: the on-screen question is untouched and still answerable.
    assert rnd._pending is not None and rnd._pending.content == first.text
    assert rnd._superseded == []
    ev = await rnd.answer(Answer.NO)
    assert ev.kind == "query"


async def test_opposition_reverses_the_classified_direction(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=_people_backend(),
                rng=_FixedRandom(0.99))
    first = await rnd.open()
    assert first.text == "Do you want to bring Rob a drink?"
    assert rnd._pending is not None and rnd._pending.direction == "me_for_them"
    ev = await rnd.flip()
    assert ev.flipped_from == first.text
    # The mirrored question re-classifies by code — answers now credit the
    # opposite intent bucket.
    assert rnd._pending is not None and rnd._pending.direction == "them_for_me"


def test_futile_pair_bans_the_drilled_value(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend())
    seg = [
        {"kind": "query", "text": f"q{i}", "answer": "no",
         "slots": {"who": "Rob", "how": "remind"}}
        for i in range(4)
    ]
    assert rnd._futile_pair(seg) == ("how", "remind")  # never the who-anchor


def test_futile_direction_bans_the_bucket(topics: list[Topic]) -> None:
    # Content guesses vary but the direction doesn't — ban the whole bucket.
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend())
    seg = [
        {"kind": "query", "text": f"q{i}", "answer": "no",
         "slots": {"who": "Rob", "what": f"thing {i}"},
         "direction": "tell_them"}
        for i in range(4)
    ]
    assert rnd._futile_pair(seg) == (
        "how", facets.DIRECTION_BUCKETS["tell_them"]
    )


async def test_board_record_carries_seeds_and_final(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    record = rnd.board_record()
    assert record["seeds"] == rnd._seed_values
    assert any(
        score > 0 for pairs in record["final"].values() for _v, score in pairs
    )


# ------------------------------------------------------------ session, misc


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


def test_deliberate_messages_carry_focus_and_directive() -> None:
    from my20q.agent import facets as f

    board = f.seed_board({"what": ["a drink", "a snack"]})
    content = deliberate_messages(
        "My body",
        board,
        [],
        focus="what",
        directive="split",
        split_pair=("a drink", "a snack"),
        asked=["Is it about food?"],
    )[1]["content"]
    assert "FOCUS SLOT: what" in content
    assert "SPLIT the tie" in content
    assert '"a drink" vs "a snack"' in content
    assert "ALREADY ASKED" in content and "Is it about food?" in content


def test_history_formatting_dumps_noise_after_restart() -> None:
    from my20q.agent.prompts import _format_history

    history = [
        {"kind": "query", "text": "Old yes?", "answer": "yes"},
        {"kind": "query", "text": "Old no?", "answer": "no"},
        {"kind": "query", "text": "Old kinda?", "answer": "kinda"},
        {"kind": "context", "text": "caregiver note", "answer": None},
        {"kind": "restart", "reason": "no-streak", "board": {}},
        {"kind": "query", "text": "Fresh question?", "answer": "no"},
    ]
    out = _format_history(history)
    assert "Old yes?" in out  # confirmed signal survives the dump
    assert "caregiver note" in out  # caregiver context survives the dump
    assert "Old no?" not in out and "Old kinda?" not in out  # noise dumped
    assert "restart" in out and "Fresh question?" in out


def test_reasoning_tuning_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MY20Q_MIN_YES", "7")
    monkeypatch.setenv("MY20Q_REPHRASE_LIMIT", "5")
    monkeypatch.setenv("MY20Q_EXPLORE_DECAY", "1.8")  # above the range -> clamped
    monkeypatch.setenv("MY20Q_SPLIT_MARGIN", "0.5")
    t = ReasoningTuning.from_env()
    assert t.min_yes_for_synthesis == 7
    assert t.rephrase_limit == 5
    assert t.explore_decay == 1.0  # clamped into [0, 1]
    assert t.facet_split_margin == 0.5
    assert t.new_yes_for_resynthesis == 3  # untouched -> default


def test_explore_probability_decays_with_yeses(topics: list[Topic]) -> None:
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
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "pin down the ONE specific" in system:
            seen.append("EXPLORE" in messages[1]["content"])
            return "thinking"
        if "Convert a drafted question" in system:
            word = _SUBJECTS[state["q"] % len(_SUBJECTS)]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {"what": word},
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
