"""Tests for the reasoner's call protocol and its hard, code-level gates.

The ask is two-phase for EVERY model — deliberate (free-form) then format
(strict JSON) — and the result passes three deterministic gates: yes/no
answerability, the repeat gate, and slot anchoring (a credited value must be
words the question itself says — the fix for the hallucinated-"Aaron" score
drift). See ``docs/design/reasoning-retro.md``.
"""

from __future__ import annotations

import json

import pytest

from my20q.agent import facets
from my20q.agent.reasoner import Reasoner, ReasonerError, _drill_violation
from my20q.llm import MockBackend

_BOARD = facets.seed_board(
    {"who": ["Zach"], "what": ["a picture", "a drink"], "how": ["move it"]}
)


def _format_responder(payloads: list[dict]):
    """Respond to DELIBERATE with prose and to FORMAT with the next payload."""
    state = {"n": 0}

    def responder(msgs: list) -> str:
        if "Convert a drafted question" in msgs[0]["content"]:  # FORMAT
            out = payloads[min(state["n"], len(payloads) - 1)]
            state["n"] += 1
            return json.dumps(out)
        return "thinking it through..."  # DELIBERATE draft

    return responder


async def _ask(mock: MockBackend, **kw):
    defaults = dict(
        topic_label="My people",
        board=_BOARD,
        focus="what",
        directive="probe",
        history=[],
        asked=[],
    )
    defaults.update(kw)
    return await Reasoner(mock).ask(**defaults)


async def test_ask_is_two_phase_for_every_model() -> None:
    good = {"question": "Is it a picture?", "slots": {"what": "a picture"},
            "preface": "", "rationale": "x"}
    mock = MockBackend(responder=_format_responder([good]))
    action = await _ask(mock)
    assert action.content == "Is it a picture?"
    assert len(mock.calls) == 2  # deliberate + format, regardless of model
    assert action.focus == "what"


async def test_format_salvages_from_the_draft() -> None:
    # If the format pass yields nothing usable, the structured question is
    # salvaged straight from the deliberate draft.
    good = json.dumps({"question": "Is it a picture?", "slots": {"what": "a picture"},
                       "rationale": "x"})

    def responder(msgs: list) -> str:
        if "Convert a drafted question" in msgs[0]["content"]:
            return "sorry, no idea"  # junk — not JSON
        return good  # the draft already carries the question JSON

    action = await _ask(MockBackend(responder=responder))
    assert action.content == "Is it a picture?"
    assert action.slots == {"what": "a picture"}


async def test_slot_anchoring_drops_unmentioned_values() -> None:
    # THE Aaron bug: the model tags a value the question never says — the tag
    # must be dropped, and crediting falls back to what the text DOES mention.
    bad_tag = {
        "question": "Is it about moving the picture?",
        "slots": {"who": "Aaron coming to visit"},  # never mentioned!
        "preface": "",
        "rationale": "x",
    }
    action = await _ask(MockBackend(responder=_format_responder([bad_tag])))
    assert "who" not in action.slots  # the hallucinated tag is gone
    # Deterministic recovery credited the mentioned board contenders instead.
    assert action.slots.get("what") == "a picture"


async def test_slot_values_fold_onto_existing_contenders() -> None:
    variant = {
        "question": "Do you need Zach to move the picture?",
        "slots": {"who": "zach", "what": "the picture"},
        "preface": "",
        "rationale": "x",
    }
    action = await _ask(MockBackend(responder=_format_responder([variant])))
    assert action.slots["who"] == "Zach"  # canonical casing
    assert action.slots["what"] == "a picture"  # folded onto the contender


# ------------------------------------------- autopsy instrumentation (W2-O)


async def test_ask_carries_the_gate_rejections_out() -> None:
    # The gate messages used to be discarded, so an autopsy could see a
    # question took three attempts but never WHY the first two were thrown
    # away. Attempt 1 fails the yes/no gate; attempt 2 passes.
    not_a_question = {"question": "Tell me about the picture.", "slots": {},
                      "preface": "", "rationale": "x"}
    good = {"question": "Is it a picture?", "slots": {"what": "a picture"},
            "preface": "", "rationale": "x"}
    action = await _ask(
        MockBackend(responder=_format_responder([not_a_question, good]))
    )
    assert action.content == "Is it a picture?"
    assert action.rejections  # the first attempt's gate reason survived
    assert action.timings["attempts"] == 2


