"""Tests for the round engine — the 5W1H facet controller and its recovery."""

from __future__ import annotations

import json
import random

from my20q.agent.dialogue import (
    MIN_YES_FOR_SYNTHESIS,
    SOFT_RESET_NO_STREAK,
    Answer,
    Round,
    Session,
)
from my20q.agent.prompts import deliberate_messages, seed_messages
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


def _controller_backend(
    *,
    seed: dict[str, list[str]] | None = None,
    utterance: str = "I would like a glass of water.",
    expand: dict[str, list[str]] | None = None,
    slot_cat: str = "what",
) -> MockBackend:
    """A MockBackend that plays the seed/deliberate/format/expand/synth protocol.

    Each formatted question uses a fresh subject (tagged into ``slot_cat``), so
    the repeat gate and the slot-anchoring gate both pass indefinitely.
    """
    state = {"q": 0}

    def responder(messages: list) -> str:
        system = messages[0]["content"]
        if "starting GUESSES" in system:  # seed
            return json.dumps(seed or SEED_SLOTS)
        if "HIGH-TRUST" in system:  # expand (caregiver note)
            return json.dumps({"slots": expand or {}})
        if "Convert a drafted question" in system:  # format
            word = _SUBJECTS[state["q"] % len(_SUBJECTS)]
            state["q"] += 1
            return json.dumps(
                {"question": f"Is it about {word}?", "slots": {slot_cat: word},
                 "preface": "", "rationale": "drill"}
            )
        if "pin down the ONE specific" in system:  # deliberate
            return "thinking it through..."
        return json.dumps({"utterance": utterance})  # synthesize

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
    rnd = Round(_topic(topics, "physical_health"), llm=_controller_backend(),
                rng=_FixedRandom(0.99))
    await rnd.open()
    for _ in range(SOFT_RESET_NO_STREAK):  # exactly the threshold — no restart
        await rnd.answer(Answer.NO)
    assert not any(h["kind"] == "restart" for h in rnd.history)
    await rnd.answer(Answer.NO)  # one MORE -> the restart recovery fires
    assert any(h["kind"] == "restart" for h in rnd.history)
    assert rnd.engine == "reasoning"  # stayed in reasoning throughout


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
