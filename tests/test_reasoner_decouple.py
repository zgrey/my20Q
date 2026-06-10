"""The ask is two-phase for EVERY model: deliberate (free-form) then format.

Splitting the ask into a free-form *deliberate* pass and a cheap *format* pass
keeps a thinking model's chain-of-thought from starving the JSON, and gives an
ordinary model an open reasoning step too (it used to answer in one cramped JSON
call). The format pass salvages straight from the draft if it comes up empty. See
``docs/design/reasoning-retro.md``.
"""

from __future__ import annotations

import json

from my20q.agent.reasoner import Reasoner
from my20q.llm import MockBackend

_GOOD = json.dumps(
    {"question": "Is it inside the house?", "yes_ids": ["h1"], "rationale": "x"}
)
_CANDIDATES = [("h1", "it is inside", 0.0), ("h2", "it is outside", 0.0)]


async def test_ask_is_two_phase_for_every_model() -> None:
    # Even an ordinary (non-thinking) model now deliberates then formats.
    mock = MockBackend(responder=lambda _m: _GOOD)  # thinking=False by default
    action = await Reasoner(mock).ask(
        topic_label="My body", candidates=_CANDIDATES, history=[]
    )
    assert action.content == "Is it inside the house?"
    assert len(mock.calls) == 2  # deliberate + format, regardless of model


async def test_format_salvages_from_the_draft() -> None:
    # If the format pass yields nothing usable, the structured question is salvaged
    # straight from the deliberate draft — a flaky format never costs a whole turn.
    def responder(msgs: list) -> str:
        if "Convert a drafted question" in msgs[0]["content"]:  # FORMAT pass
            return "sorry, no idea"  # junk — not JSON
        return _GOOD  # the deliberate draft already carries the question JSON

    action = await Reasoner(MockBackend(responder=responder)).ask(
        topic_label="My body", candidates=_CANDIDATES, history=[]
    )
    assert action.content == "Is it inside the house?"


async def test_structured_json_calls_force_thinking_off() -> None:
    # Regression: on a thinking model, leaving thinking ON for a json_mode call
    # lets the chain-of-thought eat the token budget and Ollama returns empty
    # content (seed/synthesize/expand all degraded). They must force think=False.
    seeds = json.dumps({"hypotheses": ["I am tired", "I am thirsty", "I hurt", "I am sad"]})
    mock = MockBackend(responder=lambda _m: seeds, thinking=True)
    await Reasoner(mock).seed_hypotheses(topic_label="My feelings")
    assert mock.think_args == [False]  # the single seed call forced thinking off
