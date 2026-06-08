"""The ask is single-call for ordinary models, two-phase for thinking models.

A thinking model (gemma4) would starve the JSON if it reasoned inside the single
constrained call, so the reasoner splits the ask into a free-form *deliberate*
pass and a cheap *format* pass — but only when the backend reports the thinking
capability. Everything else keeps the fast single call. See
``docs/design/reasoning-retro.md``.
"""

from __future__ import annotations

import json

from my20q.agent.reasoner import Reasoner
from my20q.llm import MockBackend

_GOOD = json.dumps(
    {"question": "Is it inside the house?", "yes_ids": ["h1"], "rationale": "x"}
)
_CANDIDATES = [("h1", "it is inside", 0.5), ("h2", "it is outside", 0.5)]
_WEIGHTS = {"h1": 0.5, "h2": 0.5}


async def test_non_thinking_model_uses_a_single_call() -> None:
    mock = MockBackend(responder=lambda _m: _GOOD)  # thinking=False by default
    reasoner = Reasoner(mock)
    action = await reasoner.ask(
        topic_label="My body",
        candidates=_CANDIDATES,
        weights=_WEIGHTS,
        history=[],
    )
    assert action.content == "Is it inside the house?"
    assert len(mock.calls) == 1  # one fast JSON call — no deliberate phase


async def test_structured_json_calls_force_thinking_off() -> None:
    # Regression: on a thinking model, leaving thinking ON for a json_mode call
    # lets the chain-of-thought eat the token budget and Ollama returns empty
    # content (seed/synthesize/expand all degraded). They must force think=False.
    seeds = json.dumps({"hypotheses": ["I am tired", "I am thirsty", "I hurt", "I am sad"]})
    mock = MockBackend(responder=lambda _m: seeds, thinking=True)
    await Reasoner(mock).seed_hypotheses(topic_label="My feelings")
    assert mock.think_args == [False]  # the single seed call forced thinking off


async def test_thinking_model_uses_two_phase_deliberate_then_format() -> None:
    seen_systems: list[str] = []

    def responder(msgs: list) -> str:
        system = msgs[0]["content"]
        seen_systems.append(system)
        if "Convert a drafted question" in system:  # FORMAT phase
            return _GOOD
        return "I'll ask whether it is inside the house."  # DELIBERATE draft

    mock = MockBackend(responder=responder, thinking=True)
    reasoner = Reasoner(mock)
    action = await reasoner.ask(
        topic_label="My body",
        candidates=_CANDIDATES,
        weights=_WEIGHTS,
        history=[],
    )
    assert action.content == "Is it inside the house?"
    assert len(mock.calls) == 2  # deliberate + format
    assert any("Work out the SINGLE best" in s for s in seen_systems)  # deliberated
    assert any("Convert a drafted question" in s for s in seen_systems)  # formatted
