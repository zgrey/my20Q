"""LLM-driven reasoning calls for a round.

The LLM does *language*; ``agent/facets.py`` + ``agent/dialogue.py`` do the
*control*. Four focused calls:

- :meth:`Reasoner.seed_board` — starter contender values for the 5W1H slots.
- :meth:`Reasoner.ask` — the next yes/no question for the controller's chosen
  focus slot, with the ``slots`` it asserts. Three hard, code-level gates: the
  question must be answerable as yes/no (``audit_query``), must not repeat a
  prior question (``is_repeat`` — models demonstrably ignore the prompt nudge),
  and every tagged slot value must actually be SAID by the question
  (``facets.mentions`` — the fix for points landing on subjects no question
  ever mentioned, e.g. the hallucinated "visit with Aaron").
- :meth:`Reasoner.expand_slots` — facet values a caregiver note implies.
- :meth:`Reasoner.synthesize` — weave the slot leaders into an utterance.
- :meth:`Reasoner.flip` — the pending question re-rendered in its opposite
  connotation (the caregiver's opposition button; one fast call, no
  deliberation, repeat gate deliberately not applied).

Anything unusable raises ``ReasonerError``; the engine then runs its
context-restart recovery and, failing that, surfaces a diagnostic to the
caregiver — it NEVER falls back to canned questions.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from my20q.agent import facets, prompts
from my20q.agent.auditor import _normalize, audit_query, is_repeat
from my20q.agent.safety import sanitize_llm_text, sanitize_utterance
from my20q.llm.base import LLMBackend, LLMUnavailable

log = logging.getLogger(__name__)

#: How many times a query failing the gates is re-proposed.
MAX_AUDIT_RETRIES = 2
#: Bounds on the seeded board.
MIN_SEED_VALUES = 4
MAX_VALUES_PER_SLOT = 5
MAX_VALUE_CHARS = 48

# Output ceilings (num_predict). These are NOT a cost limit — inference is local
# and free — they only guard against a runaway/looping generation hanging the
# live cockpit. Because every call uses format=json, Ollama stops at the closing
# brace, so a generous ceiling never adds latency to a normal response; it only
# avoids truncating a longer one into invalid JSON.
SEED_MAX_TOKENS = 1024
# Two-phase ask (every model): the deliberate phase is the REASONING phase — do
# NOT token-cap it, or a verbose thinking model gets truncated mid-thought and
# emits nothing. -1 = generate until the model stops naturally; latency is
# bounded by the per-call timeout, which is the proper runaway guard. The
# format phase stays cheap and capped (it only emits the JSON).
DELIBERATE_MAX_TOKENS = -1
FORMAT_MAX_TOKENS = 512
SYNTH_MAX_TOKENS = 256
EXPAND_MAX_TOKENS = 512
FLIP_MAX_TOKENS = 256

#: Longest preface (spoken lead-in) we let through, in characters.
MAX_PREFACE_CHARS = 64


class ReasonerError(RuntimeError):
    """Raised when LLM output cannot be used — the engine should recover."""


@dataclass
class ReasonerAction:
    kind: Literal["query", "synthesis"]
    content: str
    rationale: str = ""
    #: Short spoken lead-in that flows grammatically into a query.
    preface: str = ""
    #: For a query: the (category -> value) pairs the question asserts — the
    #: ONLY pairs an answer is allowed to score. For a synthesis: the slot
    #: leaders the utterance was woven from.
    slots: dict[str, str] = field(default_factory=dict)
    #: The focus category the controller chose for this query.
    focus: str = ""
    #: The intent-direction bucket the question asserts (person topics only;
    #: classified by code from the question text — see facets.classify_direction).
    direction: str = ""
    #: The question this one replaced via the caregiver's opposition button —
    #: recorded with the answer so the dataset shows the flip happened.
    flipped_from: str = ""


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
        # Ollama returns empty content. Free-form reasoning belongs in ask()'s
        # separate `deliberate` phase.
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

        Uncapped so a thinking model can reason to completion; a non-thinking
        model just writes a short rationale plus the question. If the model
        spends its whole budget thinking and returns empty, retry once with
        thinking OFF so we still get text; only a genuinely down backend raises.
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
        self, board: facets.Board, focus: str, draft: str
    ) -> dict:
        """Phase 2 of the ask: format the draft into strict question JSON.

        Salvages directly from the draft when the format call yields nothing
        usable (the draft may already carry the structured question), so a
        flaky format pass never costs us a whole turn.
        """
        try:
            data = await self._chat_json(
                prompts.format_question_messages(board, focus, draft),
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

    async def seed_board(
        self,
        *,
        topic_label: str,
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        seed_universal_wants: bool = True,
        on_phase: Callable[[str], None] | None = None,
    ) -> dict[str, list[str]]:
        """Starter contender values per facet category (the board's prior)."""
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
        # Accept either the documented flat shape or a {"slots": {...}} wrapper.
        source = data.get("slots") if isinstance(data.get("slots"), dict) else data
        seeds = _clean_slot_lists(source)
        total = sum(len(v) for v in seeds.values())
        if total < MIN_SEED_VALUES or not seeds.get("what"):
            raise ReasonerError(
                f"seed: too few usable slot values (got {total}, what={seeds.get('what')})"
            )
        return seeds

    async def ask(
        self,
        *,
        topic_label: str,
        board: facets.Board,
        focus: str,
        directive: str,
        split_pair: tuple[str, str] | None = None,
        history: list[dict],
        asked: list[str] | None = None,
        established: set[tuple[str, str]] | None = None,
        banned: tuple[str, str] | None = None,
        caregiver_hint: str = "",
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        exploratory: bool = False,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Propose the next yes/no question for the controller's focus slot.

        Two phases for EVERY model: DELIBERATE (free-form reasoning, no JSON)
        then FORMAT (strict JSON, salvaged from the draft if the format call
        comes up empty). Four hard gates — yes/no answerability, the repeat
        gate, slot anchoring (every credited value must be words the question
        says), and the zero-information gate (a question asserting ONLY
        already-established pairs re-confirms what is known and is re-prompted
        — the cheap expected-information-gain proxy). After the retry budget,
        the best clean question is accepted; otherwise raises for the engine's
        recovery path.
        """
        asked = list(asked or [])
        established = established or set()
        corrections: list[str] = []
        best: ReasonerAction | None = None
        for attempt in range(MAX_AUDIT_RETRIES + 1):
            if on_phase is not None:
                on_phase("re-asking" if attempt else "thinking")
            draft = await self._deliberate(
                prompts.deliberate_messages(
                    topic_label,
                    board,
                    history,
                    focus=focus,
                    directive=directive,
                    split_pair=split_pair,
                    asked=asked,
                    seed_context=seed_context,
                    profile_context=profile_context,
                    topic_hint=topic_hint,
                    emotional_state=emotional_state,
                    corrections=corrections,
                    exploratory=exploratory,
                    banned=banned,
                    caregiver_hint=caregiver_hint,
                )
            )
            if not draft.strip():
                corrections.append(
                    "the reasoning produced no question; state one plainly"
                )
                continue
            data = await self._format_question(board, focus, draft)
            question = data.get("question", "")
            cleaned = (
                sanitize_llm_text(question) if isinstance(question, str) else ""
            )
            if not cleaned:
                corrections.append("no usable question in the draft; state one plainly")
                continue

            # Gate 1 — answerable as a plain yes/no, no leaked reasoning language.
            verdict = audit_query(cleaned)
            if not verdict.ok:
                corrections.append(verdict.reason)
                continue
            # Gate 2 — the hard repeat gate. Checked against every question
            # asked this round (including dumped segments); the prompt nudge
            # alone demonstrably failed (the same question asked 7 times).
            dup = is_repeat(cleaned, asked)
            if dup is not None:
                corrections.append(
                    f'you already asked "{dup}" — ask about something genuinely different'
                )
                continue
            # Gate 3 — slot anchoring: keep only pairs whose value the question
            # actually SAYS, folded onto existing contenders when equivalent.
            slots = _anchored_slots(data.get("slots"), cleaned, board)
            if not slots:
                # Recover deterministically: credit board contenders the
                # question text itself mentions.
                slots = _derive_slots(cleaned, board, prefer=focus)
            action = self._ask_action(cleaned, slots, data, focus)
            best = action
            if not slots:
                corrections.append(
                    "tag 1-2 slots whose value the question itself states "
                    "(who/what/when/where/why/how)"
                )
                continue
            # Gate 4 — zero information: every asserted pair is already an
            # established leader (confirmation farming — one trial pumped
            # who=Zach to +7.5 on re-confirmations while the real unknown
            # starved). Splits are exempt: tied leaders NEED separating.
            if (
                directive != "split"
                and all((c, v) in established for c, v in slots.items())
            ):
                corrections.append(
                    "every detail in that question is already confirmed — ask "
                    f"about something not yet established (the '{focus}' slot)"
                )
                continue
            return action

        # Retries exhausted. A clean, novel question with no scorable slots
        # still beats stalling the round — accept it; the answer scores nothing.
        if best is not None:
            best.rationale = f"(best-effort) {best.rationale}".strip()
            return best
        raise ReasonerError("ask: could not produce a usable question")

    async def expand_slots(
        self,
        *,
        topic_label: str,
        context: str,
        board: facets.Board,
        history: list[dict],
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> dict[str, list[str]]:
        """Facet values a caregiver note implies — never raises (additive).

        Each value must be anchored in the note's own words or match an
        existing contender (the same anti-hallucination rule as questions);
        a failure just means "no change" and the round carries on.
        """
        if on_phase is not None:
            on_phase("thinking")
        messages = prompts.expand_messages(
            topic_label,
            context,
            board,
            history,
            seed_context=seed_context,
            profile_context=profile_context,
            topic_hint=topic_hint,
            emotional_state=emotional_state,
        )
        try:
            data = await self._chat_json(messages, max_tokens=EXPAND_MAX_TOKENS)
        except ReasonerError:
            return {}
        source = data.get("slots") if isinstance(data.get("slots"), dict) else data
        cleaned = _clean_slot_lists(source)
        out: dict[str, list[str]] = {}
        for cat, values in cleaned.items():
            kept: list[str] = []
            for value in values:
                canonical = facets.canonical_value(board, cat, value)
                known = canonical in board.get(cat, {})
                if known or facets.mentions(context, value):
                    kept.append(canonical if known else value)
            if kept:
                out[cat] = kept
        return out

    async def synthesize(
        self,
        *,
        topic_label: str,
        leaders: dict[str, str],
        history: list[dict],
        rejected: list[tuple[str, str]] | None = None,
        seed_context: str = "",
        profile_context: str = "",
        topic_hint: str = "",
        emotional_state: dict | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Weave the slot leaders into a confirmable first-person utterance.

        ``rejected`` is ``(utterance, answer)`` for utterances the caregiver
        did NOT confirm this attempt — the model must phrase it DIFFERENTLY (a
        "kinda" means close, refine the wording; a "no" means try a different
        angle).
        """
        if on_phase is not None:
            on_phase("thinking")
        messages = prompts.synthesize_messages(
            topic_label,
            leaders,
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
        if not cleaned:
            raise ReasonerError(f"synthesize: unusable utterance {data!r}")
        rationale = (
            "Rephrased — a prior utterance was not confirmed."
            if rejected
            else "Proposed from the confirmed slot leaders."
        )
        return ReasonerAction(
            kind="synthesis", content=cleaned, rationale=rationale, slots=dict(leaders)
        )

    async def flip(
        self,
        *,
        question: str,
        board: facets.Board,
        focus: str = "",
        direction: str = "",
        mirror: str = "",
        on_phase: Callable[[str], None] | None = None,
    ) -> ReasonerAction:
        """Re-render a pending question with its connotation reversed.

        The caregiver's opposition button: same subject, opposite direction
        (who-does-for-whom mirrored via the intent buckets when the original
        classified, the key attribute reversed otherwise). One fast JSON call
        — no deliberate phase, so the flip feels like a trigger click. The
        result is audited for yes/no form and must actually differ from the
        original; the repeat gate deliberately does NOT apply — a flip is a
        near-duplicate of its source by design.
        """
        if on_phase is not None:
            on_phase("flipping")
        note = ""
        if direction in facets.DIRECTION_BUCKETS and mirror in facets.DIRECTION_BUCKETS:
            note = (
                f'It currently asks the person about "'
                f'{facets.DIRECTION_BUCKETS[direction]}" — the flipped question '
                f'must ask about "{facets.DIRECTION_BUCKETS[mirror]}" instead.'
            )
        corrections: list[str] = []
        for _ in range(MAX_AUDIT_RETRIES + 1):
            data = await self._chat_json(
                prompts.flip_messages(
                    question, direction_note=note, corrections=corrections
                ),
                max_tokens=FLIP_MAX_TOKENS,
                think=False,
            )
            out = data.get("question", "")
            cleaned = sanitize_llm_text(out) if isinstance(out, str) else ""
            if not cleaned:
                corrections.append("return the flipped question in the JSON")
                continue
            verdict = audit_query(cleaned)
            if not verdict.ok:
                corrections.append(verdict.reason)
                continue
            if _normalize(cleaned) == _normalize(question):
                corrections.append(
                    "that is the same question unchanged — reverse its direction"
                )
                continue
            slots = _anchored_slots(data.get("slots"), cleaned, board)
            if not slots:
                slots = _derive_slots(cleaned, board, prefer=focus or "how")
            return ReasonerAction(
                kind="query",
                content=cleaned,
                rationale="Flipped to the opposite sense at the caregiver's request.",
                slots=slots,
                focus=focus,
                flipped_from=question,
            )
        raise ReasonerError("flip: could not produce a flipped question")

    def _ask_action(
        self, question: str, slots: dict[str, str], data: dict, focus: str
    ) -> ReasonerAction:
        rationale = str(data.get("rationale", "") or "")[:240]
        preface = _fluent_preface(str(data.get("preface", "") or ""), question)
        return ReasonerAction(
            kind="query",
            content=question,
            rationale=rationale,
            preface=preface,
            slots=slots,
            focus=focus,
        )


# ---------------------------------------------------------------- pure helpers


def _clean_slot_lists(source: object) -> dict[str, list[str]]:
    """Sanitize a ``{category: [values]}`` mapping from model JSON."""
    out: dict[str, list[str]] = {}
    if not isinstance(source, dict):
        return out
    for cat, values in source.items():
        if cat not in facets.CATEGORIES or not isinstance(values, list):
            continue
        kept: list[str] = []
        seen: set[str] = set()
        for v in values:
            if not isinstance(v, str):
                continue
            clean = sanitize_llm_text(v)[:MAX_VALUE_CHARS].strip()
            key = clean.casefold()
            if clean and key not in seen:
                seen.add(key)
                kept.append(clean)
            if len(kept) >= MAX_VALUES_PER_SLOT:
                break
        if kept:
            out[cat] = kept
    return out


def _anchored_slots(
    raw: object, question: str, board: facets.Board
) -> dict[str, str]:
    """Keep only slot tags whose value the question text actually says.

    This is the structural fix for the score-drift bug: a "yes" can no longer
    credit a contender (e.g. an Aaron visit) when the question never mentioned
    it. Verb-led values tagged "what" are re-filed under "how" (actions are
    not objects — mis-filing jammed the focus policy on an unfillable slot),
    and values fold onto existing contenders when equivalent so points
    accumulate instead of fragmenting.
    """
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for cat, value in raw.items():
        if cat not in facets.CATEGORIES or not isinstance(value, str):
            continue
        clean = sanitize_llm_text(value)[:MAX_VALUE_CHARS].strip()
        if not clean or not facets.mentions(question, clean):
            continue
        cat = facets.remap_slot(cat, clean)
        if cat in out:
            continue  # a re-filed action never overwrites an explicit how-tag
        out[cat] = facets.canonical_value(board, cat, clean)
        if len(out) >= 2:
            break
    return out


def _derive_slots(
    question: str, board: facets.Board, *, prefer: str
) -> dict[str, str]:
    """Deterministic fallback tagging: board contenders the question mentions.

    Used when the model's own tags all failed the anchoring gate — the
    question still earns credit for any existing contender it plainly says.
    """
    out: dict[str, str] = {}
    order = [prefer] + [c for c in facets.CATEGORIES if c != prefer]
    for cat in order:
        for value, _score in facets.live(board, cat):
            if facets.mentions(question, value):
                out[cat] = value
                break
        if len(out) >= 2:
            break
    return out


def _fluent_preface(raw: str, question: str) -> str:
    """Sanitize the spoken lead-in so `preface + question` reads as ONE flow.

    The cockpit speaks ``f"{preface} {question}"`` — so the preface must be a
    short connective, not a restatement and not its own question. Anything
    that would read awkwardly is dropped (empty beats awkward), and the ending
    is normalized to an em dash so the voice glides into the question.
    """
    preface = sanitize_llm_text(raw)
    if not preface:
        return ""
    if len(preface) > MAX_PREFACE_CHARS:
        return ""
    if "?" in preface:
        return ""  # a second question never flows into the real one
    # A preface that mostly repeats the question is noise, not a lead-in.
    if facets.overlap_ratio(preface, question) >= 0.5:
        return ""
    preface = preface.rstrip(" .,;:—-")
    if not preface:
        return ""
    return preface + " —"
