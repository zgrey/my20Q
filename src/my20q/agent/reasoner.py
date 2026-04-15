"""LLM-driven 20 Questions agent.

Given the game category and accumulated history, asks the LLM to propose
the next action (a yes/no question or a specific guess) as strict JSON.
Output is validated and sanitized; malformed or unsafe output raises
`ReasonerError` so the caller can fall back gracefully.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from my20q.agent import prompts
from my20q.agent.safety import sanitize_llm_text
from my20q.llm.base import LLMBackend, LLMUnavailable

log = logging.getLogger(__name__)


class ReasonerError(RuntimeError):
    """Raised when the LLM output cannot be used. Caller should fall back."""


@dataclass
class ReasonerAction:
    kind: Literal["question", "guess"]
    content: str
    rationale: str = ""


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(raw: str) -> dict | None:
    """Best-effort JSON extraction when the model wraps output in prose."""
    match = _JSON_OBJECT_RE.search(raw)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class Reasoner:
    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def next_action(
        self,
        *,
        category_label: str,
        history: list[dict],
        turn: int,
        max_turns: int,
        final: bool = False,
        seed_context: str = "",
        category_hint: str = "",
    ) -> ReasonerAction:
        messages = prompts.game_reason_messages(
            category_label,
            history,
            turn,
            max_turns,
            final=final,
            seed_context=seed_context,
            category_hint=category_hint,
        )
        try:
            raw = await self.llm.chat(messages, max_tokens=200, json_mode=True)
        except LLMUnavailable as exc:
            raise ReasonerError(f"llm unreachable: {exc}") from exc

        data: dict | None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = None
        except json.JSONDecodeError:
            data = _extract_json(raw)

        if data is None:
            raise ReasonerError(f"invalid JSON from LLM: {raw!r}")

        kind = data.get("action")
        content = data.get("content", "")
        rationale = data.get("rationale", "") or ""
        if kind not in ("question", "guess"):
            raise ReasonerError(f"invalid action kind: {kind!r}")
        if not isinstance(content, str) or not content.strip():
            raise ReasonerError("empty content")

        cleaned = sanitize_llm_text(content)
        if not cleaned:
            raise ReasonerError(f"sanitizer rejected content: {content!r}")

        if final and kind != "guess":
            # Enforce "final turn must be a guess".
            raise ReasonerError(f"final-turn action was not a guess: {kind}")

        return ReasonerAction(kind=kind, content=cleaned, rationale=str(rationale))
