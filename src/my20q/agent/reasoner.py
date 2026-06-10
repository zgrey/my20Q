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
from my20q.agent.hypotheses import Hypothesis
from my20q.agent.safety import sanitize_llm_text, sanitize_utterance
from my20q.llm.base import LLMBackend, LLMUnavailable

log = logging.getLogger(__name__)

#: How many times a query failing the format audit is re-proposed.
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
    #: For a query that explores a NEED NOT in the candidate list: its first-person
    #: text. On a yes/kinda the engine spawns it as a new candidate (its id is in
    #: yes_ids). Lets exploration escape the seed set.
    new_need: str = ""
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

    async def _deliberate(self, draft_messages: list) -> str:
        """Phase 1 of the ask: free-form reasoning toward the next question.

        Uncapped so a thinking model can reason to completion; a non-thinking model
        just writes a short rationale plus the question. If the model spends its
        whole budget thinking and returns empty, retry once with thinking OFF so we
        still get text; only a genuinely down backend raises.
        """
        try:
            draft = await self.llm.chat(
                draft_messages, max_tokens=DELIBERATE_MAX_TOKENS, json_mode=False
            )
        except LLMUnavailable:
            draft = ""
        if not draft.strip():
            try:
                draft = await self.llm.chat(
                    draft_messages,
                    max_tokens=DELIBERATE_MAX_TOKENS,
                    json_mode=False,
                    think=False,
                )
            except LLMUnavailable as exc:
                raise ReasonerError(f"llm unreachable: {exc}") from exc
        return draft

    async def _format_question(
        self, candidates: list[tuple[str, str, float]], draft: str
    ) -> dict:
        """Phase 2 of the ask: format the draft into strict question JSON.

        Salvages directly from the draft when the format call yields nothing usable
        (the draft may already carry the structured question), so a flaky format
        pass never costs us a whole turn.
        """
        try:
            data = await self._chat_json(
                prompts.format_question_messages(candidates, draft),
                max_tokens=FORMAT_MAX_TOKENS,
                think=False,
            )
        except ReasonerError:
            data = {}
        if not data.get("question"):
            salvaged = _extract_json(draft)
            if isinstance(salvaged, dict) and salvaged.get("question"):
                return salvaged
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
        history: list[dict],
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        anchored: bool = False,
        exploratory: bool = False,
        reset: bool = False,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Propose the next yes/no question, drilling toward the exact need.

        Two phases for EVERY model: DELIBERATE (free-form reasoning, no JSON) then
        FORMAT (strict JSON, salvaged from the draft if the format call comes up
        empty). The ONLY hard check is that the result is answerable as yes/no
        (``audit_query``); redundancy and on-topic fit are nudged through the prompt,
        NOT enforced as rejections — a rejection used to dump the round into the
        deterministic bank, which is worse than an imperfect real question. After the
        retry budget we accept the best effort rather than fall back.

        ``exploratory`` drops the profile to free up a new avenue; ``anchored``
        restricts to the warm cluster; ``reset`` re-grounds in the YES confirmations
        after a long run of "no".
        """
        live_ids = {hid for hid, _, _ in candidates}
        corrections: list[str] = []
        best: ReasonerAction | None = None
        # Warm-trail material from the answered history (for the prompt blocks).
        kinda_texts = [
            h["text"]
            for h in history
            if h.get("kind") == "query"
            and h.get("answer") == "kinda"
            and h.get("text")
        ]
        # On a soft reset, re-ground the fresh question in what was actually
        # confirmed ("yes"); the warm "kinda" trail is dumped, not echoed.
        yes_texts = [
            h["text"]
            for h in history
            if h.get("kind") == "query"
            and h.get("answer") == "yes"
            and h.get("text")
        ]
        # Exploratory turns ignore the profile so the model can open a new avenue.
        eff_profile = "" if exploratory else profile_context
        for attempt in range(MAX_AUDIT_RETRIES + 1):
            if on_phase is not None:
                on_phase("re-asking" if attempt else "thinking")
            draft = await self._deliberate(
                prompts.deliberate_messages(
                    topic_label,
                    candidates,
                    history,
                    seed_context=seed_context,
                    profile_context=eff_profile,
                    topic_hint=topic_hint,
                    emotional_state=emotional_state,
                    corrections=corrections,
                    anchored=anchored,
                    exploratory=exploratory,
                    reset=reset,
                    kinda_texts=kinda_texts,
                    yes_texts=yes_texts,
                )
            )
            if not draft.strip():
                corrections.append(
                    "the reasoning produced no question; state one plainly"
                )
                continue
            data = await self._format_question(candidates, draft)
            question = data.get("question", "")
            if not isinstance(question, str) or not question.strip():
                corrections.append("no usable question in the draft; state one plainly")
                continue
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
            raw_new = data.get("new_need")
            new_need = sanitize_llm_text(raw_new) if isinstance(raw_new, str) else ""
            # If the question maps to no existing candidate but proposes a NEW need,
            # mint a fresh candidate id — exploration escapes the seed set. On a
            # yes/kinda the engine spawns it (see dialogue._replay_belief).
            if not yes_ids and new_need:
                n_new = sum(1 for h in history if h.get("new_need"))
                yes_ids = [f"n{n_new + 1}"]
            else:
                new_need = ""  # mapped to an existing candidate — ignore any new_need
            yes_set = set(yes_ids)
            action = self._ask_action(
                cleaned, yes_ids, data.get("preface", ""), data, new_need=new_need
            )
            best = action

            # The ONE hard gate: it must be answerable as a yes/no question.
            # Redundancy and on-topic fit are handled by the prompt, never enforced
            # here — a reject would dump the round to the worse deterministic bank.
            verdict = audit_query(cleaned)
            if not verdict.ok:
                corrections.append(verdict.reason)
                continue
            # A "yes" has to land on something — an existing candidate or a new need.
            if not yes_set:
                corrections.append(
                    "tag yes_ids with the candidate(s) a 'yes' confirms, or set "
                    "new_need to explore a brand-new need"
                )
                continue
            return action

        # Retries exhausted — accept the best effort. A real, if imperfect, question
        # beats degrading to the deterministic bank; only a total miss raises.
        if best is not None and best.yes_ids:
            best.rationale = f"(best-effort) {best.rationale}".strip()
            return best
        raise ReasonerError("ask: could not produce a usable question")

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
        rejected: list[tuple[str, str]] | None = None,
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Phrase the leading need as a confirmable first-person utterance.

        ``rejected`` is ``(utterance, answer)`` for utterances the caregiver did
        NOT confirm this attempt — the model must phrase it DIFFERENTLY (a "kinda"
        means close, refine the wording; a "no" means try a different angle).
        """
        if on_phase is not None:
            on_phase("thinking")
        messages = prompts.synthesize_messages(
            topic_label,
            leading_need,
            history,
            rejected=rejected,
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
        rationale = (
            "Rephrased — a prior utterance was not confirmed."
            if rejected
            else "Proposed from the leading candidate need."
        )
        return ReasonerAction(kind="synthesis", content=cleaned, rationale=rationale)

    def _ask_action(
        self,
        question: str,
        yes_ids: list[str],
        preface_raw: object,
        data: dict,
        new_need: str = "",
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
            new_need=new_need,
        )
