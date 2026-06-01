"""LLM-driven reasoner for a round.

Given the topic and the round's history, asks the LLM for the next
action — a yes/no `query` or a `synthesis` — as strict JSON. Output is
validated, sanitized, and (for queries) run through the format auditor:
a query that fails the audit triggers a bounded re-prompt loop with the
auditor's reason fed back to the model. Output that still cannot be used
raises `ReasonerError` so the engine degrades to fallback mode.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from my20q.agent import prompts
from my20q.agent.auditor import audit_query
from my20q.agent.safety import sanitize_llm_text, sanitize_utterance
from my20q.llm.base import LLMBackend, LLMUnavailable

log = logging.getLogger(__name__)

#: How many times a query failing the format audit is re-proposed.
MAX_AUDIT_RETRIES = 2


class ReasonerError(RuntimeError):
    """Raised when LLM output cannot be used — the engine should fall back."""


@dataclass
class ReasonerAction:
    kind: Literal["query", "synthesis"]
    content: str
    rationale: str = ""
    #: A short, patient-facing spoken lead-in read aloud just before the
    #: query — a distillation of the reasoning that varies turn to turn so the
    #: readout is less monotonous. Sanitized like any patient-facing string;
    #: empty when the model omitted it or it was rejected.
    preface: str = ""


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
        topic_label: str,
        history: list[dict],
        query_index: int,
        max_queries: int,
        final: bool = False,
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Propose the next action, re-prompting if a query fails the audit.

        `on_phase`, when given, is called with a short progress label
        ("thinking", "re-asking") — the API forwards these to the SSE
        channel so the cockpit can show live progress.
        """
        corrections: list[str] = []
        action: ReasonerAction | None = None
        for attempt in range(MAX_AUDIT_RETRIES + 1):
            if on_phase is not None:
                on_phase("re-asking" if attempt else "thinking")
            action = await self._propose(
                topic_label=topic_label,
                history=history,
                query_index=query_index,
                max_queries=max_queries,
                final=final,
                seed_context=seed_context,
                profile_context=profile_context,
                topic_hint=topic_hint,
                emotional_state=emotional_state,
                corrections=corrections,
            )
            if action.kind != "query":
                return action  # a synthesis is a statement — no format audit
            verdict = audit_query(action.content)
            if verdict.ok:
                if corrections:
                    action.rationale = (
                        f"(re-asked — {corrections[-1]}) {action.rationale}".strip()
                    )
                return action
            log.info("auditor rejected query (%s) — re-prompting", verdict.reason)
            corrections.append(verdict.reason)

        # Retries exhausted — accept the best effort rather than dead-end the
        # round, but flag it in the rationale so the caregiver sees it.
        assert action is not None
        log.warning(
            "auditor: query still failing after %d retries — accepting", MAX_AUDIT_RETRIES
        )
        action.rationale = f"(auditor: not a clean yes/no) {action.rationale}".strip()
        return action

    async def _propose(
        self,
        *,
        topic_label: str,
        history: list[dict],
        query_index: int,
        max_queries: int,
        final: bool,
        seed_context: str,
        profile_context: str,
        topic_hint: str,
        emotional_state: dict | None,
        corrections: list[str],
    ) -> ReasonerAction:
        """One LLM call → a validated, sanitized action (no audit)."""
        messages = prompts.reason_messages(
            topic_label,
            history,
            query_index,
            max_queries,
            final=final,
            seed_context=seed_context,
            profile_context=profile_context,
            topic_hint=topic_hint,
            emotional_state=emotional_state,
            corrections=corrections,
        )
        try:
            raw = await self.llm.chat(messages, max_tokens=240, json_mode=True)
        except LLMUnavailable as exc:
            raise ReasonerError(f"llm unreachable: {exc}") from exc

        try:
            data: dict | None = json.loads(raw)
            if not isinstance(data, dict):
                data = None
        except json.JSONDecodeError:
            data = _extract_json(raw)
        if data is None:
            raise ReasonerError(f"invalid JSON from LLM: {raw!r}")

        kind = data.get("action")
        content = data.get("content", "")
        rationale = str(data.get("rationale", "") or "")
        if kind not in ("query", "synthesis"):
            raise ReasonerError(f"invalid action: {kind!r}")
        if not isinstance(content, str) or not content.strip():
            raise ReasonerError("empty content")
        if final and kind != "synthesis":
            raise ReasonerError(f"final action must be a synthesis, got {kind!r}")

        cleaned = (
            sanitize_utterance(content)
            if kind == "synthesis"
            else sanitize_llm_text(content)
        )
        if not cleaned:
            raise ReasonerError(f"sanitizer rejected content: {content!r}")
        # Optional spoken lead-in. Patient-facing, so sanitized; kept short
        # (it prefaces, not replaces, the question) and never identical to it.
        preface = sanitize_llm_text(str(data.get("preface", "") or ""))[:100]
        if preface.casefold() == cleaned.casefold():
            preface = ""
        return ReasonerAction(
            kind=kind, content=cleaned, rationale=rationale[:240], preface=preface
        )
