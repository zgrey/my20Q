"""LLM-driven reasoning controller for a round.

The LLM does *language*; ``agent/hypotheses.py`` + ``agent/dialogue.py`` do the
*control*. Three focused calls replace the old single-shot proposer:

- :meth:`Reasoner.seed_hypotheses` — the round's candidate-need set (belief prior).
- :meth:`Reasoner.ask` — the next most-discriminating yes/no question, with the
  ``yes_ids`` it would split (validated by the format auditor *and* a balance
  check, with a bounded correction loop).
- :meth:`Reasoner.synthesize` — phrase the leading need as a first-person
  utterance.

Each output is validated and sanitized; anything unusable raises
``ReasonerError`` so the engine degrades to deterministic fallback mode.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from my20q.agent import prompts
from my20q.agent.auditor import audit_query
from my20q.agent.hypotheses import Hypothesis, is_balanced
from my20q.agent.safety import sanitize_llm_text, sanitize_utterance
from my20q.llm.base import LLMBackend, LLMUnavailable

log = logging.getLogger(__name__)

#: How many times a query failing the audit/balance check is re-proposed.
MAX_AUDIT_RETRIES = 2
#: Bounds on the seed candidate set.
MIN_HYPOTHESES = 4
MAX_HYPOTHESES = 10


class ReasonerError(RuntimeError):
    """Raised when LLM output cannot be used — the engine should fall back."""


@dataclass
class ReasonerAction:
    kind: Literal["query", "synthesis"]
    content: str
    rationale: str = ""
    #: Short patient-facing spoken lead-in read just before a query.
    preface: str = ""
    #: For a query: the candidate ids this question would answer "yes" for.
    yes_ids: list[str] = field(default_factory=list)
    #: For a synthesis: the hypothesis id it was built from (so a rejection can
    #: eliminate it on belief replay).
    hyp_id: str = ""


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

    # --------------------------------------------------------------- helpers

    async def _chat_json(self, messages: list, max_tokens: int) -> dict:
        try:
            raw = await self.llm.chat(messages, max_tokens=max_tokens, json_mode=True)
        except LLMUnavailable as exc:
            raise ReasonerError(f"llm unreachable: {exc}") from exc
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        data = _extract_json(raw)
        if data is None:
            raise ReasonerError(f"invalid JSON from LLM: {raw!r}")
        return data

    # ------------------------------------------------------------- the calls

    async def seed_hypotheses(
        self,
        *,
        topic_label: str,
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> list[Hypothesis]:
        """Propose the round's candidate needs (the belief's prior)."""
        if on_phase is not None:
            on_phase("thinking")
        messages = prompts.seed_messages(
            topic_label,
            seed_context=seed_context,
            profile_context=profile_context,
            topic_hint=topic_hint,
            emotional_state=emotional_state,
        )
        data = await self._chat_json(messages, max_tokens=400)
        items = data.get("hypotheses")
        if not isinstance(items, list):
            raise ReasonerError(f"seed: no hypotheses list in {data!r}")
        needs: list[str] = []
        seen: set[str] = set()
        for it in items:
            if not isinstance(it, str):
                continue
            clean = sanitize_llm_text(it)
            key = clean.casefold()
            if clean and key not in seen:
                seen.add(key)
                needs.append(clean)
        needs = needs[:MAX_HYPOTHESES]
        if len(needs) < MIN_HYPOTHESES:
            raise ReasonerError(f"seed: too few usable hypotheses ({len(needs)})")
        return [Hypothesis(id=f"h{i + 1}", need=n) for i, n in enumerate(needs)]

    async def ask(
        self,
        *,
        topic_label: str,
        candidates: list[tuple[str, str, float]],
        weights: dict[str, float],
        history: list[dict],
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Propose the next discriminating yes/no question over ``candidates``.

        Re-prompts (bounded) when the question fails the format audit or the
        ``yes_ids`` don't split the live belief informatively.
        """
        live_ids = {hid for hid, _, _ in candidates}
        corrections: list[str] = []
        best: ReasonerAction | None = None
        for attempt in range(MAX_AUDIT_RETRIES + 1):
            if on_phase is not None:
                on_phase("re-asking" if attempt else "thinking")
            messages = prompts.ask_messages(
                topic_label,
                candidates,
                history,
                seed_context=seed_context,
                profile_context=profile_context,
                topic_hint=topic_hint,
                emotional_state=emotional_state,
                corrections=corrections,
            )
            data = await self._chat_json(messages, max_tokens=240)
            question = data.get("question", "")
            if not isinstance(question, str) or not question.strip():
                raise ReasonerError("ask: empty question")
            cleaned = sanitize_llm_text(question)
            if not cleaned:
                corrections.append("question was unusable after sanitizing; rephrase")
                continue
            raw_ids = data.get("yes_ids", [])
            yes_ids = (
                [str(x) for x in raw_ids if str(x) in live_ids]
                if isinstance(raw_ids, list)
                else []
            )
            yes_set = set(yes_ids)
            action = self._ask_action(cleaned, yes_ids, data.get("preface", ""), data)
            best = action

            verdict = audit_query(cleaned)
            if not verdict.ok:
                corrections.append(verdict.reason)
                continue
            if not yes_set or yes_set == live_ids:
                corrections.append(
                    "yes_ids must mark SOME but not ALL candidates — a real split"
                )
                continue
            if not is_balanced(weights, yes_set):
                corrections.append(
                    "the split was too lopsided; ask something about half would "
                    "answer yes"
                )
                continue
            return action

        # Retries exhausted — accept a best effort that at least splits the set,
        # rather than dead-ending the round; flag it for the caregiver.
        if best is not None and best.yes_ids and set(best.yes_ids) != live_ids:
            best.rationale = f"(imperfect split accepted) {best.rationale}".strip()
            return best
        raise ReasonerError("ask: could not produce a discriminating question")

    async def synthesize(
        self,
        *,
        topic_label: str,
        leading_need: str,
        history: list[dict],
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Phrase the leading need as a confirmable first-person utterance."""
        if on_phase is not None:
            on_phase("thinking")
        messages = prompts.synthesize_messages(
            topic_label,
            leading_need,
            history,
            seed_context=seed_context,
            profile_context=profile_context,
            topic_hint=topic_hint,
            emotional_state=emotional_state,
        )
        data = await self._chat_json(messages, max_tokens=120)
        utterance = data.get("utterance", "")
        cleaned = sanitize_utterance(utterance) if isinstance(utterance, str) else ""
        if not cleaned:  # fall back to the leading need itself
            cleaned = sanitize_utterance(leading_need)
        if not cleaned:
            raise ReasonerError(f"synthesize: unusable utterance {data!r}")
        return ReasonerAction(
            kind="synthesis",
            content=cleaned,
            rationale="Proposed from the leading candidate need.",
        )

    def _ask_action(
        self, question: str, yes_ids: list[str], preface_raw: object, data: dict
    ) -> ReasonerAction:
        rationale = str(data.get("rationale", "") or "")[:240]
        preface = sanitize_llm_text(str(preface_raw or ""))[:100]
        if preface.casefold() == question.casefold():
            preface = ""
        return ReasonerAction(
            kind="query",
            content=question,
            rationale=rationale,
            preface=preface,
            yes_ids=list(yes_ids),
        )
