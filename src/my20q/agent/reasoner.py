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
#: A proposed question whose yes_ids overlap a recent question's this much
#: (Jaccard) is treated as a near-repeat and re-prompted — this is what stops the
#: reasoner re-slicing the same group of candidates over and over.
REDUNDANCY_JACCARD = 0.8
#: How many recent queries the redundancy check compares against.
REDUNDANCY_WINDOW = 4

# Output ceilings (num_predict). These are NOT a cost limit — inference is local
# and free — they only guard against a runaway/looping generation hanging the
# live cockpit. Because every call uses format=json, Ollama stops at the closing
# brace, so a generous ceiling never adds latency to a normal response; it only
# avoids truncating a longer one into invalid JSON. Kept roomy so the model can
# produce its best questioning/reasoning without being clipped.
SEED_MAX_TOKENS = 1024
ASK_MAX_TOKENS = 512
# Two-phase ask (thinking models only): the deliberate phase is the REASONING
# phase — do NOT token-cap it, or a verbose thinking model gets truncated
# mid-thought and emits nothing (we then can't judge whether its reasoning was
# any good). -1 = generate until the model stops naturally; latency is bounded by
# the per-call timeout, which is the proper runaway guard. The format phase stays
# cheap and capped (it only emits the JSON).
DELIBERATE_MAX_TOKENS = -1
FORMAT_MAX_TOKENS = 512
SYNTH_MAX_TOKENS = 256
EXPAND_MAX_TOKENS = 512


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


