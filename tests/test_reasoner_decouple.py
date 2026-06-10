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
from my20q.agent.reasoner import Reasoner, ReasonerError
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
    # Values must come from the note's words or match an existing contender —
    # the same anti-hallucination rule as questions. "a drink" is on the board
    # (confirmed-by-note), "Aaron" is in neither the note nor the board.
    payload = json.dumps(
        {"slots": {"what": ["a drink"], "who": ["Aaron"], "where": ["the kitchen"]}}
    )
    out = await Reasoner(MockBackend(responder=lambda _m: payload)).expand_slots(
        topic_label="My body",
        context="She keeps pointing at the kitchen, maybe her cup.",
        board=_BOARD,
        history=[],
    )
    assert out.get("what") == ["a drink"]
    assert out.get("where") == ["the kitchen"]
    assert "who" not in out  # the hallucinated person was dropped
