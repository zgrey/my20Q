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
from my20q.llm.base import ChatResult, LLMBackend, LLMUnavailable

log = logging.getLogger(__name__)

#: How many times a query failing the audit/balance check is re-proposed.
MAX_AUDIT_RETRIES = 2
#: Bounds on the seed candidate set.
MIN_HYPOTHESES = 4
MAX_HYPOTHESES = 10

# Output ceilings (num_predict). These are NOT a cost limit — inference is local
# and free — they only guard against a runaway/looping generation hanging the
# live cockpit. Because every call uses format=json, Ollama stops at the closing
# brace, so a generous ceiling never adds latency to a normal response; it only
# avoids truncating a longer one into invalid JSON. Kept roomy so the model can
# produce its best questioning/reasoning without being clipped.
SEED_MAX_TOKENS = 1024
# The ask is two phases: deliberate gets a big ceiling so a thinking model can
# reason at length before drafting; format is a cheap constrained JSON call.
DELIBERATE_MAX_TOKENS = 2048
FORMAT_MAX_TOKENS = 512
SYNTH_MAX_TOKENS = 256
EXPAND_MAX_TOKENS = 512
CRITIQUE_MAX_TOKENS = 512
SUMMARY_MAX_TOKENS = 256
#: Cap on augmented deliberate→critique→refine passes per question.
MAX_EFFORT = 3