def _jaccard(a: set[str], b: set[str]) -> float:
    """Overlap of two id sets in [0, 1]; 0 if either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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
        self, messages: list, max_tokens: int, *, think: bool | None = False
    ) -> dict:
        # Structured-JSON calls (seed, format, expand, synthesize) force thinking
        # OFF by default: on a thinking model a rich prompt makes the chain-of-
        # thought eat the whole num_predict budget before the JSON is emitted, so
        # Ollama returns empty content and the round wrongly degrades to fallback.
        # Free-form reasoning belongs in ask()'s separate `deliberate` phase.
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

    async def _thinking_model(self) -> bool:
        """Whether the active backend is a thinking model.

        Gates the two-phase ask: thinking models deliberate then format so the
        reasoning never starves the JSON; everything else keeps the fast
        single-call path. Backends without a probe (or a failing probe) are
        treated as non-thinking.
        """
        probe = getattr(self.llm, "is_thinking_model", None)
        if probe is None:
            return False
        try:
            return bool(await probe())
        except Exception:  # never let a probe failure break the ask
            return False

    # ------------------------------------------------------------- the calls

    async def seed_hypotheses(
        self,
        *,
        topic_label: str,
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        seed_universal_wants: bool = True,
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
            include_universal_wants=seed_universal_wants,
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
        anchored: bool = False,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Propose the next discriminating yes/no question over ``candidates``.

        A thinking model takes two decoupled phases — **deliberate** (free-form
        reasoning, no JSON budget pressure) then **format** (a cheap thinking-off
        call → strict JSON); every other model takes a single fast JSON call.
        Re-prompts (bounded) when the question fails the format audit or the
        ``yes_ids`` don't split the live belief informatively.
        """
        live_ids = {hid for hid, _, _ in candidates}
        corrections: list[str] = []
        best: ReasonerAction | None = None
        two_phase = await self._thinking_model()
        # Splits of recent questions — a new question that re-slices the same
        # group is a near-repeat (the "circular questioning" failure mode).
        recent_yes_sets = [
            {str(x) for x in h.get("yes_ids", [])}
            for h in history
            if h.get("kind") == "query" and h.get("yes_ids")
        ][-REDUNDANCY_WINDOW:]
        for attempt in range(MAX_AUDIT_RETRIES + 1):
            if on_phase is not None:
                on_phase("re-asking" if attempt else "thinking")
            if two_phase:
                # Phase 1 — deliberate (free-form; backend-default thinking; no
                # JSON), then Phase 2 — format the draft into strict JSON with
                # thinking forced off.
                deliberate_msgs = prompts.deliberate_messages(
                    topic_label,
                    candidates,
                    history,
                    seed_context=seed_context,
                    profile_context=profile_context,
                    topic_hint=topic_hint,
                    emotional_state=emotional_state,
                    corrections=corrections,
                    anchored=anchored,
                )
                try:
                    draft = await self.llm.chat(
                        deliberate_msgs,
                        max_tokens=DELIBERATE_MAX_TOKENS,
                        json_mode=False,
                    )
                except LLMUnavailable:
                    # A thinking model can spend the whole budget thinking and
                    # return EMPTY content (Ollama reports this as "empty
                    # response"); a big model never even finishes the thought.
                    # Don't kill the round — retry the draft with thinking OFF so
                    # we still get a question. A genuinely down backend will fail
                    # this retry too and surface as a ReasonerError.
                    draft = ""
                if not draft.strip():
                    try:
                        draft = await self.llm.chat(
                            deliberate_msgs,
                            max_tokens=DELIBERATE_MAX_TOKENS,
                            json_mode=False,
                            think=False,
                        )
                    except LLMUnavailable as exc:
                        raise ReasonerError(f"llm unreachable: {exc}") from exc
                if not draft.strip():
                    corrections.append(
                        "the reasoning produced no question; state one plainly"
                    )
                    continue
                data = await self._chat_json(
                    prompts.format_question_messages(candidates, draft),
                    max_tokens=FORMAT_MAX_TOKENS,
                    think=False,
                )
            else:
                messages = prompts.ask_messages(
                    topic_label,
                    candidates,
                    history,
                    seed_context=seed_context,
                    profile_context=profile_context,
                    topic_hint=topic_hint,
                    emotional_state=emotional_state,
                    corrections=corrections,
                    anchored=anchored,
                )
                data = await self._chat_json(messages, max_tokens=ASK_MAX_TOKENS)
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
            if any(_jaccard(yes_set, prev) >= REDUNDANCY_JACCARD for prev in recent_yes_sets):
                corrections.append(
                    "that question splits the SAME group as a recent one — open a "
                    "different dimension (a different feeling, or its cause), not a "
                    "reworded repeat"
                )
                continue
            return action

        # Retries exhausted — accept a best effort that at least splits the set,
        # rather than dead-ending the round; flag it for the caregiver.
        if best is not None and best.yes_ids and set(best.yes_ids) != live_ids:
            best.rationale = f"(imperfect split accepted) {best.rationale}".strip()
            return best
        raise ReasonerError("ask: could not produce a discriminating question")

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

    async def clarify(
        self,
        *,
        topic_label: str,
        leading_need: str,
        leader_id: str,
        history: list[dict],
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """A confirming/sharpening yes/no question about the leading need.

        The deepen phase: a frontrunner has emerged but not enough yes answers to
        synthesize, so we gather positive confirmation (and refine the detail)
        rather than guess early. The returned query carries ``yes_ids=[leader_id]``
        so a "yes" both counts toward the synthesis gate and reinforces the leader;
        a "no" is a soft down-weight that lets the round re-broaden.
        """
        corrections: list[str] = []
        for attempt in range(MAX_AUDIT_RETRIES + 1):
            if on_phase is not None:
                on_phase("re-asking" if attempt else "thinking")
            data = await self._chat_json(
                prompts.clarify_messages(
                    topic_label,
                    leading_need,
                    history,
                    seed_context=seed_context,
                    profile_context=profile_context,
                    topic_hint=topic_hint,
                    emotional_state=emotional_state,
                    corrections=corrections,
                ),
                max_tokens=ASK_MAX_TOKENS,
            )
            question = data.get("question", "")
            cleaned = sanitize_llm_text(question) if isinstance(question, str) else ""
            if not cleaned:
                corrections.append("question was unusable; ask one plainly")
                continue
            verdict = audit_query(cleaned)
            if not verdict.ok:
                corrections.append(verdict.reason)
                continue
            return self._ask_action(cleaned, [leader_id], data.get("preface", ""), data)
        raise ReasonerError("clarify: could not produce a confirming question")

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
