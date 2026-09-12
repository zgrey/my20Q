"""Tests for the round engine — the 5W1H facet controller and its recovery."""

from __future__ import annotations

import json
import random
import re

import pytest

from my20q.agent import facets
from my20q.agent.dialogue import (
    MAX_CLARIFY_QUERIES,
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
        if "DOUBLE-CHECK" in system:  # verify-on-lock one-shot
            m = re.search(r':\s+"(.+)"', messages[1]["content"])
            value = m.group(1) if m else "that"
            return json.dumps(
                {"question": f"Can you confirm it is {value} you mean?"}
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
    # The catch-all topic keeps core [what, how] — these tests exercise the
    # generic policy mechanics, not a topic's facet layout.
    rnd = Round(_topic(topics, "general"), llm=MockBackend())
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
    body = _topic(topics, "physical_health")
    assert body.core_facets == ["what", "where"]  # sensation + location
    assert body.facet_priority[:2] == ["what", "where"]
    feelings = _topic(topics, "mental_health")
    assert feelings.facet_priority[:3] == ["what", "why", "who"]
    with pytest.raises(ValueError):
        Topic(id="x", label="X", facet_priority=["whom"])


def test_physical_where_is_core_and_probed_early(topics: list[Topic]) -> None:
    # 06-11 trial B1: location starved as a modifier (probed once in 42 q).
    # where is core for the body topic — probed the moment what has signal.
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._seed_values = {"what": ["pain", "thirst"], "where": ["legs", "back"],
                        "how": ["adjust position"]}
    rnd._history = [
        {"kind": "query", "text": "Are you feeling pain?", "answer": "yes",
         "slots": {"what": "pain"}},
    ]
    focus, directive, _ = rnd._pick_focus(rnd._replay_board(), rnd._history)
    assert (focus, directive) == ("where", "probe")


# --------------------------------------------------- reasoning round flow


async def test_reasoning_round_converges_via_the_board(topics: list[Topic]) -> None:
    # The full banner stack: pending → draft → verify-on-lock → board-ready
    # LLM weave → caregiver accept. The engine never proposes on its own.
    backend = _controller_backend(
        expand={"what": ["a drink"], "where": ["my throat"]}
    )
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    ev = await rnd.open()
    assert ev.kind == "query" and ev.engine == "reasoning"
    # The honest tile gets the full six-category board.
    assert [f["category"] for f in ev.facets] == [
        "who", "what", "when", "where", "why", "how"
    ]
    assert any(f["focus"] for f in ev.facets)
    assert rnd.banner()["state"] == "pending"  # seeds carry no signal yet

    ev = await rnd.answer(Answer.YES)  # "Is it about a drink?" → +1
    assert rnd.banner()["state"] == "draft"  # populated EARLY, pre-readiness
    assert not rnd.banner()["ready"]

    # A caregiver note locks both core slots → two verify-on-lock turns.
    # Both values must be IN THE NOTE'S WORDS: since W2-M a note credits only
    # what it actually says, so "her cup" no longer stands in for "a drink".
    ev = await rnd.add_context("she rubbed her throat and wants a drink")
    assert rnd._pending is not None and rnd._pending.verify
    ev = await rnd.answer(Answer.YES)  # confirm what='a drink'
    assert rnd._pending is not None and rnd._pending.verify
    ev = await rnd.answer(Answer.YES)  # confirm where='my throat'
    assert ev.kind == "query"  # still questioning — no auto-proposal

    banner = rnd.banner()
    assert banner["ready"] and banner["state"] == "draft"
    # Board-ready (body core = what + where) → the LLM weave replaced the
    # code template.
    assert "water" in banner["text"]
    assert {p["category"] for p in banner["parts"]} >= {"what", "where"}
    assert all(p["band"] == "locked" for p in banner["parts"])

    ev = rnd.accept()
    assert ev.kind == "synthesized"
    assert rnd.outcome == "synthesized"
    assert "water" in rnd.final_utterance
    # The accept is recorded like a confirmed synthesis (one dataset shape).
    last = rnd.history[-1]
    assert last["kind"] == "synthesis" and last["answer"] == "yes"
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


async def test_engine_never_proposes_only_accept_concludes(
    topics: list[Topic],
) -> None:
    # Auto-synthesis is gone (the living banner replaced it): any number of
    # yeses still yields queries; accept() is the only success terminator.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                tuning=ReasoningTuning(stall_window=0), rng=_FixedRandom(0.99))
    ev = await rnd.open()
    with pytest.raises(RuntimeError):
        rnd.accept()  # nothing positive on the board — nothing to accept
    for _ in range(8):
        assert ev.kind == "query"
        ev = await rnd.answer(Answer.YES)
    assert ev.kind == "query" and rnd.outcome is None
    ev = rnd.accept()
    assert ev.kind == "synthesized" and rnd.is_terminal
    assert rnd.final_utterance.startswith("I need")  # alternates collapsed
    assert "…" not in rnd.final_utterance


async def test_banner_pending_until_a_core_slot_has_signal(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    assert rnd.banner()["state"] == "pending"  # before open
    await rnd.open()
    assert rnd.banner()["state"] == "pending"  # seeds carry no signal
    await rnd.answer(Answer.NO)  # negative signal only — still pending
    assert rnd.banner()["state"] == "pending"
    await rnd.answer(Answer.YES)
    banner = rnd.banner()
    assert banner["state"] == "draft" and not banner["ready"]
    assert banner["parts"] and banner["parts"][0]["band"] == "working"
    assert banner["text"].endswith("…")  # still working — the ellipsis says so


async def test_edit_ban_strikes_a_value_and_keeps_going(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # "Is it about a drink?" → in the weave
    assert any(p["value"] == "a drink" for p in rnd.banner()["parts"])
    ev = await rnd.edit("no, not a drink")
    assert ev.kind == "query"  # the round keeps going against the edit
    banner = rnd.banner()
    assert {"category": "what", "value": "a drink"} in banner["banned"]
    assert all(p["value"] != "a drink" for p in banner["parts"])
    # Floored on the board — out of play, never re-minted, gated from asks.
    board = rnd._replay_board()
    assert board["what"]["a drink"] == facets.ELIMINATE_FLOOR
    assert ("what", "a drink") not in rnd._weave(board).items()


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
    ev = await rnd.add_context("She keeps pointing at the empty cup — she wants a drink.")
    assert ev.kind == "query"
    assert "context" in [h["kind"] for h in rnd.history]
    assert ev.text != first.text
    what = next(f for f in ev.facets if f["category"] == "what")
    assert what["contenders"][0] == {
        "value": "a drink", "score": 2.0, "parent": None,
    }


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
    # The REASONING failed; the belief did not. A diagnostic carries the board,
    # because a failure is exactly when the caregiver wants to see what the
    # round is holding — and because the cockpit's reasoning tile (and the
    # board's hide/reveal control with it) emptied out without this. Found in
    # the 09-12 trial, where diagnosing the stall needed the JSONL export
    # precisely because the screen had nothing on it.
    assert ev.facets, "a diagnostic must still report the board"
    assert {f["category"] for f in ev.facets} >= {"who", "what", "where"}
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


async def test_a_farming_yes_is_stamped_as_uninformative(
    topics: list[Topic],
) -> None:
    """Re-confirming an established pair earns points but carries no information.

    The flag used to gate synthesis; the banner removed those gates, so it is
    now INSTRUMENTATION — the autopsy dump and the bench read it to measure
    confirmation farming, and nothing in the engine branches on it.
    """
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # first yes on this pair: informative
    assert rnd.history[0]["informative"] is True

    # Force a second yes on the SAME, now-established pair.
    rnd._history.append(
        {"kind": "query", "text": "again?", "answer": "yes",
         "slots": {"what": rnd.history[0]["slots"]["what"]}}
    )
    board = rnd._replay_board()
    pair = rnd.history[0]["slots"]["what"]
    assert board["what"][pair] >= rnd.tuning.facet_ready_points


async def test_banner_template_shows_ambiguous_alternates(
    topics: list[Topic],
) -> None:
    # The owner's example, verbatim: who converges quickly to Rob and the
    # draft reads "I need/want something for/from Rob …" — the slashed
    # alternates are the undecided dimensions, the ellipsis says "working".
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._opened = True
    rnd._seed_values = rnd._inject_direction_buckets(
        {"who": ["Rob"], "what": ["a chore", "a meal"]}
    )
    rnd._history = [
        {"kind": "query", "text": "Is this about Rob?", "answer": "yes",
         "slots": {"who": "Rob"}},
    ]
    banner = rnd.banner()
    assert banner["state"] == "draft" and not banner["ready"]
    assert banner["text"] == "I need/want something for/from Rob …"
    assert banner["parts"] == [
        {"category": "who", "value": "Rob", "band": "working"}
    ]
    # Direction evidence collapses the for/from alternate into "to tell".
    rnd._history += [
        {"kind": "query", "text": f"t{i}", "answer": "yes",
         "slots": {"who": "Rob"}, "direction": "tell_them"}
        for i in range(2)
    ]
    assert rnd.banner()["text"] == "I need/want something to tell Rob …"


async def test_no_synthesis_entry_is_ever_unconfirmed(
    topics: list[Topic],
) -> None:
    """The invariant that made the `pin` directive dead code.

    `pin` targeted the weakest slot of a REJECTED utterance. Since W1-C deleted
    engine-initiated synthesis there is exactly one place a synthesis entry is
    written — `accept()` — and it hardcodes answer=YES, so "rejected" could
    never occur. No synthesis action ever becomes `_pending` either: the two
    `synthesize()` callers (`_refresh_draft`, `restate()`) touch only the draft
    cache.

    If this test ever fails, proposals are back and `pin` should come back with
    them.
    """
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    for _ in range(3):
        assert rnd._pending is None or rnd._pending.kind == "query"
        await rnd.answer(Answer.YES)
    rnd.accept()
    synths = [h for h in rnd.history if h.get("kind") == "synthesis"]
    assert synths, "accept() writes the only synthesis entry there is"
    assert all(h["answer"] == Answer.YES.value for h in synths)


# ------------------------------------------ refinement links (W3-H)


async def test_refines_tag_recorded_and_frontier_woven(
    topics: list[Topic],
) -> None:
    # e2e: a refinement-tagged yes deepens the WEAVE (the draft says the
    # fine value) while family confidence absorbs the fragmentation.
    state = {"q": 0}
    script = [
        {"question": "Are you feeling discomfort?",
         "slots": {"what": "discomfort"}},
        {"question": "Is the discomfort more like tingling?",
         "slots": {"what": "tingling"}, "refines": {"what": "discomfort"}},
    ]

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps({"what": ["discomfort", "an ache"],
                               "where": ["legs", "back"],
                               "how": ["adjust position"]})
        if "pin down the ONE specific" in system:
            return "thinking"
        if "Convert a drafted question" in system:
            payload = script[min(state["q"], len(script) - 1)]
            state["q"] += 1
            return json.dumps({**payload, "preface": "", "rationale": "x"})
        return json.dumps({"utterance": "It tingles."})

    rnd = Round(_topic(topics, "physical_health"),
                llm=MockBackend(responder=responder), rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # discomfort +1
    await rnd.answer(Answer.YES)  # tingling +1, refines discomfort
    entry = next(h for h in rnd.history if h.get("refines"))
    assert entry["refines"] == {"what": "discomfort"}
    board = rnd._replay_board()
    assert rnd._edges["what"]["tingling"] == "discomfort"
    assert rnd._weave(board)["what"] == "tingling"  # frontier, not the root
    assert any(p["value"] == "tingling" for p in rnd.banner()["parts"])


def test_family_confidence_ends_the_what_hammering(topics: list[Topic]) -> None:
    # Thigh-round shape: with refinements linked, the what FAMILY is
    # established, so focus moves to the open core slot instead of drilling
    # the fragmented sensation for the 22nd time.
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._seed_values = {"what": ["discomfort"], "where": ["legs", "back"],
                        "how": ["adjust position"]}
    rnd._history = [
        {"kind": "query", "text": "d1", "answer": "yes",
         "slots": {"what": "discomfort"}},
        {"kind": "query", "text": "d2", "answer": "yes",
         "slots": {"what": "discomfort"}},
        {"kind": "query", "text": "t1", "answer": "yes",
         "slots": {"what": "tingling"}, "refines": {"what": "discomfort"}},
    ]
    board = rnd._replay_board()
    focus, directive, _ = rnd._pick_focus(board, rnd._history)
    assert (focus, directive) == ("where", "probe")


def test_replacement_value_extraction() -> None:
    # The three trial-2 ban notes, verbatim — each was actually a replacement.
    rv = Round._replacement_value
    assert rv("plans seem to be dinner", "making plans") == "dinner"
    assert rv("Replace a visit with dinner", "a visit") == "dinner"
    assert (
        rv("she seems to be indicating the right leg is the body part",
           "body part")
        == "right leg"
    )
    assert rv("no, not a drink", "a drink") == ""  # plain ban — nothing to mint


async def test_edit_replacement_bans_and_mints(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # what='a drink' +1 → in the weave
    ev = await rnd.edit("a drink seems to be hot tea")
    assert ev.kind == "query"
    board = rnd._replay_board()
    assert board["what"]["a drink"] == facets.ELIMINATE_FLOOR  # struck
    assert board["what"]["hot tea"] == 2.0  # minted at context strength
    assert rnd._weave(board)["what"] == "hot tea"  # the draft re-weaves on it
    entry = next(h for h in rnd.history if h["kind"] == "edit")
    assert entry["ban"] == {"category": "what", "value": "a drink"}
    assert entry["mint"] == {"category": "what", "value": "hot tea"}


# ----------------------------------------- the synthesis editor (W1-F)


async def test_replace_refines_when_extending(topics: list[Topic]) -> None:
    # The Avalanche case: "tickets" → "Avalanche tickets" is an AUGMENTATION
    # — the old value stays (as the parent), the draft deepens, nothing is
    # banned, and the caregiver-chosen value needs no double-check.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # what='a drink' +1
    ev = await rnd.replace("what", "a drink", "a hot drink")
    assert ev.kind == "query"
    board = rnd._replay_board()
    assert board["what"]["a drink"] == 1.0  # NOT banned — it is the parent
    assert board["what"]["a hot drink"] == 2.0  # caregiver strength
    assert rnd._edges["what"]["a hot drink"] == "a drink"  # the dive
    assert rnd._weave(board)["what"] == "a hot drink"  # frontier deepened
    assert rnd._verify_due(board) is None  # caregiver-chosen — no double-check


async def test_replace_swaps_when_different(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # what='a drink' +1
    await rnd.replace("what", "a drink", "the Avalanche tickets")
    board = rnd._replay_board()
    assert board["what"]["a drink"] == facets.ELIMINATE_FLOOR  # struck
    assert board["what"]["the Avalanche tickets"] == 2.0
    assert rnd._weave(board)["what"] == "the Avalanche tickets"
    banner = rnd.banner()
    assert {"category": "what", "value": "a drink"} in banner["banned"]


async def test_replace_with_empty_removes_the_detail(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    ev = await rnd.replace("when", "today", "")
    assert ev.kind == "query"
    assert rnd.banner()["muted"] == ["when"]
    assert "when" not in rnd._weave(rnd._replay_board())


async def test_restate_changes_only_the_draft(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    before_history = len(rnd.history)
    pending = rnd._pending
    await rnd.restate()
    text1 = rnd.banner()["text"]
    await rnd.restate()
    text2 = rnd.banner()["text"]
    assert text1 and text2 and text1 != text2  # same content, new words
    assert "water" in text1 and "water" in text2
    assert len(rnd.history) == before_history  # the board is untouched
    assert rnd._pending is pending  # the pending question is untouched


async def test_free_text_augmentation_note_is_context_not_a_ban(
    topics: list[Topic],
) -> None:
    # The Avalanche blow-up: an augmentation note mentioning the CORRECT
    # woven value must never ban it — without a negation cue or replacement
    # marker, the note is guiding context.
    rnd = Round(_topic(topics, "physical_health"),
                llm=_controller_backend(expand={"what": ["a drink"]}),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # what='a drink' in the weave
    await rnd.edit("the news is that she wants a drink with lots of ice")
    kinds = [h["kind"] for h in rnd.history]
    assert "edit" not in kinds and "context" in kinds
    board = rnd._replay_board()
    assert board["what"]["a drink"] > 0  # the correct anchor survived


async def test_removal_note_bans_without_minting_junk(topics: list[Topic]) -> None:
    # "remove worrying" minted why='remove' in the Avalanche round and the
    # engine then double-checked the junk aloud. Marker-gated minting: a
    # removal note bans, full stop.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # what='a drink' +1
    await rnd.edit("remove a drink")
    entry = next(h for h in rnd.history if h["kind"] == "edit")
    assert entry["ban"] == {"category": "what", "value": "a drink"}
    assert "mint" not in entry  # nothing invented from the instruction words


async def test_edit_mute_dims_a_slot_and_redirects(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    ev = await rnd.edit("the when does not matter")
    assert ev.kind == "query"
    assert rnd.banner()["muted"] == ["when"]
    board = rnd._replay_board()
    assert "when" not in rnd._weave(board)  # never woven, never spoken
    assert rnd._pick_focus(board, [])[0] != "when"  # never asked about


async def test_edit_without_a_draft_match_becomes_context(
    topics: list[Topic],
) -> None:
    # Nothing the caregiver types is dropped: an ✗-note matching neither a
    # slot dismissal nor a woven value lands as ordinary guiding context.
    rnd = Round(_topic(topics, "physical_health"),
                llm=_controller_backend(expand={"why": ["thirsty"]}),
                rng=_FixedRandom(0.99))
    await rnd.open()
    ev = await rnd.edit("she keeps pointing at the window")
    assert ev.kind == "query"
    kinds = [h["kind"] for h in rnd.history]
    assert "context" in kinds and "edit" not in kinds


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


# ----------------------------------------------------- verify-on-lock (W2-F)


def test_verify_due_matrix(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink", "a snack"], "how": ["bring it"]}
    # Two patient yeses — solidly confirmed, no double-check needed.
    rnd._history = [
        {"kind": "query", "text": f"q{i}", "answer": "yes",
         "slots": {"what": "a drink"}} for i in range(2)
    ]
    assert rnd._verify_due(rnd._replay_board()) is None
    # One yes + a caregiver boost locked it — double-check due.
    rnd._history = [
        {"kind": "query", "text": "q", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "context", "text": "cup", "answer": None,
         "slots": {"what": ["a drink"]}},
    ]
    assert rnd._verify_due(rnd._replay_board()) == ("what", "a drink")
    # Context-only lock (the patient never confirmed at all) — due.
    rnd._history = [
        {"kind": "context", "text": "cup", "answer": None,
         "slots": {"what": ["a drink"]}},
    ]
    assert rnd._verify_due(rnd._replay_board()) == ("what", "a drink")
    # A spent double-check is never repeated — even when it was answered no.
    rnd._history = [
        {"kind": "query", "text": "q", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "context", "text": "cup", "answer": None,
         "slots": {"what": ["a drink"]}},
        {"kind": "query", "text": "v", "answer": "no",
         "slots": {"what": "a drink"}, "verify": True},
    ]
    assert rnd._verify_due(rnd._replay_board()) is None


def test_verify_budget_is_capped(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink"], "how": ["bring it"],
                        "why": ["thirsty"]}
    rnd._history = [
        {"kind": "query", "text": "v1", "answer": "yes",
         "slots": {"what": "a drink"}, "verify": True},
        {"kind": "query", "text": "v2", "answer": "yes",
         "slots": {"how": "bring it"}, "verify": True},
        # a fresh context-only lock that WOULD be due...
        {"kind": "context", "text": "thirsty", "answer": None,
         "slots": {"why": ["thirsty"]}},
    ]
    assert rnd._verify_due(rnd._replay_board()) is None  # budget spent


def test_direction_credited_bucket_needs_no_verify(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend(),
                rng=_FixedRandom(0.99))
    rnd._seed_values = rnd._inject_direction_buckets(
        {"who": ["Rob"], "what": ["a chore", "a meal"]}
    )
    rnd._history = [
        {"kind": "query", "text": f"t{i}", "answer": "yes",
         "slots": {"who": "Rob"}, "direction": "tell_them"} for i in range(2)
    ]
    board = rnd._replay_board()
    bucket = facets.DIRECTION_BUCKETS["tell_them"]
    assert board["how"][bucket] == 2.0  # climbed purely via direction credits
    assert rnd._verify_due(board) is None  # those yeses count as confirmations


async def test_context_locked_pair_gets_a_double_check(
    topics: list[Topic],
) -> None:
    backend = _controller_backend(expand={"what": ["a drink"]})
    rnd = Round(_topic(topics, "physical_health"), llm=backend,
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)  # "Is it about a drink?" → +1
    # The caregiver note boosts the same value to +3 — locked on ONE yes:
    # the very next turn must be the gate-exempt double-check.
    ev = await rnd.add_context("she pointed at her cup — she wants a drink")
    assert ev.kind == "query"
    assert rnd._pending is not None and rnd._pending.verify is True
    assert rnd._pending.slots == {"what": "a drink"}
    assert ev.preface.startswith("Just to double-check")

    ev = await rnd.answer(Answer.NO)  # the double-check is rejected
    entry = next(h for h in rnd.history if h.get("verify"))
    assert entry["answer"] == "no"
    # W2-T: the contradiction is HELD, not scored. The pair keeps 1 + 2 = 3.0
    # (it used to be knocked to 2.0 immediately) and the entry is marked
    # contested, so the anchored clarification carries the real evidence.
    assert entry["contested"] is True
    board = rnd._replay_board()
    assert board["what"]["a drink"] == 3.0
    # Never re-verified, and the round is now clarifying rather than moving on.
    assert rnd._pending is not None and not rnd._pending.verify
    state = rnd._clarify_state()
    assert state is not None
    assert (state["category"], state["value"]) == ("what", "a drink")
    assert state["reason"] == "contradicted" and state["asked"] == 0


# ------------------------------------- clarifying mode (W2-T / W2-U)


def _clarify_backend(value: str = "a drink") -> MockBackend:
    """The drill protocol, plus a caregiver note that credits `value`.

    Clarification questions are TEMPLATED by the engine and make no LLM call at
    all, so this backend never has to produce one — which is itself the thing
    worth noticing about the design.
    """
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:  # seed
            return json.dumps(SEED_SLOTS)
        if "HIGH-TRUST" in system:  # expand (caregiver note)
            return json.dumps({"slots": {"what": [value]}})
        if "DOUBLE-CHECK" in system:  # the double-check turn
            return json.dumps({"question": f"Can you confirm it is {value}?"})
        if "OPPOSITE button" in system:  # the caregiver flips the question
            return json.dumps(
                {"question": f"Is it something other than {value}?",
                 "slots": {"what": value}}
            )
        if "pin down the ONE specific" in system:  # deliberate
            return "thinking it through..."
        if "Convert a drafted question" in system:  # format
            word = _SUBJECTS[state["q"] % len(_SUBJECTS)]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {"what": word},
                 "preface": "", "rationale": "drill"}
            )
        return json.dumps({"utterance": _UTTERANCES[0]})

    return MockBackend(responder=responder)


async def _contradicted(topics: list[Topic], value: str = "a drink") -> Round:
    """A round sitting on a fresh contradiction, clarifying-mode question pending."""
    rnd = Round(
        _topic(topics, "physical_health"),
        llm=_clarify_backend(value),
        rng=_FixedRandom(0.99),
    )
    await rnd.open()
    await rnd.answer(Answer.YES)  # confirms the seeded value at +1
    # The note must SAY the value or W2-M drops it — that is the point of W2-M.
    await rnd.add_context(f"she pointed at her cup — she wants {value}")  # +2
    assert rnd._pending is not None and rnd._pending.verify  # the double-check
    await rnd.answer(Answer.NO)  # …contradicted
    return rnd


async def test_clarify_walks_the_draft_one_detail_at_a_time(
    topics: list[Topic],
) -> None:
    rnd = await _contradicted(topics)
    assert rnd._pending is not None
    action = rnd._pending
    assert action.clarify is True
    assert action.focus == "what"
    # Owner-specified shape: pointed, one detail, no LLM call behind it.
    assert action.content == "This is about a drink, correct?"
    assert action.slots == {"what": "a drink"}
    # The mode announces itself ALOUD, because the patient hears the questions
    # rather than reading the demarcated conversation.
    assert action.preface == "Let me check this one piece at a time —"
    # …and the cockpit is told to demarcate.
    ev = rnd._pending_event()
    assert ev.clarifying is True


def test_clarify_question_emphasises_what_distinguishes_a_refinement() -> None:
    q = Round._clarify_question("where", "right foot", parent="foot")
    assert q == "This is about RIGHT foot, correct?"
    # No parent: nothing to contrast against, so nothing is emphasised.
    assert Round._clarify_question("where", "foot") == "This is about foot, correct?"
    assert Round._clarify_question("why", "thirsty") == (
        "This is because of thirsty, correct?"
    )
    # `how` holds verb phrases, and the default frame reads as broken English
    # around them ("This is about call them, correct?" — a real live output).
    assert Round._clarify_question("how", "call them") == (
        "You want to call them, correct?"
    )
    # …but `how` also holds GERUNDS, which break the infinitive frame the other
    # way. "You want to lifting things, correct?" was spoken to a patient in a
    # live round. The frame follows the value's form, not its slot.
    assert Round._clarify_question("how", "lifting things") == (
        "This is about lifting things, correct?"
    )
    assert Round._clarify_question("how", "bring it") == (
        "You want to bring it, correct?"  # -ing, but not a gerund
    )


def test_clarify_details_walk_the_chain_coarse_to_fine(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["pain"], "where": ["right foot"]}
    rnd._history = [
        {"kind": "query", "text": "Pain?", "answer": "yes", "slots": {"what": "pain"}},
        {"kind": "query", "text": "Foot?", "answer": "yes", "slots": {"where": "foot"}},
        {"kind": "query", "text": "Right foot?", "answer": "yes",
         "slots": {"where": "right foot"}, "refines": {"where": "foot"}},
    ]
    board = rnd._replay_board()
    details = rnd._clarify_details(board)
    # "foot" before "right foot" — the general detail is confirmed before the
    # one that distinguishes it, exactly as specified.
    assert ("where", "foot") in details
    assert details.index(("where", "foot")) < details.index(("where", "right foot"))
    assert ("what", "pain") in details


async def test_clarify_ends_on_the_first_no_which_localizes_the_error(
    topics: list[Topic],
) -> None:
    rnd = await _contradicted(topics)
    board_before = rnd._replay_board()["what"]["a drink"]
    await rnd.answer(Answer.NO)
    assert rnd._clarify_state() is None  # localized — the walk is done
    # The contradicted double-check still scored nothing; the ANCHORED question
    # is what carries the loss, and it lands once.
    assert rnd._replay_board()["what"]["a drink"] == board_before - 1.0
    assert rnd.clarifications[0]["outcome"] == "localized"


async def test_clarify_confirms_the_draft_and_the_belief_survives(
    topics: list[Topic],
) -> None:
    rnd = await _contradicted(topics)
    # 1 (yes) + 2 (note) — the double-check's "no" never scored.
    assert rnd._replay_board()["what"]["a drink"] == 3.0
    await rnd.answer(Answer.YES)
    assert rnd._replay_board()["what"]["a drink"] == 4.0
    assert rnd.clarifications[0]["outcome"] == "confirmed"


async def test_clarify_never_ends_the_round(topics: list[Topic]) -> None:
    rnd = await _contradicted(topics)
    for _ in range(MAX_CLARIFY_QUERIES):  # "kinda" settles nothing
        if rnd._pending is None or not rnd._pending.clarify:
            break
        await rnd.answer(Answer.KINDA)
    assert rnd.outcome is None  # not terminal, not a diagnostic
    record = rnd.clarifications[0]
    # The record states a fact about the DIALOGUE, never about the person.
    assert set(record) == {
        "reason", "category", "value", "trigger_question", "attempts", "outcome"
    }
    assert record["outcome"] in ("unresolved", "open", "confirmed")


async def test_flipping_a_clarification_stays_in_the_mode_and_advances_it(
    topics: list[Topic],
) -> None:
    """The opposition button inside clarifying mode (live report, 09-10).

    A flip builds a fresh action, so without carrying the mode across it (a)
    dropped the cockpit demarcation and (b) — because the walk tracked progress
    by question TEXT — never registered the detail as asked, and re-issued the
    identical template on the very next turn.
    """
    rnd = await _contradicted(topics)
    before = rnd._pending
    assert before is not None and before.clarify
    detail = before.clarify_detail
    assert detail is not None

    ev = await rnd.flip()
    flipped = rnd._pending
    assert flipped is not None
    assert flipped.content != before.content  # a genuinely different question
    assert flipped.clarify is True and ev.clarifying is True  # mode survives
    assert flipped.clarify_detail == detail  # …and knows what it is about

    await rnd.answer(Answer.YES)
    # The walk MOVED ON — it does not re-ask the question that was flipped away.
    nxt = rnd._pending
    assert nxt is None or nxt.content != before.content
    state = rnd._clarify_state()
    if state is not None and state["phase"] == "confirm":
        assert state["next"] != detail


async def test_a_flipped_clarification_decides_nothing(
    topics: list[Topic],
) -> None:
    # A flipped question asserts its OWN slots, so its answer cannot be read as
    # confirming or denying the detail the step was about — it counts as asked
    # and nothing more. A "no" here must not localize.
    rnd = await _contradicted(topics)
    await rnd.flip()
    await rnd.answer(Answer.NO)
    entry = next(h for h in rnd.history if h.get("flipped_from"))
    assert entry["clarify"] is True
    assert rnd.clarifications[0]["outcome"] != "localized"


async def test_undo_reopens_the_clarification(topics: list[Topic]) -> None:
    """Clarify state is DERIVED, so rewinding an answer rewinds the mode too."""
    rnd = await _contradicted(topics)
    await rnd.answer(Answer.NO)
    assert rnd._clarify_state() is None
    await rnd.undo()
    state = rnd._clarify_state()
    assert state is not None and state["reason"] == "contradicted"


def test_contested_no_neither_scores_nor_counts_as_a_no(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink"]}
    rnd._history = [
        {"kind": "query", "text": "Is it a drink you want?", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "query", "text": "Is it a drink?", "answer": "no",
         "slots": {"what": "a drink"}, "verify": True, "contested": True},
    ]
    assert rnd._replay_board()["what"]["a drink"] == 1.0  # the yes survives
    assert rnd._consec_no_streak() == 0  # an ambiguity, not a no
    # …and it must never mark its own value as an exhausted avenue, which would
    # forbid the very question the clarification is about to ask.
    assert rnd._futile_pair(rnd._history * 2) is None
    state = rnd._clarify_state()
    assert state is not None and state["reason"] == "contradicted"


# ---- the second trigger: a score that rises and then falls


def test_score_conflict_fires_on_a_rise_then_fall(topics: list[Topic]) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["pain", "a drink"]}
    rnd._history = [
        {"kind": "query", "text": "Pain?", "answer": "yes", "slots": {"what": "pain"}},
        {"kind": "query", "text": "Still pain?", "answer": "no",
         "slots": {"what": "pain"}},
    ]
    conflict = rnd._score_conflict()
    assert conflict is not None
    assert (conflict["category"], conflict["value"]) == ("what", "pain")
    assert conflict["peak"] == 1.0


def test_a_conflict_with_nothing_scored_does_not_open_the_mode(
    topics: list[Topic],
) -> None:
    """The gate: no scored detail means nothing to clarify, so do not enter.

    `pain` rose to 1.0 and fell back to 0.0, taking it off the draft. There is
    a conflict, but no object to clarify — asking about it would interrogate a
    value the board has already discarded.
    """
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["pain"]}
    rnd._history = [
        {"kind": "query", "text": "Pain?", "answer": "yes", "slots": {"what": "pain"}},
        {"kind": "query", "text": "Still pain?", "answer": "no",
         "slots": {"what": "pain"}},
    ]
    assert rnd._score_conflict() is not None  # the conflict IS detected
    assert rnd._clarify_details(rnd._replay_board()) == []  # …but nothing scored
    assert rnd._clarify_state() is None  # …so the mode stays shut


def test_a_conflict_opens_the_mode_once_a_detail_is_scored(
    topics: list[Topic],
) -> None:
    # Same conflict, but `a drink` is still standing — that scored detail is
    # the object to clarify, so the mode opens and walks it.
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["pain"], "how": ["bring it"]}
    rnd._history = [
        {"kind": "query", "text": "Pain?", "answer": "yes", "slots": {"what": "pain"}},
        {"kind": "query", "text": "Bring it?", "answer": "yes",
         "slots": {"how": "bring it"}},
        {"kind": "query", "text": "Still pain?", "answer": "no",
         "slots": {"what": "pain"}},
    ]
    state = rnd._clarify_state()
    assert state is not None
    assert state["reason"] == "non-monotonic"
    assert state["phase"] == "confirm"
    # The conflicted value is off the draft, so it is NOT what gets walked —
    # the scored detail is.
    assert ("what", "pain") not in state["details"]
    assert state["next"] == ("how", "bring it")


# ---- phase 2: every detail held, so the FRAMING is what is wrong


def test_dig_axes_keep_the_anchor_and_change_the_angle(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "my_people"), llm=MockBackend())
    rnd._seed_values = {"who": ["Rob"], "how": ["remind"]}
    rnd._history = [
        {"kind": "query", "text": "Rob?", "answer": "yes", "slots": {"who": "Rob"}},
    ]
    axes = rnd._clarify_dig_axes(rnd._replay_board(), "who")
    # Owner's rule: a confirmed `who` is dug at from every OTHER angle.
    assert "who" not in axes
    assert set(axes) == {"what", "when", "where", "why", "how"}


async def test_all_details_confirmed_turns_into_a_dig(
    topics: list[Topic],
) -> None:
    rnd = await _contradicted(topics)
    state = rnd._clarify_state()
    assert state is not None and state["phase"] == "confirm"
    # Say yes to every scored detail the walk puts up.
    for _ in range(6):
        state = rnd._clarify_state()
        if state is None or state["phase"] != "confirm":
            break
        await rnd.answer(Answer.YES)
    state = rnd._clarify_state()
    assert state is not None
    # Nothing was wrong with the details, so the round digs at the anchor from
    # a different axis instead of declaring itself finished.
    assert state["phase"] == "dig"
    assert state["anchor"] == ("what", "a drink")
    assert state["axis"] != "what"
    assert rnd._pending is not None and rnd._pending.clarify
    assert rnd._pending.clarify_phase == "dig"
    # The controller ASKED for the new axis. This mock tags `what` whatever it
    # is asked, so W2-P re-attributes the focus to what the question actually
    # asserts — and `focus_requested` is what keeps the axis recoverable, which
    # is also what stops the walk retrying the same axis forever.
    p = rnd._pending
    assert (p.focus_requested or p.focus) == state["axis"]


async def test_a_dig_that_lands_closes_the_clarification(
    topics: list[Topic],
) -> None:
    rnd = await _contradicted(topics)
    for _ in range(6):
        state = rnd._clarify_state()
        if state is None or state["phase"] != "confirm":
            break
        await rnd.answer(Answer.YES)
    assert (rnd._clarify_state() or {}).get("phase") == "dig"
    # A dig closes on YES — the opposite of a confirm, because a yes here is
    # the missing frame rather than a detail holding.
    await rnd.answer(Answer.YES)
    assert rnd._clarify_state() is None


def test_score_that_only_ever_falls_is_not_a_conflict(topics: list[Topic]) -> None:
    # A wrong guess being eliminated is ordinary progress — it was never believed.
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a snack"]}
    rnd._history = [
        {"kind": "query", "text": "A snack?", "answer": "no",
         "slots": {"what": "a snack"}},
        {"kind": "query", "text": "Sure it is not a snack?", "answer": "no",
         "slots": {"what": "a snack"}},
    ]
    assert rnd._score_conflict() is None
    assert rnd._clarify_state() is None


def test_a_caregiver_edit_is_not_a_conflict(topics: list[Topic]) -> None:
    # Striking a value is the caregiver being RIGHT, not the board disagreeing
    # with itself — it must never drag the round into clarifying mode.
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["a drink"]}
    rnd._history = [
        {"kind": "query", "text": "A drink?", "answer": "yes",
         "slots": {"what": "a drink"}},
        {"kind": "edit", "text": "not a drink", "answer": None,
         "ban": {"category": "what", "value": "a drink"}},
    ]
    assert rnd._score_conflict() is None


# ------------------------------------------------- CB-2 · ⟳ Restate must restate


def _restate_backend(replies: list[str]) -> MockBackend:
    """Drill protocol, with the synthesize call returning `replies` in order."""
    state = {"q": 0, "s": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "DOUBLE-CHECK" in system:
            m = re.search(r':\s+"(.+)"', messages[1]["content"])
            return json.dumps({"question": f"Is it {m.group(1) if m else 'that'}?"})
        if "pin down the ONE specific" in system:
            return "thinking"
        if "Convert a drafted question" in system:
            word = _SUBJECTS[state["q"] % len(_SUBJECTS)]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {"what": word},
                 "preface": "", "rationale": "x"}
            )
        out = replies[min(state["s"], len(replies) - 1)]  # synthesize
        state["s"] += 1
        return json.dumps({"utterance": out})

    return MockBackend(responder=responder)


async def test_restate_rejects_an_unchanged_wording(topics: list[Topic]) -> None:
    """The 09-11 defect: press ⟳ twice, second press silently does nothing.

    `restate()` accepted whatever `synthesize` returned without checking it had
    changed, so a model that repeated itself left the caregiver pressing a
    button that appeared dead.
    """
    same = "I am feeling pain right now."
    rnd = Round(
        _topic(topics, "physical_health"),
        llm=_restate_backend([same, same, same]),
        rng=_FixedRandom(0.99),
    )
    await rnd.open()
    await rnd.answer(Answer.YES)
    assert rnd.banner()["state"] == "draft"

    await rnd.restate()
    assert rnd.banner()["text"] == same  # the first press lands

    # The model now only ever repeats itself: say so instead of no-op'ing.
    with pytest.raises(RuntimeError, match="same wording"):
        await rnd.restate()
    assert rnd.banner()["text"] == same  # …and the draft is left intact


async def test_restate_accumulates_what_it_has_already_said(
    topics: list[Topic],
) -> None:
    rnd = Round(
        _topic(topics, "physical_health"),
        llm=_restate_backend(["First wording.", "Second wording.", "Third one."]),
        rng=_FixedRandom(0.99),
    )
    await rnd.open()
    await rnd.answer(Answer.YES)
    for expected in ("First wording.", "Second wording.", "Third one."):
        await rnd.restate()
        assert rnd.banner()["text"] == expected
    # Every wording worn so far is carried forward, so the NEXT press has all
    # of them to avoid rather than only the latest.
    assert "First wording." in rnd._restated
    assert "Third one." in rnd._restated


def test_same_wording_is_stricter_than_the_repeat_gate() -> None:
    from my20q.agent.auditor import same_wording

    assert same_wording("I am feeling pain right now.", "I am feeling pain right now!")
    assert same_wording("I need a drink", "i need a drink")
    # A genuine rephrase is a SUCCESS for restate even though it is close —
    # this is a different question from "is this query redundant".
    assert not same_wording(
        "I am feeling pain right now.", "Right now, I am in pain."
    )
    assert not same_wording("I would like a drink.", "Could I have some water?")


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
    monkeypatch.setenv("MY20Q_EXPLORE_DECAY", "1.8")  # above the range -> clamped
    monkeypatch.setenv("MY20Q_SPLIT_MARGIN", "0.5")
    monkeypatch.setenv("MY20Q_STALL_WINDOW", "12")
    t = ReasoningTuning.from_env()
    assert t.explore_decay == 1.0  # clamped into [0, 1]
    assert t.facet_split_margin == 0.5
    assert t.stall_window == 12
    assert t.facet_ready_points == 2.0  # untouched -> default


def test_the_retired_synthesis_knobs_are_gone(monkeypatch) -> None:
    """They were parsed, clamped and documented to operators while doing nothing.

    The living proposal banner replaced engine-initiated synthesis in 06-11 and
    these count-gates have had no effect since; they were kept "until the banner
    survives a live trial", which it has.
    """
    for var in ("MY20Q_MIN_YES", "MY20Q_NEW_YES", "MY20Q_REPHRASE_LIMIT",
                "MY20Q_SYNTH_ATTEMPTS"):
        monkeypatch.setenv(var, "99")
    t = ReasoningTuning.from_env()
    for gone in ("min_yes_for_synthesis", "new_yes_for_resynthesis",
                 "rephrase_limit", "synth_attempts_before_restart"):
        assert not hasattr(t, gone), f"{gone} is back — does it DO anything now?"


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


async def test_a_slot_that_keeps_minting_values_keeps_exploring(
    topics: list[Topic],
) -> None:
    # RNG pinned just under the initial rate. (min_yes is huge so the round
    # never synthesizes.)
    seen: list[bool] = []
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "DOUBLE-CHECK" in system:
            # Required now that every new detail draws a check: a mock without
            # this branch falls through to the synthesis reply, the verify call
            # finds no question, and the round restarts out of recovery.
            m = re.search(r':\s+"(.+)"', messages[1]["content"])
            return json.dumps({"question": f"Is it {m.group(1) if m else 'that'}?"})
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
        tuning=ReasoningTuning(explore_decay=2 / 3),
        rng=_FixedRandom(0.4),  # pinned just under the initial 0.667 rate
    )
    await rnd.open()  # the focus slot knows nothing -> p=0.667 > 0.4 -> explore
    assert seen[0] is True
    # W2-X: the decay follows what the FOCUS SLOT knows, not how long the round
    # has run — and this mock confirms a DIFFERENT value every turn, so the
    # slot's leading family never accumulates however many yeses arrive. A slot
    # that keeps producing new contenders has not converged, and exploration
    # correctly stays on. Under the old round-level rule these same answers
    # would have driven it to ~0, which is the §1i failure in miniature.
    while len(seen) < 4 and not rnd.is_terminal and rnd._pending is not None:
        await rnd.answer(Answer.YES)
    assert all(seen), seen


def test_rejecting_the_placeholder_is_what_frees_the_starved_slot(
    topics: list[Topic],
) -> None:
    """W2-Y alone fixes the §1i starvation — the drill ranking needed no change.

    On that board `where` was led by "one specific area of your body" at +4.0,
    which made the slot look ESTABLISHED. Key 2 of the drill ranking already
    puts unestablished slots ahead of established ones, so the placeholder was
    never beating the ordering — it was lying to it.

    That matters beyond this one slot: DRILL is an exploitation move, and a
    weakest-first drill ranking (briefly shipped as W2-X, reverted) asks the
    engine to refine the thing it is least sure of.
    """
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend())
    rnd._seed_values = {"what": ["discomfort"], "where": ["arm"]}
    rnd._edges = {c: {} for c in facets.CATEGORIES}
    # Every modifier carries a value, so the empty-modifier probe cannot fire
    # and the policy reaches DRILL.
    mods = {"when": {"now": 1.0}, "who": {"Rob": 1.0},
            "why": {"a fall": 1.0}, "how": {"rest": 1.0}}

    # With the placeholder holding `where` at +4.0 both core slots look
    # established, so key 2 cannot separate them and topic priority picks
    # `what` — which is the slot that drew 32 of 69 focuses in §1i.
    lying = {
        "what": {"discomfort": 9.5, "pain": 6.0},
        "where": {"one specific area of your body": 4.0, "arm": 1.0},
        **mods,
    }
    focus, directive, _ = rnd._pick_focus(lying, [])
    assert (focus, directive) == ("what", "drill")

    # W2-Y stops it being minted at all, so `where` sits at 1.0 —
    # unestablished — and key 2 hands it the focus, ordering untouched.
    assert facets.is_vacuous("one specific area of your body")
    honest = {**lying, "where": {"arm": 1.0}}
    focus, _directive, _ = rnd._pick_focus(honest, [])
    assert focus == "where"

def test_explore_probability_follows_the_slot_not_the_round(
    topics: list[Topic],
) -> None:
    """The §1i failure, as a unit.

    That round had 32 yeses — so the OLD round-level decay had driven
    exploration to ~0 — while `where` still had no real answer at all. The round
    looked settled; the slot did not.
    """
    rnd = Round(_topic(topics, "physical_health"), llm=MockBackend(),
                tuning=ReasoningTuning(explore_decay=2 / 3))
    rnd._seed_values = {"what": ["pain"], "where": ["arm"]}
    rnd._history = [
        {"kind": "query", "text": f"q{i}", "answer": "yes",
         "slots": {"what": "pain"}} for i in range(6)
    ]
    board = rnd._replay_board()

    known = rnd._slot_confirmation_depth(board, "what")    # 6.0 / 2.0 ready
    unknown = rnd._slot_confirmation_depth(board, "where")  # nothing at all
    assert known == 3.0 and unknown == 0.0

    p_known = rnd._explore_probability(known)
    p_unknown = rnd._explore_probability(unknown)
    assert p_unknown > p_known          # explore where we are ignorant…
    assert p_unknown > 0.6 and p_known < 0.25   # …and exploit where we are not


# ------------------------------------------- autopsy instrumentation (W2-O)


async def test_banner_snapshot_rides_every_answered_entry(
    topics: list[Topic],
) -> None:
    # The record carried no banner state, so an autopsy could not say WHEN a
    # board turned propose-ready -- the metric separating "the round needed 18
    # questions" from "it was answerable at q9 and asked nine more".
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    entry = rnd.history[-1]
    assert entry["banner"]["parts"], "the answered entry carries the live weave"
    assert entry["banner"]["text"]
    # A single yes is below board-readiness; the flag is honest about that.
    assert entry["banner"]["ready"] is False


async def test_banner_ready_transition_is_recoverable(topics: list[Topic]) -> None:
    # physical_health gates readiness on BOTH core slots (what + where), so the
    # mock feeds one then the other; facet_ready_points=1 makes a single yes
    # enough, keeping the false -> true edge inside a two-question round.
    state = {"q": 0}
    asks = [
        ("Is it about a drink?", {"what": "a drink"}),
        ("Is it about your left hand?", {"where": "your left hand"}),
    ]

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "pin down the ONE specific" in system:
            return "thinking"
        if "DOUBLE-CHECK" in system:  # a thin lock draws verify-on-lock
            m = re.search(r':\s+"(.+)"', messages[1]["content"])
            return json.dumps({"question": f"Can you confirm {m.group(1)}?"})
        if "Convert a drafted question" in system:
            question, slots = asks[min(state["q"], len(asks) - 1)]
            state["q"] += 1
            return json.dumps(
                {"question": question, "slots": slots,
                 "preface": "", "rationale": "x"}
            )
        return json.dumps({"utterance": "I would like a drink."})

    rnd = Round(
        _topic(topics, "physical_health"),
        llm=MockBackend(responder=responder),
        tuning=ReasoningTuning(facet_ready_points=1.0),
        rng=_FixedRandom(0.99),
    )
    await rnd.open()
    for _ in range(3):  # what, its double-check, then where
        await rnd.answer(Answer.YES)
    flags = [h["banner"]["ready"] for h in rnd.history if h.get("banner")]
    # The edge itself is the metric: the record now says which query it was.
    assert flags[0] is False and flags[-1] is True
    # Exactly one false -> true edge — readiness is reached once and then held.
    # (Which query that is shifts as the double-check cadence changes, so the
    # monotonicity is the claim, not the index.)
    assert flags == sorted(flags)


async def test_banner_is_stamped_even_when_the_next_question_fails(
    topics: list[Topic],
) -> None:
    # A live gemma4:e4b run left one answered query with no banner: the answer
    # landed, then the NEXT ask failed the repeat gate and the failure path
    # returned before the stamp. An answer that tips the board into readiness
    # must not go unrecorded because of what happened after it.
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "pin down the ONE specific" in system:
            return "thinking"
        if "Convert a drafted question" in system:
            state["q"] += 1
            if state["q"] == 1:
                return json.dumps(
                    {"question": "Is it about a drink?", "slots": {"what": "a drink"},
                     "preface": "", "rationale": "x"}
                )
            return "not json at all"  # every later ask fails -> diagnostic
        return json.dumps({"utterance": "x"})

    rnd = Round(_topic(topics, "physical_health"),
                llm=MockBackend(responder=responder), rng=_FixedRandom(0.99))
    await rnd.open()
    event = await rnd.answer(Answer.YES)
    assert event.kind == "diagnostic"  # the next question could not be made
    answered = next(h for h in rnd.history if h["kind"] == "query")
    assert answered["banner"]["parts"], "the answer's board state was recorded"


async def test_open_does_not_stamp_a_banner(topics: list[Topic]) -> None:
    # open() advances with an empty history -- the stamp must not blow up.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    assert rnd.history == []


async def test_query_entries_carry_timing_and_the_round_carries_seed_ms(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    timing = rnd.history[-1]["timing"]
    assert timing["llm_calls"] >= 1
    assert timing["attempts"] >= 1
    assert timing["total_ms"] >= 0.0
    assert rnd.seed_ms >= 0.0  # the round's fixed pre-first-question cost


async def test_pending_question_survives_an_abandon(topics: list[Topic]) -> None:
    # A round abandoned on a topic switch leaves a question hanging; the record
    # used to drop it, so "ran out of questions" and "the caregiver walked away
    # mid-question" looked identical.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    event = await rnd.open()
    rnd.abandon()
    assert rnd.outcome == "abandoned"
    assert rnd.pending_question is not None
    assert rnd.pending_question["text"] == event.text
    assert rnd.pending_question["slots"]


async def test_no_pending_question_when_the_round_converged(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    rnd.accept()
    # accept() consumed the pending question into the synthesis entry.
    assert rnd.pending_question is None


async def test_restart_records_its_position(topics: list[Topic]) -> None:
    # Restart markers are stripped from the recorded conversation, which lost
    # WHERE each restart happened; after_query carries the position instead.
    tuning = ReasoningTuning(stall_window=0)
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                tuning=tuning, rng=_FixedRandom(0.99))
    await rnd.open()
    for _ in range(SOFT_RESET_NO_STREAK + 1):
        await rnd.answer(Answer.NO)
    restarts = rnd.board_record()["restarts"]
    assert len(restarts) == 1
    assert restarts[0]["reason"] == "no-streak"
    assert restarts[0]["after_query"] == SOFT_RESET_NO_STREAK + 1


def _ladder_backend(ladder: list[str]) -> MockBackend:
    """A mock that answers whichever slot the controller ASKED for.

    `drill` (priority 4) only becomes available once every core slot has a
    positive leader — priority-1 PROBE outranks it until then. A mock that
    only ever tags `what` therefore never reaches the drill path at all, so
    this one reads the focus out of the format prompt: non-target slots get a
    distinct filler value each time (so the repeat gate stays happy) and the
    target slot walks `ladder`, tagging NO `refines`.
    """
    state = {"q": 0, "filler": 0}
    fillers = _SUBJECTS[6:]  # distinct enough for the content-overlap gate

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps({**SEED_SLOTS, "what": [ladder[0]]})
        if "pin down the ONE specific" in system:
            return "thinking"
        if "DOUBLE-CHECK" in system:
            m = re.search(r':\s+"(.+)"', messages[1]["content"])
            return json.dumps({"question": f"Can you confirm {m.group(1)}?"})
        if "Convert a drafted question" in system:
            m = re.search(r"Focus slot: (\w*)", messages[1]["content"])
            cat = m.group(1) if m else "what"
            if cat != "what":
                word = fillers[state["filler"] % len(fillers)]
                state["filler"] += 1
                return json.dumps(
                    {"question": f"Is it about {word}?", "slots": {cat: word},
                     "preface": "", "rationale": "probe"}
                )
            # Past the end of the ladder, keep emitting values distinct ENOUGH
            # for the repeat gate, which matches on content overlap — so
            # "another thing 3"/"another thing 4" would collide.
            i = state["q"]
            state["q"] += 1
            word = ladder[i] if i < len(ladder) else _SUBJECTS[i % len(_SUBJECTS)]
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {"what": word},
                 "preface": "", "rationale": "drill"}  # note: no `refines`
            )
        return json.dumps({"utterance": "I would like a blanket."})

    return MockBackend(responder=responder)


async def _drive(rnd: Round, answers: list[Answer], stop=None) -> None:
    """Answer in sequence, pressing Retry through any diagnostic card.

    ``stop`` ends the drive early once the state under test is reached — which
    matters now that double-checks interleave with questioning, so "N answers"
    no longer means "N questions of the kind this test is about".
    """
    for a in answers:
        for _ in range(3):
            if rnd.is_terminal:
                return
            if rnd._pending is not None:
                break
            await rnd.retry()
        else:
            return
        await rnd.answer(a)
        if stop is not None and stop():
            return


async def test_drill_ladder_builds_edges_and_moves_the_draft(
    topics: list[Topic],
) -> None:
    # W2-R end to end: the controller drills the `what` slot, the model tags no
    # `refines` and the values are not lexically nested, so before this fix the
    # board fragmented and the banner froze on the first value.
    ladder = ["an object", "keeps you warm", "fabric", "a blanket"]
    rnd = Round(_topic(topics, "general"), llm=_ladder_backend(ladder),
                rng=_FixedRandom(0.99))
    await rnd.open()
    # Drive until the ladder is walked rather than a fixed number of turns:
    # double-checks now interleave with drills, so the turn count that reaches
    # the bottom rung moved — and overshooting mints values past the ladder.
    await _drive(
        rnd,
        [Answer.YES] * 20,
        stop=lambda: (
            facets.frontier(rnd._replay_board(), "what", rnd._edges) or ("",)
        )[0] == ladder[-1],
    )

    board = rnd._replay_board()
    assert rnd._edges["what"], "a drilled ladder must link, even with no refines tag"

    # The load-bearing claim: the evidence ACCUMULATES instead of fragmenting
    # into one singleton family per confirmed value. Mass is the sum of the
    # ladder values that were actually reached (verify turns and the other
    # core slot's probes consume some of the budget).
    confirmed = [
        v for v in ladder
        if any(
            h.get("answer") == Answer.YES.value
            and (h.get("slots") or {}).get("what") == v
            for h in rnd.history
        )
    ]
    assert len(confirmed) >= 2, "the mock should have walked into the ladder"
    fams = facets.families(board, "what", rnd._edges)
    assert len(fams) == 1, f"the slot fragmented into {len(fams)} families"
    assert fams[0][1] >= float(len(confirmed))

    # And the draft follows the answers DOWN the ladder rather than freezing on
    # the value that happened to be scored first.
    top = facets.frontier(board, "what", rnd._edges)[0]
    assert top == confirmed[-1] != ladder[0]
    assert any(p["value"] == top for p in rnd.banner()["parts"])


async def test_a_probe_never_infers_a_refinement(topics: list[Topic]) -> None:
    # Only a `drill` means "narrow this". A probe opens an empty slot, so its
    # yes is a first value, not a refinement of anything.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    first = rnd.history[0]
    assert "drill_parent" not in first


async def test_drill_parent_is_recorded_for_the_autopsy(
    topics: list[Topic],
) -> None:
    rnd = Round(_topic(topics, "general"),
                llm=_ladder_backend(["an object", "keeps you warm", "a blanket"]),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await _drive(rnd, [Answer.YES] * 20)
    drills = [h for h in rnd.history if h.get("drill_parent")]
    assert drills, "the ladder the controller walked must be recoverable"
    assert all(isinstance(h["drill_parent"], str) for h in drills)


async def test_a_no_to_a_drill_infers_nothing(topics: list[Topic]) -> None:
    # A drill that misses narrows nothing — the rejected value must not be
    # chained under the value it failed to refine.
    rnd = Round(_topic(topics, "general"),
                llm=_ladder_backend(["an object", "keeps you warm", "a blanket"]),
                rng=_FixedRandom(0.99))
    await rnd.open()
    for i in range(8):
        if rnd.is_terminal:
            break
        await rnd.answer(Answer.YES if i < 2 else Answer.NO)
    for h in rnd.history:
        if h.get("drill_parent") and h.get("answer") == Answer.NO.value:
            child = (h.get("slots") or {}).get(h.get("focus") or "")
            assert rnd._edges.get(h["focus"], {}).get(child) != h["drill_parent"]


# ------------------------------------------- W2-P: focus/content divergence


async def test_focus_is_reattributed_to_what_the_question_asserts(
    topics: list[Topic],
) -> None:
    # 23% of questions in the recorded trials asserted nothing in the slot the
    # controller asked for. Recording the REQUESTED slot made rotation believe
    # that slot had been covered while it still had no leader — so _pick_focus
    # kept re-selecting it (`why` starved 7 times, `what` asserted 12 instead).
    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:
            return json.dumps(SEED_SLOTS)
        if "pin down the ONE specific" in system:
            return "thinking"
        if "DOUBLE-CHECK" in system:
            return json.dumps({"question": "Can you confirm that?"})
        if "Convert a drafted question" in system:
            # Whatever the controller asks for, answer about `what`.
            return json.dumps(
                {"question": "Is it about a drink?", "slots": {"what": "a drink"},
                 "preface": "", "rationale": "x"}
            )
        return json.dumps({"utterance": "x"})

    rnd = Round(_topic(topics, "physical_health"),
                llm=MockBackend(responder=responder), rng=_FixedRandom(0.99))
    ev = await rnd.open()
    await rnd.answer(Answer.YES)
    entry = rnd.history[0]
    # Recorded as the slot it really asserts...
    assert entry["focus"] == "what"
    assert entry["slots"] == {"what": "a drink"}
    # ...and the controller's original ask survives for the autopsy.
    if "focus_requested" in entry:
        assert entry["focus_requested"] != "what"
    assert ev.kind == "query"


async def test_focus_is_left_alone_when_the_question_lands(
    topics: list[Topic],
) -> None:
    # No divergence — nothing to re-attribute, and no stray field recorded.
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await rnd.answer(Answer.YES)
    entry = rnd.history[0]
    assert entry["focus"] in entry["slots"]
    assert "focus_requested" not in entry


async def test_a_wandering_drill_infers_no_refinement(topics: list[Topic]) -> None:
    # A drill that lands on a DIFFERENT category is not a narrowing of the slot
    # it targeted — inferring an edge across categories would invent structure
    # the round never walked. So drill_parent is only set when it landed.
    rnd = Round(_topic(topics, "general"),
                llm=_ladder_backend(["an object", "keeps you warm", "a blanket"]),
                rng=_FixedRandom(0.99))
    await rnd.open()
    await _drive(rnd, [Answer.YES] * 12)
    for h in rnd.history:
        if h.get("kind") != "query" or not h.get("drill_parent"):
            continue
        # Every recorded drill_parent belongs to the slot actually asserted.
        assert h["focus"] in (h.get("slots") or {})
        assert "focus_requested" not in h