async def test_ask_records_per_phase_timings() -> None:
    good = {"question": "Is it a picture?", "slots": {"what": "a picture"},
            "preface": "", "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([good])))
    t = action.timings
    assert t["llm_calls"] == 2  # deliberate + format
    assert t["attempts"] == 1
    assert "deliberate_ms" in t and "format_ms" in t
    # total_ms is wall clock for the whole turn — gates and parsing included.
    assert t["total_ms"] >= 0.0


async def test_exhausted_ask_names_the_gate_that_fired() -> None:
    # "could not produce a usable question" told the caregiver's diagnostic
    # card — and the record — nothing. The reason now carries the gate.
    not_a_question = {"question": "Tell me about the picture.", "slots": {},
                      "preface": "", "rationale": "x"}
    mock = MockBackend(responder=_format_responder([not_a_question]))
    with pytest.raises(ReasonerError) as exc:
        await _ask(mock)
    assert "rejected:" in str(exc.value)


async def test_verify_and_flip_are_instrumented() -> None:
    reasoner = Reasoner(
        MockBackend(responder=lambda msgs: json.dumps(
            {"question": "Do you mean a picture?", "slots": {"what": "a picture"}}
        ))
    )
    verified = await reasoner.verify(category="what", value="a picture", board=_BOARD)
    assert verified.timings["llm_calls"] == 1
    flipped = await reasoner.flip(
        question="Is it about moving the picture?", board=_BOARD, focus="what"
    )
    assert flipped.timings["attempts"] == 1


async def test_action_values_tagged_what_are_refiled_to_how() -> None:
    # The gemma4 trial filed "help with tasks" under WHAT (+6) while HOW never
    # established, jamming the focus policy — verb-led values re-file to how.
    mistagged = {
        "question": "Do you need help with tasks at the house?",
        "slots": {"what": "help with tasks"},
        "preface": "",
        "rationale": "x",
    }
    action = await _ask(MockBackend(responder=_format_responder([mistagged])))
    assert "what" not in action.slots
    assert action.slots["how"] == "help with tasks"


async def test_gate4_rejects_zero_information_questions() -> None:
    # Every tagged pair already an established leader → re-prompt (the cheap
    # expected-information-gain proxy; stops confirmation farming).
    farming = {"question": "Do you need Zach to move it again today?",
               "slots": {"who": "Zach", "how": "move it"}, "preface": "",
               "rationale": "x"}
    fresh = {"question": "Is it about the kitchen?",
             "slots": {"where": "the kitchen"}, "preface": "", "rationale": "x"}
    mock = MockBackend(responder=_format_responder([farming, fresh]))
    action = await _ask(
        mock, established={("who", "Zach"), ("how", "move it")}
    )
    assert action.content == "Is it about the kitchen?"
    assert any("already confirmed" in str(m) for m in mock.calls[2])


async def test_gate4_exempts_split_questions() -> None:
    # Tied leaders NEED a separating question even though both are "known".
    split_q = {"question": "Do you need Zach to move it?",
               "slots": {"who": "Zach", "how": "move it"}, "preface": "",
               "rationale": "x"}
    action = await _ask(
        MockBackend(responder=_format_responder([split_q])),
        directive="split",
        split_pair=("move it", "clean it"),
        established={("who", "Zach"), ("how", "move it")},
    )
    assert action.content == "Do you need Zach to move it?"



async def test_repeat_gate_rejects_and_reprompts() -> None:
    repeat = {"question": "Is it a picture?", "slots": {"what": "a picture"},
              "preface": "", "rationale": "x"}
    fresh = {"question": "Is it something heavy?", "slots": {"what": "something heavy"},
             "preface": "", "rationale": "x"}
    mock = MockBackend(responder=_format_responder([repeat, fresh]))
    action = await _ask(mock, asked=["Is it a picture?"])
    assert action.content == "Is it something heavy?"
    # The correction loop told the model what it repeated.
    assert any("already asked" in str(m) for m in mock.calls[2])


async def test_persistent_repeats_raise_for_engine_recovery() -> None:
    repeat = {"question": "Is it a picture?", "slots": {"what": "a picture"},
              "preface": "", "rationale": "x"}
    mock = MockBackend(responder=_format_responder([repeat]))
    with pytest.raises(ReasonerError):
        await _ask(mock, asked=["Is it a picture?"])


async def test_audit_failures_reprompt_until_clean() -> None:
    bad = {"question": "Is it inside or outside?", "slots": {"what": "a picture"},
           "rationale": "x"}
    good = {"question": "Is it a picture?", "slots": {"what": "a picture"},
            "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([bad, good])))
    assert action.content == "Is it a picture?"


async def test_clean_question_with_no_slots_is_best_effort_accepted() -> None:
    # A novel, well-formed question whose tags all fail still beats a stall —
    # accepted with empty slots (the answer scores nothing).
    odd = {"question": "Is it about the garden gnome?", "slots": {"who": "Aaron"},
           "preface": "", "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([odd])))
    assert action.content == "Is it about the garden gnome?"
    assert action.slots == {}
    assert action.rationale.startswith("(best-effort)")


# --------------------------------------------------------------------- flip


def _flip_responder(payloads: list[dict]):
    """Respond to each flip call with the next payload (last one repeats)."""
    state = {"n": 0}

    def responder(msgs: list) -> str:
        out = payloads[min(state["n"], len(payloads) - 1)]
        state["n"] += 1
        return json.dumps(out)

    return responder


async def test_flip_is_one_shot_and_carries_its_origin() -> None:
    flipped = {"question": "Do you want Zach to move it for you?",
               "slots": {"who": "Zach"}}
    mock = MockBackend(responder=_flip_responder([flipped]))
    action = await Reasoner(mock).flip(
        question="Do you want to move it for Zach?", board=_BOARD, focus="how"
    )
    assert action.kind == "query"
    assert action.content == "Do you want Zach to move it for you?"
    assert action.flipped_from == "Do you want to move it for Zach?"
    assert action.slots["who"] == "Zach"
    assert len(mock.calls) == 1  # no deliberate phase — a trigger, not a turn
    assert mock.think_args == [False]


async def test_flip_rejects_an_unchanged_echo() -> None:
    same = {"question": "Do you want to move it for Zach?", "slots": {"who": "Zach"}}
    flipped = {"question": "Do you want Zach to move it for you?",
               "slots": {"who": "Zach"}}
    mock = MockBackend(responder=_flip_responder([same, flipped]))
    action = await Reasoner(mock).flip(
        question="Do you want to move it for Zach?", board=_BOARD
    )
    assert action.content == "Do you want Zach to move it for you?"
    assert any("same question" in str(m) for m in mock.calls[1])


async def test_flip_mirror_note_names_both_buckets() -> None:
    flipped = {"question": "Do you want Zach to move it for you?",
               "slots": {"who": "Zach"}}
    mock = MockBackend(responder=_flip_responder([flipped]))
    await Reasoner(mock).flip(
        question="Do you want to move it for Zach?",
        board=_BOARD,
        direction="me_for_them",
        mirror="them_for_me",
    )
    sent = str(mock.calls[0])
    assert facets.DIRECTION_BUCKETS["me_for_them"] in sent
    assert facets.DIRECTION_BUCKETS["them_for_me"] in sent


async def test_flip_raises_when_no_usable_flip_emerges() -> None:
    same = {"question": "Do you want to move it for Zach?", "slots": {}}
    with pytest.raises(ReasonerError):
        await Reasoner(MockBackend(responder=_flip_responder([same]))).flip(
            question="Do you want to move it for Zach?", board=_BOARD
        )


# ------------------------------------------------------------------ refines


async def test_refines_tag_is_board_anchored() -> None:
    tagged = {"question": "Is it more like a framed photo?",
              "slots": {"what": "a framed photo"},
              "refines": {"what": "a picture"}, "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([tagged])))
    assert action.refines == {"what": "a picture"}  # the parent exists


async def test_refines_to_an_unknown_parent_is_dropped() -> None:
    ghost = {"question": "Is it more like a framed photo?",
             "slots": {"what": "a framed photo"},
             "refines": {"what": "a daguerreotype"}, "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([ghost])))
    assert action.refines == {}  # a refines tag can never resurrect a value


# ------------------------------------------------------------------- verify


async def test_verify_is_one_shot_and_anchored() -> None:
    good = {"question": "Is it Zach you need help from?"}
    mock = MockBackend(responder=_flip_responder([good]))
    action = await Reasoner(mock).verify(category="who", value="Zach", board=_BOARD)
    assert action.kind == "query" and action.verify is True
    assert action.content == "Is it Zach you need help from?"
    assert action.slots == {"who": "Zach"}
    assert action.focus == "who"
    assert action.preface.startswith("Just to double-check")
    assert len(mock.calls) == 1  # no deliberate phase — a check, not a turn
    assert mock.think_args == [False]


async def test_verify_must_say_the_value_out_loud() -> None:
    vague = {"question": "Are you sure about that?"}
    good = {"question": "Is it Zach you mean?"}
    mock = MockBackend(responder=_flip_responder([vague, good]))
    action = await Reasoner(mock).verify(category="who", value="Zach", board=_BOARD)
    assert action.content == "Is it Zach you mean?"
    assert any("out loud" in str(m) for m in mock.calls[1])


# ------------------------------------------------------------------ preface


async def test_preface_is_normalized_to_flow_into_the_question() -> None:
    raw = {"question": "Is it a picture?", "slots": {"what": "a picture"},
           "preface": "Okay, not a drink then.", "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([raw])))
    assert action.preface == "Okay, not a drink then —"  # period → em dash


async def test_preface_dropped_when_it_restates_the_question() -> None:
    raw = {"question": "Is it a picture?", "slots": {"what": "a picture"},
           "preface": "I wonder if it is a picture", "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([raw])))
    assert action.preface == ""  # restating lead-in is noise, not a lead-in


async def test_preface_dropped_when_it_is_its_own_question() -> None:
    raw = {"question": "Is it a picture?", "slots": {"what": "a picture"},
           "preface": "Shall we keep going?", "rationale": "x"}
    action = await _ask(MockBackend(responder=_format_responder([raw])))
    assert action.preface == ""


# ------------------------------------------------------------- seed + think


async def test_structured_json_calls_force_thinking_off() -> None:
    # Regression: on a thinking model, leaving thinking ON for a json_mode call
    # lets the chain-of-thought eat the token budget and Ollama returns empty
    # content. Structured calls must force think=False.
    seeds = json.dumps(
        {"who": ["my son"], "what": ["a drink", "a snack"], "how": ["bring it"]}
    )
    mock = MockBackend(responder=lambda _m: seeds, thinking=True)
    await Reasoner(mock).seed_board(topic_label="My body")
    assert mock.think_args == [False]  # the single seed call forced thinking off


async def test_seed_board_rejects_a_boardless_payload() -> None:
    mock = MockBackend(responder=lambda _m: json.dumps({"who": ["Zach"]}))
    with pytest.raises(ReasonerError):  # "what" empty and too few values
        await Reasoner(mock).seed_board(topic_label="My body")


async def test_expand_slots_anchors_to_the_note() -> None:
    # Every value must come from the NOTE'S OWN WORDS. This test used to assert
    # that "a drink" was creditable here because it sits on the board, even
    # though the note never says it — that board shortcut is the W2-M defect
    # (it let "right side paralysis" credit `what: pain` at +2.0), so the
    # contract is now note-anchoring only.
    payload = json.dumps(
        {"slots": {"what": ["a drink"], "who": ["Aaron"], "where": ["the kitchen"]}}
    )
    out = await Reasoner(MockBackend(responder=lambda _m: payload)).expand_slots(
        topic_label="My body",
        context="She keeps pointing at the kitchen, maybe her cup.",
        board=_BOARD,
        history=[],
    )
    assert out.get("where") == ["the kitchen"]  # the note says it
    assert "what" not in out  # on the board, but NOT in the note
    assert "who" not in out  # the hallucinated person was dropped


# ------------------------------------------- W2-M: caregiver-note fidelity


async def test_a_note_never_credits_a_word_it_does_not_say() -> None:
    # THE 09-01 defect: the note said "right side paralysis"; the model emitted
    # `what: pain` because "pain" was already on the board, and it was credited
    # at CONTEXT_POINTS (+2.0) — instantly locking the slot on a word the
    # caregiver never wrote, then contradicted by the engine's own verify turn.
    board = facets.seed_board({"what": ["pain", "tingling"], "where": ["right side"]})
    mock = MockBackend(
        responder=lambda msgs: json.dumps({"slots": {"what": ["pain"]}})
    )
    got = await Reasoner(mock).expand_slots(
        topic_label="My body",
        context="right side paralysis",
        board=board,
        history=[],
    )
    assert got == {}, "a value absent from the note must never be credited"


async def test_a_note_keeps_its_own_precision() -> None:
    # And the other half: "Pain in right calf" must not have its location
    # deleted. W2-K stopped `right calf` folding onto `right side`; this pins
    # that the note path preserves it end to end.
    board = facets.seed_board({"what": ["pain"], "where": ["right side", "legs"]})
    mock = MockBackend(
        responder=lambda msgs: json.dumps(
            {"slots": {"what": ["pain"], "where": ["right calf"]}}
        )
    )
    got = await Reasoner(mock).expand_slots(
        topic_label="My body",
        context="Pain in right calf",
        board=board,
        history=[],
    )
    assert got["where"] == ["right calf"]   # NOT folded to "right side"
    assert got["what"] == ["pain"]          # the note does say "pain" here


async def test_a_note_still_folds_a_pure_rewording() -> None:
    # Anchoring must not fragment the board: the note's words map onto an
    # existing contender when they are genuinely the same thing.
    board = facets.seed_board({"what": ["a picture"]})
    mock = MockBackend(
        responder=lambda msgs: json.dumps({"slots": {"what": ["the picture"]}})
    )
    got = await Reasoner(mock).expand_slots(
        topic_label="My people",
        context="she keeps pointing at the picture",
        board=board,
        history=[],
    )
    assert got["what"] == ["a picture"]


async def test_a_note_mints_what_the_board_has_never_seen() -> None:
    board = facets.seed_board({"what": ["a drink"]})
    mock = MockBackend(
        responder=lambda msgs: json.dumps({"slots": {"what": ["water cup"]}})
    )
    got = await Reasoner(mock).expand_slots(
        topic_label="My body",
        context="she is reaching for her water cup",
        board=board,
        history=[],
    )
    # Minted on its own terms rather than folded into the vaguer "a drink" —
    # the specific is the reason the caregiver bothered to type it.
    assert got["what"] == ["water cup"]


# --------------------------------------------- the drill contract (W2-Z, 1m)


def _where_board() -> facets.Board:
    """The 09-12 board at the stall: 'arm' confirmed, siblings still listed."""
    board = facets.empty_board()
    board["where"] = {"arm": 1.0, "hand": 0.0, "leg": 0.0, "foot": 0.0}
    return board


def test_drill_rejects_re_asserting_the_parent() -> None:
    # A drill of "arm" answered with "arm" is a re-assertion, not a narrowing.
    # It used to be stamped drill_parent="arm": the value recorded as a
    # refinement of itself.
    reason = _drill_violation("arm", _where_board(), "where", None)
    assert reason is not None
    assert "arm" in reason and "more specific" in reason.lower()


def test_drill_rejects_a_sibling() -> None:
    # The exact 09-12 failure: a drill of "arm" came back "hand". Caught then
    # only because that question happened to have been asked already; had the
    # model said "leg", "leg refines arm" would have entered the belief.
    reason = _drill_violation("hand", _where_board(), "where", None)
    assert reason is not None
    assert "same level" in reason


def test_drill_accepts_a_genuine_narrowing() -> None:
    # The question the round needed and never asked.
    assert _drill_violation("wrist", _where_board(), "where", None) is None


def test_drill_correction_names_the_parent() -> None:
    # The point of the message: today's rejections say what NOT to do and never
    # what to do, which is how the model ended up cycling between blocked moves.
    for value in ("arm", "hand"):
        reason = _drill_violation(value, _where_board(), "where", None)
        assert reason is not None and "'arm'" in reason


def test_drill_contract_is_vacuous_with_an_empty_slot() -> None:
    # Nothing to narrow below, so any value is fresh coverage, not a violation.
    assert _drill_violation("wrist", facets.empty_board(), "where", None) is None