def _clip(text: str, limit: int = 160) -> str:
    """Collapse whitespace and truncate — for short trace lines."""
    out = " ".join(text.split())
    return out[: limit - 1] + "…" if len(out) > limit else out


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
    #: Labelled reasoning trace for the cockpit — each {"kind": "thinking" |
    #: "strategy", "text": ...}. "thinking" = a thinking model's summarized
    #: opaque reasoning; "strategy" = the explicit deliberate/critique passes.
    trace: list[dict] = field(default_factory=list)


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

    async def _chat_json(
        self, messages: list, max_tokens: int, *, think: bool | None = None
    ) -> dict:
        try:
            raw = await self.llm.chat(
                messages, max_tokens=max_tokens, json_mode=True, think=think
            )
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
        data = await self._chat_json(messages, max_tokens=SEED_MAX_TOKENS)
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
        effort: int = 1,
        augmented: bool = False,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Propose the next discriminating yes/no question over ``candidates``.

        Two decoupled phases: **deliberate** (free-form reasoning — a thinking
        model thinks at length, no JSON budget pressure) then **format** (a cheap
        thinking-off call → strict JSON). In augmented mode ``effort`` adds
        deliberate→critique→refine passes; a thinking model's hidden reasoning is
        summarized into the trace either way. Re-prompts (bounded) on a bad
        question.
        """
        ctx = {
            "seed_context": seed_context,
            "profile_context": profile_context,
            "topic_hint": topic_hint,
            "emotional_state": emotional_state,
        }
        live_ids = {hid for hid, _, _ in candidates}
        corrections: list[str] = []
        trace: list[dict] = []
        captured_thinking = False
        best: ReasonerAction | None = None
        for attempt in range(MAX_AUDIT_RETRIES + 1):
            if on_phase is not None:
                on_phase("re-asking" if attempt else "thinking")
            # Phase 1 — deliberate (backend-default thinking; roomy; no JSON).
            result = await self._deliberate(
                topic_label, candidates, history, corrections, ctx
            )
            draft = result.content.strip()
            # Surface the model's opaque reasoning once, summarized.
            if result.thinking and not captured_thinking:
                captured_thinking = True
                summary = await self.summarize_thinking(result.thinking)
                if summary:
                    trace.append({"kind": "thinking", "text": summary})
            if not draft:
                corrections.append("the reasoning produced no question; state one plainly")
                continue
            if augmented:
                trace.append({"kind": "strategy", "text": f"draft: {_clip(draft)}"})
            # Augmented refinement: critique the draft and re-deliberate.
            for _ in range(max(0, min(effort, MAX_EFFORT) - 1)):
                crit = await self.critique(candidates, history, draft)
                if not crit:
                    break
                trace.append({"kind": "strategy", "text": f"critique: {crit}"})
                refined = await self._deliberate(
                    topic_label, candidates, history,
                    [*corrections, f"a critique of your last draft: {crit}"], ctx,
                )
                if refined.content.strip():
                    draft = refined.content.strip()
                    trace.append({"kind": "strategy", "text": f"refined: {_clip(draft)}"})
            # Phase 2 — format the draft into strict JSON (thinking forced off).
            data = await self._chat_json(
                prompts.format_question_messages(candidates, draft),
                max_tokens=FORMAT_MAX_TOKENS,
                think=False,
            )
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
            action.trace = trace
            return action

        # Retries exhausted — accept a best effort that at least splits the set,
        # rather than dead-ending the round; flag it for the caregiver.
        if best is not None and best.yes_ids and set(best.yes_ids) != live_ids:
            best.rationale = f"(imperfect split accepted) {best.rationale}".strip()
            best.trace = trace
            return best
        raise ReasonerError("ask: could not produce a discriminating question")

    async def _deliberate(
        self,
        topic_label: str,
        candidates: list[tuple[str, str, float]],
        history: list[dict],
        corrections: list[str],
        ctx: dict,
    ) -> ChatResult:
        """One free-form reasoning pass → a draft question (+ thinking channel)."""
        messages = prompts.deliberate_messages(
            topic_label, candidates, history, corrections=corrections, **ctx
        )
        try:
            return await self.llm.chat_full(
                messages, max_tokens=DELIBERATE_MAX_TOKENS, json_mode=False
            )
        except LLMUnavailable as exc:
            raise ReasonerError(f"llm unreachable: {exc}") from exc

    async def critique(
        self,
        candidates: list[tuple[str, str, float]],
        history: list[dict],
        draft: str,
    ) -> str:
        """Short critique of a drafted question (augmented refinement). Lenient."""
        try:
            out = await self.llm.chat(
                prompts.critique_messages(candidates, history, draft),
                max_tokens=CRITIQUE_MAX_TOKENS,
                think=False,
            )
        except LLMUnavailable:
            return ""
        return _clip(out, 200)

    async def summarize_thinking(self, thinking: str) -> str:
        """Condense a thinking model's opaque reasoning to a few short points."""
        try:
            out = await self.llm.chat(
                prompts.summarize_thinking_messages(thinking[:4000]),
                max_tokens=SUMMARY_MAX_TOKENS,
                think=False,
            )
        except LLMUnavailable:
            return ""
        return out.strip()[:400]

    async def zoom(
        self,
        *,
        topic_label: str,
        parent_need: str,
        history: list[dict],
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> list[str]:
        """Finer, self-contained sub-needs of a confirmed need (zoom deeper).

        Best-effort: returns [] when the need is already specific enough or the
        call fails, so the round synthesizes instead of descending.
        """
        if on_phase is not None:
            on_phase("thinking")
        messages = prompts.zoom_messages(
            topic_label,
            parent_need,
            history,
            seed_context=seed_context,
            profile_context=profile_context,
            topic_hint=topic_hint,
            emotional_state=emotional_state,
        )
        try:
            data = await self._chat_json(messages, max_tokens=EXPAND_MAX_TOKENS, think=False)
        except ReasonerError:
            return []
        items = data.get("hypotheses")
        if not isinstance(items, list):
            return []
        seen = {parent_need.casefold()}
        out: list[str] = []
        for it in items:
            if not isinstance(it, str):
                continue
            clean = sanitize_llm_text(it)
            key = clean.casefold()
            if clean and key not in seen:
                seen.add(key)
                out.append(clean)
        return out[:6]

    async def expand_hypotheses(
        self,
        *,
        topic_label: str,
        context: str,
        existing: list[tuple[str, str]],
        history: list[dict],
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> tuple[list[str], list[str]]:
        """New needs + confirmed-existing ids implied by a caregiver note.

        Never raises — context is additive, so a failure just means "no change"
        and the round carries on. Returns ``(new_needs, boost_ids)``: sanitized
        needs not already present (capped so the set stays bounded), and the ids
        of existing candidates the note confirms.
        """
        if on_phase is not None:
            on_phase("thinking")
        messages = prompts.expand_messages(
            topic_label,
            context,
            existing,
            history,
            seed_context=seed_context,
            profile_context=profile_context,
            topic_hint=topic_hint,
            emotional_state=emotional_state,
        )
        try:
            data = await self._chat_json(messages, max_tokens=EXPAND_MAX_TOKENS)
        except ReasonerError:
            return [], []
        existing_ids = {hid for hid, _ in existing}
        seen = {need.casefold() for _, need in existing}
        new_needs: list[str] = []
        items = data.get("hypotheses")
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, str):
                    continue
                clean = sanitize_llm_text(it)
                key = clean.casefold()
                if clean and key not in seen:
                    seen.add(key)
                    new_needs.append(clean)
        room = max(0, MAX_HYPOTHESES - len(existing))
        new_needs = new_needs[:room]
        raw_boost = data.get("boost_ids")
        boost_ids = (
            [str(x) for x in raw_boost if str(x) in existing_ids]
            if isinstance(raw_boost, list)
            else []
        )
        return new_needs, boost_ids

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
        data = await self._chat_json(messages, max_tokens=SYNTH_MAX_TOKENS)
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
