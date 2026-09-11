"""Round state machine — the my20Q dialogue engine.

A `Session` spans one tool process and spawns `Round`s. A `Round` is one
convergence attempt under a single topic. A round ends ONLY when the caregiver
ACCEPTS the live draft (the banner's ✓) — the engine never proposes or
terminates on its own. The caregiver can still end it out-of-band (an
emergency topic short-circuit, a topic switch, or an operator safety ceiling).
Terminology and lifecycle: docs/design/beta-retool.md §2, §7.

The belief is a 5W1H **facet board** (``agent/facets.py``): per-category
contender values with additive consensus points, recomputed from history so
``undo()`` is pop-and-recompute. Each turn the controller picks a FOCUS slot
and a directive (probe an unestablished core slot / split tied contenders /
drill the vague leader); the `Reasoner` turns that into language. Crediting is
anchored to the question text, so an answer can never move a contender the
question didn't mention.

Synthesis is the **living proposal banner** (owner design — see
docs/design/convergence-plan.md W1-C): an evolving draft utterance woven from
the ban/mute-aware slot leaders, rendered from the first converged core slot
(code template with slashed alternates + ellipsis; the LLM weave takes over
at board-readiness, regenerated only when the weave changes). The caregiver
concludes with ``accept()`` (✓ — the sole terminator), speaks the draft at
whim, and edits it in real time with ``edit()`` (✗ — value bans struck
through, slot mutes dimmed; both replayable history entries). There are no
engine-initiated proposals, so there is no kinda-rephrase loop.

A **restart** is the round's one recovery mechanism, with three triggers
(a long run of "no", stalled board progress, and the reasoner fail-loop):
it dumps every "no"/"kinda" influence and rebuilds the board from ONLY the
caregiver context and this round's confirmed-yes answers (round-specific, not
session-wide), plus fresh broad probes. Post-restart prompts hide the dumped
noise; the code-level repeat gate still spans the whole round.

There are NO canned fallback questions. When reasoning fails and the restart
recovery cannot produce a question either, the round surfaces a DIAGNOSTIC
event that tells the caregiver exactly what failed and how to proceed (retry /
add context / switch model) — a useful failure beats a meaningless question.

The engine is async: the FastAPI layer awaits it directly; a CLI wraps it in
`asyncio.run`.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from my20q.agent import facets
from my20q.agent.reasoner import Reasoner, ReasonerAction, ReasonerError
from my20q.agent.safety import EMERGENCY_SCREEN
from my20q.config import Config, Mode, ReasoningTuning
from my20q.llm.base import LLMBackend
from my20q.profiles import PatientProfile, is_real_patient
from my20q.recording.yes_memory import YesMemory, dated_path
from my20q.topics import Topic, find_topic

log = logging.getLogger(__name__)


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    KINDA = "kinda"
    NOT_SURE = "not_sure"


EventKind = Literal[
    "query", "synthesis", "emergency", "synthesized", "abandoned", "diagnostic"
]
Engine = Literal["reasoning", "fallback"]

#: Consecutive reasoning failures after which the log level escalates. Reasoning
#: is NEVER permanently abandoned — every turn retries the reasoner, because only
#: the reasoner can produce the utterance a round needs to end.
MAX_CONSEC_REASON_FAILURES = 3

#: Default behavior knobs — the single source of truth for the tuning defaults. A
#: `Round` uses the `ReasoningTuning` it is handed (the API/CLI thread the
#: env-configured one through `Session`); this module alias is the default,
#: kept for readability and the tests. Override via MY20Q_* env vars (see config).
_DEFAULT_TUNING = ReasoningTuning()
SOFT_RESET_NO_STREAK = _DEFAULT_TUNING.soft_reset_no_streak

#: Never focus the same facet category more than this many turns in a row when
#: an alternative exists — the cure for the observed "why"-hammering (20 wasted
#: queries on motivation while "what" stayed unknown).
MAX_CATEGORY_RUN = 2

#: Consecutive "no" answers sharing one asserted pair (or one direction) after
#: which that avenue is declared exhausted for a turn — the enumeration guard
#: (remind→dinner→memory→appointment→medicine→BP… was 10+ fruitless guesses
#: down one avenue).
FUTILE_STREAK = 4

#: Double-checks are no longer capped per round (owner, 2026-09-10). Every new
#: detail the draft picks up gets confirmed once, so the caregiver can hold one
#: invariant: *the banner never says anything you have not confirmed twice.* The
#: old budget of 2 existed only because a contradicted check used to DESTROY the
#: belief (W2-L) — W2-T removed that cost, and a verify is a single LLM call
#: against a normal question's two, making it the cheapest question asked.
#:
#: What is capped is a RUN. Each pair is checked at most once ever, so the
#: natural rate is set by how often a new detail appears; the failure mode left
#: is several landing at once — a caregiver note crediting three slots would
#: draw three consecutive "just to double-check" turns and read as an
#: interrogation. Two in a row is fine and often right (a note that lands two
#: details deserves two checks); a third must wait behind an ordinary question.
VERIFY_MAX_RUN = 2

#: Ceiling on one clarification episode. The detail walk is normally bounded by
#: how many details the draft has; this is the backstop for a draft that somehow
#: mines more. Running out is never a failure and never ends anything.
MAX_CLARIFY_QUERIES = 8

#: The clarifying-mode question frame (owner-specified, 2026-09-10). Pointed and
#: deterministic — in clarifying mode the CONTEXT is carried by the mode itself
#: (the banner shows the sentence, the conversation is demarcated, and the
#: spoken preface announces the walk), which frees each question to name exactly
#: one detail. Costs ZERO LLM calls, so a whole walk is cheaper than one
#: ordinary question.
CLARIFY_FRAME = "This is about {value}, correct?"
CLARIFY_FRAME_WHY = "This is because of {value}, correct?"
#: `how` holds VERB phrases — "call them", "bring it", "tell them something" —
#: and the default frame reads as broken English around them ("This is about
#: call them, correct?", seen in the first live run). But `how` ALSO holds
#: gerunds, and this frame breaks on those the other way: "You want to lifting
#: things, correct?" was spoken to a patient in the second live run, and the
#: caregiver kept reaching for the opposition button to escape it. The frame is
#: therefore chosen by the value's grammatical FORM, not by its slot.
CLARIFY_FRAME_HOW = "You want to {value}, correct?"

#: Spoken once, at the top of a clarification — the patient HEARS the questions
#: rather than reading the banner, so the frame the demarcation gives the
#: caregiver has to reach them some other way.
CLARIFY_PREFACE_OPEN = "Let me check this one piece at a time —"
CLARIFY_PREFACE = "Still checking —"

_NO_LLM_TEXT = (
    "No language model is configured, so questions cannot be generated. "
    "Start the backend with an LLM (e.g. Ollama) and retry."
)


@dataclass
class RoundEvent:
    """The cockpit-facing state produced by open()/answer()/undo()/retry()."""

    kind: EventKind
    text: str = ""
    rationale: str = ""
    preface: str = ""  # short spoken lead-in read just before a query
    query_index: int = 0
    engine: Engine = "reasoning"
    emergency_screen: dict | None = None
    # The live facet board — per 5W1H category, the top contender values with
    # their consensus points — that drove this question. Empty in emergency /
    # terminal / diagnostic events. Powers the cockpit's honest reasoning tile.
    facets: list[dict] = field(default_factory=list)
    # For kind == "diagnostic": what failed and what was attempted, so the
    # cockpit can render a useful failure card instead of a canned question.
    diagnostic: dict | None = None
    # The question this one replaced via the opposition button — lets the
    # cockpit mark a flipped question instead of presenting it as a new turn.
    flipped_from: str = ""
    # The round is CLARIFYING: confirming the draft one detail at a time after
    # a contradiction. The cockpit demarcates the conversation and shows a
    # "clarifying" banner, so the pointed questions read as a deliberate pass
    # over the draft rather than the engine losing the thread.
    clarifying: bool = False


class Round:
    """One convergence attempt under a single topic.

    Drive it: ``await open()`` once, then alternate ``await answer(a)``
    with reading each `RoundEvent`. ``add_context`` injects caregiver
    steering; ``await undo()`` rewinds the last entry; ``await retry()``
    re-proposes after a diagnostic; ``await flip()`` re-renders the pending
    question in its opposite connotation (an action, not an answer).
    ``banner()`` is the living draft proposal; ``accept()`` concludes the
    round with it (the ✓ — the only way a round ends in success) and
    ``await edit(note)`` applies a ✗-edit (value ban / slot mute / fallback
    context). The round is over once an event of kind ``synthesized``,
    ``abandoned``, or ``emergency`` is returned (also ``is_terminal``).
    """

    def __init__(
        self,
        topic: Topic,
        *,
        llm: LLMBackend | None = None,
        max_queries: int = 0,
        mode: Mode = "training",
        seed_context: str = "",
        profile_context: str = "",
        caregivers: list[str] | None = None,
        emotional_state: dict[str, float] | None = None,
        tuning: ReasoningTuning | None = None,
        yes_memory: YesMemory | None = None,
        round_id: str = "",
        session_id: str = "",
        rng: random.Random | None = None,
    ) -> None:
        self.topic = topic
        self.llm = llm
        # <= 0 means unlimited — a pure operator safety ceiling, not a terminator.
        self.max_queries = max_queries
        self.mode = mode
        self.seed_context = seed_context.strip()
        self.profile_context = profile_context.strip()
        # Known caregivers (from the profile). Hardcoded ask-order prior:
        # caregivers offer care as tasks, so when the who-leader is a caregiver
        # and no direction is established, the first how-probe tests the
        # they-do-for-me direction — order only, never score points.
        self.caregivers = [c.strip() for c in (caregivers or []) if c.strip()]
        # Caregiver emotional-slider reading; steers question tone (the API
        # keeps it in sync mid-round). See docs/design/beta-retool.md §6.
        self.emotional_state: dict[str, float] = dict(emotional_state or {})
        # Behavior knobs (env-configurable). Defaults match the module aliases.
        self.tuning = tuning or ReasoningTuning()
        # Confirmed-yes log. The engine WRITES it (for the recorded dataset and
        # the future caregiver interview tool) but no longer reads it — restart
        # recovery draws yes-context from this round's own history, keeping the
        # recovered signal round-specific by construction.
        self._yes_memory = yes_memory if yes_memory is not None else YesMemory()
        self.round_id = round_id
        #: The session this round belongs to — written into the yes-log so a
        #: confirmed answer can be joined back to its recording (W2-Q).
        self.session_id = session_id
        # Drives the (decaying) exploration coin-flip; injectable for tests.
        self._rng = rng or random.Random()
        self.engine: Engine = "reasoning" if llm is not None else "fallback"
        #: Why reasoning failed most recently, if it did — surfaced for
        #: diagnostics (the cockpit failure card and the model bench).
        self.degrade_reason: str = ""
        #: Consecutive failed reasoning turns; reset on any successful turn.
        self._consec_failures = 0
        self._reasoner = Reasoner(llm) if llm is not None else None
        # The round's seed values per facet category, generated once by the
        # reasoner on the first advance. The live board is recomputed from
        # these + history (see _replay_board).
        self._seed_values: dict[str, list[str]] | None = None
        self._history: list[dict] = []
        self._pending: ReasonerAction | None = None
        # Questions replaced unanswered via the opposition button. They were
        # rendered (often spoken), so the repeat gate must still span them —
        # but they carry no answer and never enter the history/board.
        self._superseded: list[str] = []
        # The banner's woven draft cache: the LLM weave is regenerated only
        # when the weave (the ban/mute-aware slot leaders) changes; below
        # board-readiness the banner renders the code template instead.
        self._draft_text: str = ""
        self._draft_weave: dict[str, str] = {}
        # Refinement edges (child → parent per category), derived alongside
        # every full board replay from history tags + the lexical fallback —
        # reading structure only; scores stay flat and text-anchored.
        self._edges: facets.Edges = facets.empty_edges()
        self._outcome: str | None = None
        self._final_utterance = ""
        self._opened = False
        # Autopsy instrumentation (W2-O). The seed call is the round's fixed
        # cost before the first question, so it is timed once and recorded at
        # round level rather than charged to q1; the question on screen when a
        # round is abandoned is captured so the record shows what was unanswered.
        self._seed_ms: float = 0.0
        self._abandoned_pending: ReasonerAction | None = None
        # Optional progress hook — the API wires this to the SSE channel.
        self.on_phase: Callable[[str], None] | None = None

    # ----------------------------------------------------------- state

    @property
    def history(self) -> list[dict]:
        """Copy of the round's ordered query/synthesis/context entries."""
        return [dict(h) for h in self._history]

    @property
    def outcome(self) -> str | None:
        """`synthesized` / `abandoned` / `emergency`, or None while live."""
        return self._outcome

    @property
    def is_terminal(self) -> bool:
        return self._outcome is not None

    @property
    def final_utterance(self) -> str:
        """The confirmed utterance once the round is `synthesized`."""
        return self._final_utterance

    @property
    def query_count(self) -> int:
        return sum(1 for h in self._history if h["kind"] == "query")

    @property
    def seed_ms(self) -> float:
        """Wall-clock milliseconds the board-seeding call took (W2-O).

        A round's fixed cost before its first question — invisible in the
        per-query timings, and the difference between "the model is slow" and
        "the model was still loading".
        """
        return self._seed_ms

    @property
    def pending_question(self) -> dict | None:
        """The unanswered question on screen — the one the round ended on (W2-O).

        A round abandoned on a topic switch leaves a question hanging, and the
        record used to drop it — so an autopsy could not tell a round that ran
        out of questions from one the caregiver walked away from mid-question.
        For a still-live round (the conversation export serves those too) this
        is simply the question currently awaiting an answer.
        """
        action = self._pending or self._abandoned_pending
        if action is None or action.kind != "query":
            return None
        out: dict = {"text": action.content, "rationale": action.rationale}
        if action.focus:
            out["focus"] = action.focus
        if action.slots:
            out["slots"] = dict(action.slots)
        return out

    # ------------------------------------------------------- lifecycle

    async def open(self) -> RoundEvent:
        if self._opened:
            raise RuntimeError("Round.open() already called")
        self._opened = True
        if self.topic.emergency:
            self._outcome = "emergency"
            return RoundEvent(
                kind="emergency",
                engine=self.engine,
                emergency_screen=dict(EMERGENCY_SCREEN),
            )
        return await self._advance()

    async def answer(self, a: Answer) -> RoundEvent:
        if not self._opened:
            raise RuntimeError("answer() called before open()")
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        if self._pending is None:
            raise RuntimeError("answer() called with no pending action")

        pending = self._pending
        # Persist the reasoner's rationale, preface, focus, direction, and
        # asserted slots alongside each entry — saves, recordings, and the
        # review dashboard can then show *why* and *about what* each question
        # was asked, and the board replay can recompute scores for undo.
        entry: dict = {
            "kind": pending.kind,
            "text": pending.content,
            "answer": a.value,
            "rationale": pending.rationale,
        }
        if pending.preface:
            entry["preface"] = pending.preface
        if pending.slots:
            entry["slots"] = dict(pending.slots)
        if pending.kind == "query" and pending.focus:
            entry["focus"] = pending.focus
        # The slot the controller asked for, when the question went elsewhere
        # (W2-P) — kept so the divergence rate stays measurable.
        if pending.kind == "query" and pending.focus_requested:
            entry["focus_requested"] = pending.focus_requested
        if pending.kind == "query" and pending.direction:
            entry["direction"] = pending.direction
        if pending.kind == "query" and pending.flipped_from:
            entry["flipped_from"] = pending.flipped_from
        if pending.kind == "query" and pending.verify:
            entry["verify"] = True
            # W2-T: a double-check answered "no" CONTRADICTS a pair the board
            # holds confidently — `_verify_due` fires on nothing else. Scoring
            # it erases the confirmation on the strength of a question that
            # dropped the very context which made that confirmation mean
            # something ("Is there tingling in your toes?" yes → "Is it
            # tingling?" no, which halved the pair and killed the 09-01 round).
            # Mark it CONTESTED instead: replay scores it as nothing, and the
            # anchored clarification that follows carries the real evidence —
            # in whichever direction it points.
            if a is Answer.NO:
                entry["contested"] = True
        if pending.kind == "query" and pending.clarify:
            entry["clarify"] = True
            # "confirm" put a scored detail to the person; "dig" kept a
            # confirmed detail and changed the axis. The two close a
            # clarification on opposite answers, so the phase has to be on the
            # entry — a no ends a confirm, a yes ends a dig.
            entry["clarify_phase"] = pending.clarify_phase or "confirm"
            if pending.clarify_detail:
                entry["clarify_detail"] = list(pending.clarify_detail)
        if pending.kind == "query" and pending.refines:
            entry["refines"] = dict(pending.refines)
        # What the controller was drilling into (W2-R). Recorded on every drill,
        # answered or not — it is a fact about the question, and an autopsy
        # wants to see the ladder the controller was trying to walk.
        if pending.kind == "query" and pending.drill_parent:
            entry["drill_parent"] = pending.drill_parent
        # Autopsy instrumentation (W2-O): what the turn cost, and which gates
        # rejected earlier attempts at it. Both were discarded before.
        if pending.timings:
            entry["timing"] = dict(pending.timings)
        if pending.rejections:
            entry["rejections"] = list(pending.rejections)
        # An INFORMATIVE yes moved the board (some asserted pair was not yet
        # established); a yes that merely re-confirms established leaders
        # earns its points but does NOT advance the synthesis gates —
        # confirmation farming must never substitute for new information.
        if pending.kind == "query" and a is Answer.YES:
            board = self._replay_board()
            ready = self.tuning.facet_ready_points
            entry["informative"] = any(
                board.get(cat, {}).get(
                    facets.canonical_value(board, cat, val), 0.0
                )
                < ready
                for cat, val in (pending.slots or {}).items()
            )
        self._history.append(entry)
        self._pending = None
        # Log every confirmed-YES query (write-only; see __init__).
        if pending.kind == "query" and a is Answer.YES:
            self._record_yes(pending)

        if pending.kind == "synthesis" and a is Answer.YES:
            self._outcome = "synthesized"
            self._final_utterance = pending.content
            return RoundEvent(
                kind="synthesized",
                text=pending.content,
                query_index=self.query_count,
                engine=self.engine,
            )
        return await self._advance()

    async def add_context(self, text: str) -> RoundEvent:
        """Inject caregiver context mid-round and re-propose forward.

        A note from a caregiver / medical professional is **high-trust** signal
        — far more reliable than the seeds. In reasoning mode we ask the
        reasoner which facet values the note implies or confirms and credit
        them at a boosted weight (see ``facets.apply_context``), so the next
        question discriminates over what the caregiver just said. The slots are
        recorded on the context entry, keeping the board reconstructible for
        undo. Empty context is a no-op.
        """
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        text = text.strip()
        if not text:
            if self._pending is not None:
                return self._pending_event()
            return await self._advance()

        slots: dict[str, list[str]] = {}
        if self._reasoner is not None and self._seed_values is not None:
            board = self._replay_board()
            slots = await self._reasoner.expand_slots(
                context=text,
                board=board,
                history=self._history,
                **self._reasoner_ctx(),
            )

        entry: dict = {"kind": "context", "text": text, "answer": None}
        if slots:
            entry["slots"] = slots
        self._history.append(entry)
        self._pending = None
        return await self._advance()

    def abandon(self) -> None:
        """Finalize a still-live round as abandoned.

        Used when the caregiver switches topic or starts a new round
        before the current one converges (see the round lifecycle in
        docs/design/beta-retool.md §2).
        """
        if self._outcome is None:
            self._outcome = "abandoned"
            # Keep the question that was on screen — the record needs to show
            # what went unanswered (W2-O). Cleared from _pending either way, so
            # nothing treats the terminal round as still awaiting an answer.
            self._abandoned_pending = self._pending
            self._pending = None

    async def undo(self) -> RoundEvent:
        """Rewind the most recent entry and re-propose forward.

        Everything downstream of the undone entry is discarded; the next
        action is re-proposed against the truncated history, so it may
        differ from before.
        """
        if not self._opened:
            raise RuntimeError("undo() called before open()")
        if self._outcome == "emergency":
            raise RuntimeError("cannot undo an emergency round")
        if self._history:
            self._history.pop()
        self._outcome = None
        self._final_utterance = ""
        self._pending = None
        return await self._advance()

    async def retry(self) -> RoundEvent:
        """Re-propose after a diagnostic (the failure card's Retry button)."""
        if not self._opened:
            raise RuntimeError("retry() called before open()")
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        self._pending = None
        return await self._advance()

    async def flip(self) -> RoundEvent:
        """Re-render the pending question in its opposite connotation.

        The caregiver's opposition button — an ACTION, not an answer: the
        question on screen points the wrong way (e.g. "do something for Rob"
        when the need is Rob doing something for the patient), so it is
        re-asked mirrored and the round keeps waiting for an answer. The
        original was rendered (often spoken), so it still counts as asked for
        the repeat gate; it never enters the history or the board. Failure is
        soft: any error leaves the pending question untouched.
        """
        if not self._opened:
            raise RuntimeError("flip() called before open()")
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        pending = self._pending
        if pending is None or pending.kind != "query":
            raise RuntimeError("no pending question to flip")
        if self._reasoner is None:
            raise RuntimeError("no language model is configured — cannot flip")

        board = self._replay_board()
        action = await self._reasoner.flip(
            question=pending.content,
            board=board,
            focus=pending.focus,
            direction=pending.direction,
            mirror=facets.MIRROR.get(pending.direction, ""),
            on_phase=self.on_phase,
        )
        # Reasoning demonstrably works — clear any failure streak.
        self._consec_failures = 0
        self.engine = "reasoning"
        self._superseded.append(pending.content)
        # A flip during a clarification stays PART of that clarification. The
        # flipped action is built fresh by the reasoner, so without this it
        # loses the mode (the cockpit drops the demarcation) and, worse, the
        # walk never registers that this detail was put to the person and
        # re-issues the identical templated question on the very next turn.
        if pending.clarify:
            action.clarify = True
            action.clarify_phase = pending.clarify_phase
            action.clarify_detail = pending.clarify_detail
            action.focus_requested = pending.focus_requested or pending.focus
        if self.topic.direction:
            who_names = [v for v, _ in facets.live(board, "who")]
            if action.slots.get("who"):
                who_names.append(action.slots["who"])
            action.direction = (
                facets.classify_direction(action.content, who_names) or ""
            )
        self._pending = action
        return self._event_for(action, facets.facet_view(board, action.focus, self._edges))

    # -------------------------------------------------------- internal

    async def _advance(self) -> RoundEvent:
        # No reasoner at all (constructed without an LLM): there are no canned
        # questions anymore — surface the condition honestly instead.
        if self._reasoner is None:
            self.engine = "fallback"
            self._pending = None
            return RoundEvent(
                kind="diagnostic",
                text=_NO_LLM_TEXT,
                engine=self.engine,
                query_index=self.query_count,
                diagnostic={
                    "reason": "no LLM backend configured",
                    "llm_unreachable": True,
                    "restart_attempted": False,
                    "consecutive_failures": self._consec_failures,
                },
            )

        T = self.tuning
        try:
            # Seed the facet board once, on the first advance.
            if self._seed_values is None:
                t0 = time.perf_counter()
                try:
                    seeds = await self._reasoner.seed_board(
                        seed_universal_wants=self.topic.seed_universal_wants,
                        **self._reasoner_ctx(),
                    )
                finally:
                    self._seed_ms = round((time.perf_counter() - t0) * 1000, 1)
                self._seed_values = self._inject_direction_buckets(seeds)

            # A long run of "no" — or a stretch of questioning in which no
            # contender newly reached a confirmed score (stalled progress;
            # sparse kindas used to shield a dead-end round from the streak
            # trigger) — means the working context is WRONG: run the restart
            # recovery (dump no/kinda influence, keep caregiver context +
            # this round's yeses, add fresh probes).
            restart_reason = ""
            if self._consec_no_streak() > T.soft_reset_no_streak:
                restart_reason = "no-streak"
            elif self._stalled():
                restart_reason = "stalled"
            if restart_reason and not self._just_restarted():
                await self._restart(restart_reason)

            # The living proposal banner replaced engine-initiated synthesis
            # (owner design — convergence-plan W1-C): the round only ever asks
            # questions, the banner carries the evolving draft, and ONLY the
            # caregiver concludes (``accept()``). No proposals, no rephrases,
            # no kinda-loop — the remaining restart triggers are the
            # no-streak / stalled checks above and the fail-loop path.
            seg = self._segment()

            # Operator safety ceiling — the ONLY count-based stop, off by default
            # (<= 0). It never forces a half-baked utterance; it just abandons.
            if self.max_queries > 0 and self.query_count >= self.max_queries:
                self._outcome = "abandoned"
                return RoundEvent(
                    kind="abandoned", query_index=self.query_count, engine=self.engine
                )

            board = self._replay_board()
            focus = ""
            clarify = self._clarify_state()
            if clarify is not None and not clarify["unresolved"]:
                # CLARIFYING MODE outranks everything, including the
                # double-check that may have opened it: the board is
                # confidently wrong, so there is no other progress worth
                # making. Confirming a detail is templated — no LLM call at
                # all; digging at a confirmed anchor is a real question and
                # goes through the model, but only on the rarer branch where
                # every detail has already held.
                if clarify["phase"] == "confirm":
                    action = self._clarify_action(clarify)
                    focus = action.focus
                else:
                    action, board, focus = await self._propose_question(
                        seg, dig=clarify
                    )
                    action.clarify = True
                    action.clarify_phase = "dig"
                    action.preface = CLARIFY_PREFACE
            elif (pair := self._verify_due(board)) is not None:
                # Confirm a new detail the draft has picked up, before the
                # round builds anything else on top of it.
                action = await self._reasoner.verify(
                    category=pair[0],
                    value=pair[1],
                    board=board,
                    on_phase=self.on_phase,
                )
                focus = pair[0]
                if self.topic.direction:
                    who_names = [v for v, _ in facets.live(board, "who")]
                    action.direction = (
                        facets.classify_direction(action.content, who_names)
                        or ""
                    )
            else:
                action, board, focus = await self._propose_question(seg)
        except ReasonerError as exc:
            return await self._handle_reason_failure(str(exc))

        # Reasoning succeeded — clear any failure streak.
        self._consec_failures = 0
        self.engine = "reasoning"
        self._pending = action
        # Keep the banner's draft current (cheap: re-weaves only on change).
        await self._refresh_draft(board)
        self._stamp_banner(board)
        return self._event_for(action, facets.facet_view(board, focus, self._edges))

    def _stamp_banner(self, board: facets.Board | None = None) -> None:
        """Snapshot the banner onto the entry that just moved it (W2-O).

        The record carried no banner state at all, so an autopsy could not say
        WHEN the board became propose-ready — the metric that separates "the
        round needed 18 questions" from "the round was answerable at q9 and
        asked nine more". Called from every path that consumes an answer,
        the failure paths included: an answer that tipped the board into
        readiness must not go unrecorded just because the NEXT question failed
        to generate. Pass the replayed board when one is already in hand
        (``_advance`` has just refreshed the weave against it); otherwise it is
        replayed here. Re-stamped on undo/retry — the freshest snapshot wins.
        """
        if self._seed_values is None:
            return  # a round that never got its seeds
        # The target is the answer/note that produced this board state. The
        # failure path interposes its own markers (a restart, then the
        # diagnostic) between that entry and here, so skip those — but stop at
        # anything else, so a snapshot is never misattributed across a
        # caregiver edit or a synthesis.
        last: dict | None = None
        for h in reversed(self._history):
            kind = h.get("kind")
            if kind in ("query", "context"):
                last = h
                break
            if kind not in ("restart", "reseed", "diagnostic"):
                return
        if last is None:
            return
        if board is None:
            board = self._replay_board()
        snap = self._banner_for(board)
        # `state` is derivable from `parts`; bans/mutes already ride on the
        # `edit` entries — keep the per-entry snapshot to what only it knows.
        last["banner"] = {
            "ready": snap["ready"],
            "text": snap["text"],
            "parts": snap["parts"],
        }

    # ---------------------------------------- the living proposal banner

    #: Direction buckets rendered as draft connectors; unresolved direction
    #: renders as the "for/from" alternate (the sign-flip uncertainty as text).
    _BUCKET_CONN = {
        "me_for_them": "for",
        "them_for_me": "from",
        "tell_them": "to tell",
        "ask_them": "to ask",
    }

    def _core_facets(self) -> list[str]:
        """The topic's core slots, minus caregiver-muted ones (never empty)."""
        muted = self._edit_mutes()
        core = [
            c
            for c in (self.topic.core_facets or [])
            if c in facets.CATEGORIES and c not in muted
        ]
        if core:
            return core
        return [c for c in ("what", "how") if c not in muted] or ["what"]

    def _weave(self, board: facets.Board) -> dict[str, str]:
        """Ban/mute-aware FRONTIER values — what the draft is woven from.

        Per slot: the leading family's deepest confirmed member (one yes —
        verify-on-lock covers thin locks), retreating to the best positive
        member when nothing finer is confirmed. The draft therefore says
        "tingling", not "discomfort", the moment tingling is confirmed — and
        coarsens honestly if the fine value later loses support. Banned
        values are floored on the board, so families re-route around them.
        """
        muted = self._edit_mutes()
        out: dict[str, str] = {}
        for cat in facets.CATEGORIES:
            if cat in muted:
                continue
            top = facets.frontier(board, cat, self._edges)
            if top is not None and top[1] > 0:
                out[cat] = top[0]
        return out

    def _template_draft(self, weave: dict[str, str]) -> str:
        """Deterministic early draft — structured ambiguity, ellipsis while open.

        "I need/want something for/from Rob …": slashed alternates render the
        undecided dimensions (need-vs-want; the direction buckets) and
        collapse as evidence arrives; the trailing ellipsis says "still
        working". Costs nothing, so it can populate the banner the moment the
        first core slot converges; the LLM weave takes over at readiness.
        """
        bits = ["I need/want", weave.get("what", "something")]
        how = weave.get("how", "")
        bucket = next(
            (k for k, v in facets.DIRECTION_BUCKETS.items() if v == how), None
        )
        if how and bucket is None:
            bits.append(f"— {how} —")
        if self.topic.direction or "who" in weave:
            conn = self._BUCKET_CONN.get(bucket or "", "for/from")
            bits.append(f"{conn} {weave.get('who', 'someone')}")
        if "when" in weave:
            bits.append(f", {weave['when']}")
        if "where" in weave:
            bits.append(f", {weave['where']}")
        if "why" in weave:
            bits.append(f"because {weave['why']}")
        return " ".join(bits).replace(" ,", ",") + " …"

    async def _refresh_draft(self, board: facets.Board) -> None:
        """Refresh the woven draft — only when the weave actually changed.

        The LLM weave runs only at board-readiness and on weave change (the
        cost cap: leader changes are rare); below readiness the banner
        renders the code template. Never raises — a failed weave just keeps
        the template until the next change.
        """
        weave = self._weave(board)
        if weave == self._draft_weave and (
            self._draft_text or not self._board_ready(board)
        ):
            return
        self._draft_weave = dict(weave)
        self._draft_text = ""
        if not weave or self._reasoner is None or not self._board_ready(board):
            return
        try:
            action = await self._reasoner.synthesize(
                leaders=weave,
                history=self._history,
                rejected=None,
                **self._reasoner_ctx(),
            )
        except ReasonerError:
            return  # the template still shows; retried on the next change
        self._draft_text = action.content

    def banner(self) -> dict:
        """The living proposal banner — the cockpit's evolving draft.

        States: ``pending`` (no core slot has signal yet — the glowing
        "Pending synthesis…") and ``draft`` (an utterance with per-part
        confidence bands). ``ready`` mirrors board-readiness — the
        propose-ready vibrance (≈ the conjunction of per-slot posteriors
        crossing ~0.5, the point where proposing IS the best question; see
        the research audit). Bans and mutes ride along so the cockpit can
        strike / dim them.
        """
        return self._banner_for(self._replay_board())

    def _banner_for(self, board: facets.Board) -> dict:
        """``banner()`` against an already-replayed board.

        Split out so the per-entry snapshot (``_stamp_banner``) can reuse the
        board ``_advance`` already holds instead of replaying it again.
        """
        bans = self._edit_bans()
        base = {
            "state": "pending",
            "text": "",
            "ready": False,
            "parts": [],
            "banned": [
                {"category": cat, "value": v}
                for cat in facets.CATEGORIES
                for v in sorted(bans.get(cat, ()))
            ],
            "muted": sorted(self._edit_mutes()),
        }
        if (
            not self._opened
            or self._outcome == "emergency"
            or self._seed_values is None
        ):
            return base
        weave = self._weave(board)
        if not any(c in weave for c in self._core_facets()):
            return base  # nothing real to draft from yet
        parts = [
            {
                "category": cat,
                "value": val,
                "band": (
                    "locked"
                    if self._family_confident(board, cat)
                    else "working"
                ),
            }
            for cat, val in weave.items()
        ]
        text = (
            self._draft_text
            if self._draft_text and self._draft_weave == weave
            else self._template_draft(weave)
        )
        return {
            **base,
            "state": "draft",
            "text": text,
            "ready": self._board_ready(board),
            "parts": parts,
        }

    def accept(self) -> RoundEvent:
        """✓ on the banner: conclude the round with the current draft.

        The caregiver is the stopping policy — the engine never proposes on
        its own. Accept turns the live draft into the confirmed utterance,
        recorded exactly like a confirmed synthesis so the dataset keeps one
        shape. Template-stage accepts collapse the alternates ("need/want" →
        "need") and close the ellipsis.
        """
        if not self._opened:
            raise RuntimeError("accept() called before open()")
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        board = self._replay_board() if self._seed_values is not None else None
        weave = self._weave(board) if board is not None else {}
        if not weave:
            raise RuntimeError("nothing to accept yet — no confirmed details")
        if self._draft_text and self._draft_weave == weave:
            text = self._draft_text
        else:
            text = (
                self._template_draft(weave)
                .replace("I need/want", "I need")
                .replace("for/from", "for")
                .rstrip(" …")
                + "."
            )
        self._history.append(
            {
                "kind": "synthesis",
                "text": text,
                "answer": Answer.YES.value,
                "rationale": "Accepted from the live proposal banner.",
                "slots": dict(weave),
            }
        )
        self._pending = None
        self._outcome = "synthesized"
        self._final_utterance = text
        return RoundEvent(
            kind="synthesized",
            text=text,
            query_index=self.query_count,
            engine=self.engine,
        )

    #: Slot synonyms + dismissal stems for the ✗-note parser. Deterministic
    #: and strict on purpose: a mute needs BOTH a slot word and a dismissal.
    _SLOT_WORDS = {
        "who": ("who", "person", "people"),
        "what": ("what", "thing", "subject", "object"),
        "when": ("when", "time", "timing"),
        "where": ("where", "place", "location"),
        "why": ("why", "reason"),
        "how": ("how", "action"),
    }
    _DISMISS_STEMS = (
        "matter",
        "ignor",
        "skip",
        "important",
        "irrelevant",
        "forget",
        "drop",
    )
    #: Words that frame a REPLACEMENT note without being the replacement —
    #: "plans seem to be dinner", "replace a visit with dinner", "she seems
    #: to be indicating the right leg". Filtered (with stopwords and the
    #: banned value's own tokens) so what remains is the replacement value.
    _REPLACE_NOISE = frozenset(
        [
            "she", "he", "they", "i", "we", "you", "her", "him", "its",
            "seem", "seems", "seemed", "specifically", "actually", "really",
            "instead", "rather", "just", "like", "replace", "replacing",
            "replaced", "indicate", "indicates", "indicating", "mean",
            "means", "meant", "say", "says", "said", "not", "no", "never",
            "think", "thinks", "probably", "maybe", "more",
        ]
    )
    #: A free-text note may BAN a woven value only when it carries a clear
    #: negation/removal cue or a replacement marker. Without one it is
    #: guiding context — the Avalanche round's augmentation note ("The news
    #: to share is that Paula wants to give Zach … tickets") mentioned the
    #: CORRECT who-anchor and the old rule banned it, blowing up a
    #: near-correct draft.
    _NEGATION_CUES = frozenset(
        [
            "not", "no", "isn't", "isnt", "never", "wrong", "without",
            "remove", "stop", "don't", "dont", "drop", "delete",
        ]
    )
    #: Replacement markers — only notes shaped like an explicit substitution
    #: mint a replacement; bare-leftover minting produced junk twice in live
    #: trials ("remove worrying" → why='remove'; the Avalanche note → who
    #: gibberish).
    _REPLACE_MARKER = re.compile(
        r"(?:seems?\s+to\s+be|seemed\s+to\s+be|should\s+be|"
        r"replace\b.*?\bwith|change\b.*?\bto|make\s+it)\s+",
        re.IGNORECASE,
    )

    @classmethod
    def _parse_mute(cls, note: str) -> str | None:
        """The slot a note dismisses ("the when doesn't matter"), or None."""
        lowered = note.casefold()
        if not any(stem in lowered for stem in cls._DISMISS_STEMS):
            return None
        tokens = set(re.findall(r"[a-z']+", lowered))
        for cat, words in cls._SLOT_WORDS.items():
            if any(w in tokens for w in words):
                return cat
        return None

    async def edit(self, note: str) -> RoundEvent:
        """✗ on the banner: a real-time edit to the evolving proposal.

        The note is interpreted against the DRAFT, not the whole world —
        deterministically: a slot dismissal MUTES the slot (dimmed + struck;
        excluded from questioning and speech); a note matching a woven value
        BANS that value (strike-through; floored on the board; gated out of
        future questions). A note matching neither becomes ordinary guiding
        context — nothing the caregiver types is dropped. Edits are history
        entries: replayable, undoable, recorded.
        """
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        note = note.strip()
        if not note:
            if self._pending is not None:
                return self._pending_event()
            return await self._advance()
        mute = self._parse_mute(note)
        if mute is not None:
            self._history.append(
                {"kind": "edit", "text": note, "answer": None, "mute": mute}
            )
            self._pending = None
            return await self._advance()
        if self._seed_values is not None and self._edit_intent(note):
            board = self._replay_board()
            for cat, val in self._weave(board).items():
                if facets.mentions(note, val):
                    entry: dict = {
                        "kind": "edit",
                        "text": note,
                        "answer": None,
                        "ban": {"category": cat, "value": val},
                    }
                    # Replacement semantics: "X seems to be Y" / "replace X
                    # with Y" — what FOLLOWS the marker (minus the banned
                    # value's words and framing noise) is the replacement,
                    # minted at context strength. Marker-gated: leftover
                    # heuristics minted junk twice in live trials.
                    mint = self._replacement_value(note, val)
                    if mint:
                        entry["mint"] = {"category": cat, "value": mint}
                    self._history.append(entry)
                    self._pending = None
                    return await self._advance()
        return await self.add_context(note)  # no edit intent — guiding context

    @classmethod
    def _edit_intent(cls, note: str) -> bool:
        """Whether a free-text note means REMOVE/REPLACE rather than inform.

        A ban needs a negation/removal cue ("no, not a drink", "remove
        worrying") or an explicit replacement marker ("plans seem to be
        dinner"). An augmentation note that merely MENTIONS woven values is
        guiding context — never a ban.
        """
        tokens = set(re.findall(r"[a-z']+", note.casefold()))
        if tokens & cls._NEGATION_CUES:
            return True
        return cls._REPLACE_MARKER.search(note) is not None

    @classmethod
    def _replacement_value(cls, note: str, banned: str) -> str:
        """The replacement an explicitly-marked note proposes, or "".

        Marker-gated: only text FOLLOWING a replacement marker ("seems to
        be …", "replace … with …", "should be …") is considered — then the
        banned value's tokens, stopwords, and framing noise are dropped and
        the result capped at four words. "remove worrying" has no marker →
        plain ban, nothing minted.
        """
        m = cls._REPLACE_MARKER.search(note)
        if m is None:
            return ""
        tail = note[m.end():]
        banned_tokens = facets._content_tokens(banned)
        kept: list[str] = []
        for raw in re.findall(r"[A-Za-z][A-Za-z']*", tail):
            token = raw.casefold()
            if token in cls._REPLACE_NOISE or token in facets._STOPWORDS:
                continue
            stem = facets._stem(token)
            if any(facets._tokens_match(stem, b) for b in banned_tokens):
                continue
            kept.append(token)
        return " ".join(kept[:4])

    async def replace(self, category: str, old: str, new: str) -> RoundEvent:
        """A precise banner edit: the caregiver clicked a draft segment.

        The synthesis editor's primary action (owner design, 06-11): the
        clicked segment identifies the (category, value) EXACTLY — no note
        parsing, no guessing. ``new`` may come from the candidate dropdown
        or be typed free text (a word, or a grouped phrase like "Colorado
        Avalanche tickets"); an empty ``new`` means "remove this detail"
        (the slot is muted). Refine-or-replace at replay: a ``new`` that
        lexically EXTENDS ``old`` keeps it as the parent (the draft deepens,
        nothing banned); otherwise ``old`` is struck and ``new`` stands in
        at context strength.
        """
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        if category not in facets.CATEGORIES:
            raise RuntimeError(f"unknown category: {category!r}")
        old = " ".join(old.split())
        new = " ".join(new.split())[:60]
        if not new:
            self._history.append(
                {
                    "kind": "edit",
                    "text": f"remove {category}: {old}" if old else f"remove {category}",
                    "answer": None,
                    "mute": category,
                }
            )
        else:
            self._history.append(
                {
                    "kind": "edit",
                    "text": f"{old} → {new}" if old else new,
                    "answer": None,
                    "replace": {"category": category, "old": old, "new": new},
                }
            )
        self._pending = None
        return await self._advance()

    async def restate(self) -> None:
        """⟳ on the banner: say the same draft slightly differently.

        Owner design: a caregiver-triggered rephrase — same content,
        different wording (swapped verbs/nouns) — for when the draft is
        structurally right but reads wrong. Touches only the draft cache;
        the board, history, and the pending question are untouched. Soft
        failure: any error leaves the current draft as it was.
        """
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        if self._reasoner is None:
            raise RuntimeError("no language model is configured — cannot restate")
        if self._seed_values is None:
            raise RuntimeError("nothing to restate yet")
        board = self._replay_board()
        weave = self._weave(board)
        if not weave:
            raise RuntimeError("nothing to restate yet — no confirmed details")
        current = (
            self._draft_text
            if self._draft_text and self._draft_weave == weave
            else self._template_draft(weave)
        )
        # The existing rephrase machinery is exactly restate's semantics:
        # "kinda" = close — keep the gist, change the wording.
        action = await self._reasoner.synthesize(
            leaders=weave,
            history=self._history,
            rejected=[(current, Answer.KINDA.value)],
            **self._reasoner_ctx(),
        )
        self._draft_text = action.content
        self._draft_weave = dict(weave)

    def _edit_mutes(self) -> set[str]:
        """Slots the caregiver dismissed via ✗-edits (recomputed → undo-safe)."""
        return {
            h["mute"]
            for h in self._history
            if h.get("kind") == "edit" and h.get("mute") in facets.CATEGORIES
        }

    def _edit_bans(self) -> dict[str, set[str]]:
        """Values the caregiver struck, per category — explicit bans plus the
        old value of every SWAP replacement (refining replacements keep the
        old value as the parent; nothing is struck)."""
        out: dict[str, set[str]] = {}
        for h in self._history:
            if h.get("kind") != "edit":
                continue
            ban = h.get("ban")
            if isinstance(ban, dict):
                out.setdefault(ban.get("category", ""), set()).add(
                    ban.get("value", "")
                )
            rep = h.get("replace")
            if isinstance(rep, dict):
                old = rep.get("old", "")
                new = rep.get("new", "")
                if old and new and not facets.value_extends(new, old):
                    out.setdefault(rep.get("category", ""), set()).add(old)
        return out

    async def _propose_question(
        self, seg: list[dict], *, dig: dict | None = None
    ) -> tuple[ReasonerAction, facets.Board, str]:
        """Ask the next question at the controller-chosen focus slot.

        ``dig`` overrides the focus policy for a clarification's phase-2 turn:
        the anchor is a detail the person has just confirmed, and the focus is
        the axis being tried instead. Everything else — the repeat gate's
        memory, the established set, bans, direction classification — is shared
        with an ordinary ask, which is the whole reason it routes through here.
        """
        assert self._reasoner is not None
        T = self.tuning
        board = self._replay_board()
        if dig is not None:
            focus, directive, split_pair = dig["axis"], "dig", None
        else:
            focus, directive, split_pair = self._pick_focus(board, seg)
        # Exploration DECAYS as yeses accrue toward synthesis — probe a fresh
        # value (profile dropped) with probability explore_decay**(yeses+1).
        # The yes-count resets after each synthesis attempt and each restart,
        # so exploration re-opens whenever the round re-opens.
        yeses = self._yes_since_last_synth(seg)
        exploratory = (
            directive not in ("split", "pin")
            and self._rng.random() < self._explore_probability(yeses)
        )
        # Each asked entry carries its asserted slot categories so the repeat
        # gate can tell a same-anchor DRILL (new category) from a reword;
        # flip-superseded questions carry None — never exempt.
        asked = [
            (h["text"], frozenset((h.get("slots") or {}).keys()))
            for h in self._history
            if h.get("kind") == "query" and h.get("text")
        ] + [(t, None) for t in self._superseded]
        # Established pairs (confident leaders) — Gate 4's zero-information set.
        established: set[tuple[str, str]] = set()
        for cat in facets.CATEGORIES:
            top = facets.leader(board, cat)
            if top is not None and top[1] >= T.facet_ready_points:
                established.add((cat, top[0]))
        vetoed = {
            (cat, val)
            for cat, vals in self._edit_bans().items()
            for val in vals
        }
        action = await self._reasoner.ask(
            board=board,
            focus=focus,
            directive=directive,
            split_pair=split_pair,
            anchor=dig["anchor"] if dig is not None else None,
            history=self._history,
            edges=self._edges,
            asked=asked,
            established=established,
            banned=self._futile_pair(seg),
            vetoed=vetoed or None,
            caregiver_hint=self._caregiver_hint(board),
            exploratory=exploratory,
            **self._reasoner_ctx(),
        )
        # W2-P: the controller asks about ONE slot, but 23% of questions in the
        # recorded trials assert nothing in it — the model answers about `what`
        # when asked for `why`. The entry still recorded the REQUESTED slot, so
        # the rotation in `_pick_focus` believed that slot had been covered
        # while it still had no leader, and kept re-selecting it: the
        # starvation loop (`why` starved 7 times while `what` was asserted 12).
        #
        # Deliberately NOT a fifth gate, which is what W2-P originally
        # specified. A gate rejects and re-asks, and W2-S measured that adding
        # constraint makes this model fail harder — doubling diagnostics and
        # the rounds that die early. Re-attributing costs nothing, discards
        # nothing, and is simply honest bookkeeping: record the slot the
        # question actually asks about. The requested one is kept alongside so
        # the divergence stays measurable instead of being erased by its fix.
        requested = focus
        if action.kind == "query" and action.slots and focus not in action.slots:
            core = self._core_facets()
            focus = next(
                (c for c in action.slots if c in core), next(iter(action.slots))
            )
            action.focus = focus
            action.focus_requested = requested
        # W2-R: a `drill` is the controller asking to NARROW a slot that
        # already has a positive leader, so a yes to the resulting question is
        # a refinement of the value the draft is currently showing — the
        # frontier, not the leader. (Parenting to the leader builds a star, and
        # the frontier of a star is an arbitrary depth-1 child; parenting to
        # the frontier builds the ladder the round actually walked.) Captured
        # HERE, at ask time, because the frontier is a fact about the board as
        # it stood before the answer — deriving it during replay would be
        # circular, since the frontier is itself read off the edges.
        #
        # Only when the drill LANDED on the slot it targeted: if the question
        # wandered to another category, the frontier of the requested slot is
        # not that value's parent, and inferring an edge across categories
        # would invent structure the round never walked.
        if directive == "drill" and action.kind == "query" and focus == requested:
            top = facets.frontier(board, focus, self._edges)
            if top is not None:
                action.drill_parent = top[0]
        # Classify the question's intent direction (person topics) — pure
        # code; replay uses the stored label to credit/flip the buckets.
        if self.topic.direction and action.kind == "query":
            who_names = [v for v, _ in facets.live(board, "who")]
            if action.slots.get("who"):
                who_names.append(action.slots["who"])
            action.direction = (
                facets.classify_direction(action.content, who_names) or ""
            )
        return action, board, focus

    def _pick_focus(
        self, board: facets.Board, seg: list[dict]
    ) -> tuple[str, str, tuple[str, str] | None]:
        """Choose the focus slot + directive — the question-strategy policy.

        Runs only when the round is NOT clarifying and no detail is owed a
        double-check; `_advance` handles both of those ahead of this.

        Priorities (code decides strategy; the model only does language):
        1. PIN the weakest slot of a just-rejected utterance — a kinda/no on a
           proposal means one of its details is off; target it instead of
           re-confirming the parts that already scored (this automates the
           "FOCUS on WHAT" note the caregiver had to type in two trials).
        2. PROBE a core slot with no positively-led contender — coverage first,
           so synthesis is never missing its essential pieces.
        3. SPLIT a slot whose top two contenders are tied — matching scores
           carry no decision, so separate them.
        4. Every core slot confident → PROBE an empty modifier slot (fresh
           coverage beats re-drilling).
        5. DRILL a LIVE slot — any slot with a positive leader, core or not,
           that has not RETIRED. Core slots outrank modifiers; unestablished
           leaders outrank established ones (coverage first); then the
           topic's ``facet_priority`` order (data, not logic — e.g. my_people
           ranks "where" last: usually implied in caregiving), then weakest
           leader.

        RETIREMENT (the 06-11 metronome fix — ~14 tail drills on who at
        +25.5): a slot whose leader DOMINATES (>= retire_ready_x * ready
        points with the runner-up at <= half the leader) leaves the
        probe/drill/pin pool. It stays split-eligible — dominance and tie are
        mutually exclusive, so scores re-converging reopens it by themselves —
        and restarts rebuild the board, which can un-retire.

        ROTATION: a category is never focused more than MAX_CATEGORY_RUN
        turns in a row — unless the run is WORKING (its latest question got a
        yes/kinda): a rising drill-ladder (tidy → cleanup task → dishes) may
        extend, and ends on the first miss.
        """
        T = self.tuning
        core = self._core_facets()
        muted = self._edit_mutes()
        others = [
            c for c in facets.CATEGORIES if c not in core and c not in muted
        ]
        recent = [
            h.get("focus")
            for h in seg
            if h.get("kind") == "query" and h.get("focus")
        ][-MAX_CATEGORY_RUN:]

        def fresh(cat: str) -> bool:
            if recent.count(cat) < MAX_CATEGORY_RUN:
                return True
            return self._run_working(seg, cat)

        def first_fresh(cands: list[str]) -> str | None:
            for c in cands:
                if fresh(c):
                    return c
            return None

        # 1. After a rejected utterance: pin its weakest used slot until it is
        #    confident (then fall through to the normal policy).
        last_syn = next(
            (h for h in reversed(seg) if h.get("kind") == "synthesis"), None
        )
        if last_syn is not None and last_syn.get("answer") in (
            Answer.NO.value,
            Answer.KINDA.value,
        ):
            used = last_syn.get("slots") or {}
            ranked_used = sorted(
                used.items(),
                key=lambda cv: board.get(cv[0], {}).get(cv[1], 0.0),
            )
            for cat, _val in ranked_used:
                if cat not in facets.CATEGORIES or cat in muted:
                    continue
                if self._family_confident(board, cat):
                    continue
                if fresh(cat):
                    return cat, "pin", None

        # 2. Unestablished core slots — no family confirmed above zero yet.
        open_core = []
        for cat in core:
            fams = facets.families(board, cat, self._edges)
            if not fams or fams[0][1] <= 0:
                open_core.append(cat)
        cat = first_fresh(open_core)
        if cat is not None:
            return cat, "probe", None

        # 3. Tied top contenders anywhere (core first) — split them.
        for cat in core + others:
            pair = facets.tied_top(board, cat, margin=T.facet_split_margin)
            if pair is not None and fresh(cat):
                return cat, "split", (pair[0][0], pair[1][0])

        # 4. Every core slot is confident → enrich an empty modifier slot.
        if all(self._family_confident(board, c) for c in core):
            open_other = []
            for c in others:
                fams = facets.families(board, c, self._edges)
                if not fams or fams[0][1] <= 0:
                    open_other.append(c)
            alt = first_fresh(open_other)
            if alt is not None:
                return alt, "probe", None

        # 5. Drill a live slot (core or not; retired slots are out —
        #    re-drilling a settled answer is the metronome). Rank: core block
        #    first; within a block, UNESTABLISHED families (below ready) before
        #    established ones (coverage is information; refinement can wait);
        #    within a band, the topic's facet_priority order — so for people
        #    topics a vague established "what" outranks an established "when"/
        #    "where" — and weakest family last as the final tie-break.
        order = {c: i for i, c in enumerate(self._facet_order())}

        def _mass(c: str) -> float:
            fams = facets.families(board, c, self._edges)
            return fams[0][1] if fams else 0.0

        pool = [
            c
            for c in facets.CATEGORIES
            if c not in muted and not self._retired(board, c) and _mass(c) > 0
        ]
        pool.sort(
            key=lambda c: (
                c not in core,
                _mass(c) >= T.facet_ready_points,
                order.get(c, len(order)),
                _mass(c),
            )
        )
        cat = first_fresh(pool)
        if cat is not None:
            return cat, "drill", None
        # Nothing live and fresh — fall back to the weakest core leader so a
        # turn always has a focus (board-ready synthesis usually fires first).
        ranked_core = sorted(
            core, key=lambda c: (facets.leader(board, c) or ("", 0.0))[1]
        )
        cat = first_fresh(ranked_core) or ranked_core[0]
        return cat, "drill", None

    def _retired(self, board: facets.Board, cat: str) -> bool:
        """Whether `cat`'s leading FAMILY dominates — excluded from probe/drill/pin.

        Dominance ratio, not margin: family mass >= retire_ready_x * ready
        points AND the rival family <= half. (A margin rule retires whatever
        is two clean yeses ahead — which mid-round is usually the vague
        leader that most needs drilling; a ratio targets settled slots like
        who=Rob at +25.5 vs +10 while leaving what at +6 vs +4 live.) Read
        at family level: drilling WITHIN the leading family is the frontier's
        job, not the focus rotation's.
        """
        T = self.tuning
        ranked = facets.families(board, cat, self._edges)
        if not ranked or ranked[0][1] < T.retire_ready_x * T.facet_ready_points:
            return False
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        return runner <= ranked[0][1] / 2

    def _facet_order(self) -> list[str]:
        """Drill tie-break order: the topic's facet_priority, then the rest."""
        listed = [
            c for c in (self.topic.facet_priority or []) if c in facets.CATEGORIES
        ]
        return listed + [c for c in facets.CATEGORIES if c not in listed]

    @staticmethod
    def _run_working(seg: list[dict], cat: str) -> bool:
        """Whether `cat`'s current focus run is producing — last answer yes/kinda.

        Lets a working drill-ladder extend past MAX_CATEGORY_RUN; the first
        miss (no / not-sure) ends the extension and rotation applies again.
        """
        for h in reversed(seg):
            if h.get("kind") != "query":
                continue
            if h.get("focus") != cat:
                return False  # the tail run belongs to another slot
            return h.get("answer") in (Answer.YES.value, Answer.KINDA.value)
        return False

    def _board_ready(self, board: facets.Board) -> bool:
        """Whether every core slot has a clear, confirmed leader.

        Drives the banner's propose-ready vibrance (≈ the conjunction of
        per-slot posteriors crossing ~0.5 — see the research audit) and the
        LLM-weave trigger. Caregiver-muted slots are excluded; confidence is
        read at FAMILY level, so a confirmed idea fragmented across its own
        refinements still counts as established.
        """
        return all(self._family_confident(board, c) for c in self._core_facets())

    def _explore_probability(self, yeses: int) -> float:
        """Probability the next question probes fresh: ``explore_decay**(yeses+1)``.

        High when few yeses have accrued toward synthesis, decaying as the
        round homes in (see config.ReasoningTuning.explore_decay).
        """
        return self.tuning.explore_decay ** (yeses + 1)

    def _inject_direction_buckets(
        self, seeds: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        """Add the four intent buckets as standing "how" contenders.

        Person topics only (``topic.direction``). Buckets enter at zero like
        any seed — the direction layer credits them from answers.
        """
        if not self.topic.direction:
            return seeds
        out = dict(seeds)
        how = list(out.get("how") or [])
        for bucket in facets.DIRECTION_BUCKETS.values():
            if all(bucket.casefold() != v.casefold() for v in how):
                how.append(bucket)
        out["how"] = how
        return out

    def _caregiver_hint(self, board: facets.Board) -> str:
        """The who-leader's name when the care-first ask-order prior applies.

        Applies only when: the topic tracks direction, the who-leader is
        positively confirmed AND matches a known caregiver, and no direction
        bucket has positive evidence yet. An ASK-ORDER prior only — it adds no
        points, so the honest tile stays honest and genuine concern ABOUT a
        caregiver is never suppressed.
        """
        if not self.topic.direction or not self.caregivers:
            return ""
        top = facets.leader(board, "who")
        if top is None or top[1] <= 0:
            return ""
        who = top[0]
        who_cf = who.casefold()
        if not any(
            cg.casefold() in who_cf or who_cf in cg.casefold()
            for cg in self.caregivers
        ):
            return ""
        for bucket in facets.DIRECTION_BUCKETS.values():
            key = facets._find(board["how"], bucket)
            if key is not None and board["how"][key] > 0:
                return ""  # direction already has evidence — prior spent
        return who

    def _futile_pair(self, seg: list[dict]) -> tuple[str, str] | None:
        """The avenue a run of "no" guesses has exhausted, if any.

        Walks the trailing run of consecutive "no" queries. A non-who pair
        asserted in >= FUTILE_STREAK of them is the drilled-and-dead value;
        failing that, a direction label repeated that often marks the whole
        intent bucket as the dead avenue (content guesses vary, the direction
        doesn't). The who-anchor itself is never banned — the asymmetric
        scoring already protects it, and banning the person would be wrong.
        """
        pair_counts: dict[tuple[str, str], int] = {}
        direction_counts: dict[str, int] = {}
        for h in reversed(seg):
            kind = h.get("kind")
            if kind in ("synthesis", "restart"):
                break
            if kind != "query":
                continue
            answer = h.get("answer")
            if answer == Answer.NOT_SURE.value or h.get("contested"):
                # W2-T: a contested double-check must never mark its own value
                # as an exhausted avenue — that would forbid the very question
                # the clarification is about to ask.
                continue
            if answer != Answer.NO.value:
                break
            for cat, val in (h.get("slots") or {}).items():
                if cat != "who":
                    pair_counts[(cat, val)] = pair_counts.get((cat, val), 0) + 1
            direction = h.get("direction")
            if direction in facets.DIRECTION_BUCKETS:
                direction_counts[direction] = direction_counts.get(direction, 0) + 1
        if pair_counts:
            (cat, val), count = max(pair_counts.items(), key=lambda kv: kv[1])
            if count >= FUTILE_STREAK:
                return (cat, val)
        if direction_counts:
            direction, count = max(direction_counts.items(), key=lambda kv: kv[1])
            if count >= FUTILE_STREAK:
                return ("how", facets.DIRECTION_BUCKETS[direction])
        return None

    def _verify_due(self, board: facets.Board) -> tuple[str, str] | None:
        """The (category, value) pair owed a double-check, if any.

        **Verify every NEW DETAIL the draft picks up** (owner, 2026-09-10).
        Whenever a slot's FRONTIER — the value the banner is actually weaving —
        rests on at most one patient yes, confirm it once, so the caregiver can
        hold one invariant: *the draft never says anything you have not
        confirmed twice.* (Rényi–Ulam: re-ask under noise; SCA: verify what
        matters.) Never re-check a pair, never check a value the caregiver
        chose herself, and never more than VERIFY_MAX_RUN in a row.

        This used to additionally require the slot to be family-CONFIDENT,
        which shut the window almost always: a detail with one yes is not
        confident enough to qualify, and the usual way it becomes confident is
        by earning the SECOND yes — which then disqualifies it. Measured across
        every recorded round, that fired **once per 56 questions**, 77% of
        rounds never checked anything at all, and 30 of the 35 checks that did
        happen were on values a caregiver NOTE had lifted over the bar — while
        63% of all confirmed details rest on exactly one answer. The confidence
        condition is gone; the rate limit replaces the per-round budget.
        """
        # A run of checks at the tail — several details landing at once.
        run = 0
        for h in reversed(self._history):
            if h.get("kind") != "query":
                continue
            if not h.get("verify"):
                break
            run += 1
        if run >= VERIFY_MAX_RUN:
            return None

        # The pairs the question IMMEDIATELY BEFORE this one asserted. Re-asking
        # one of them on the very next turn is asking the same thing twice in a
        # row, whatever the engine calls it — the check is still owed, it just
        # waits a turn. Read off the last ENTRY, not the last query: a caregiver
        # note landing between the two is precisely the case where an immediate
        # check is right, because the patient never confirmed that detail at all.
        last = self._history[-1] if self._history else None
        just_asserted: set[tuple[str, str]] = (
            {(c, v) for c, v in (last.get("slots") or {}).items()}
            if last is not None
            and last.get("kind") == "query"
            and not last.get("verify")
            else set()
        )

        verified: set[tuple[str, str]] = set()
        yes_counts: dict[tuple[str, str], int] = {}
        for h in self._history:
            if h.get("kind") == "edit":
                # Values the CAREGIVER chose (replacements / replacement
                # mints) are already confirmed by the most reliable channel —
                # double-checking them produced nonsense in the Avalanche
                # round ("Is it remove you want to use?").
                for payload, key in ((h.get("mint"), "value"), (h.get("replace"), "new")):
                    if isinstance(payload, dict):
                        cat = payload.get("category", "")
                        val = payload.get(key, "")
                        if cat and val:
                            found = facets._find(board.get(cat, {}), val)
                            verified.add((cat, found if found is not None else val))
                continue
            if h.get("kind") != "query":
                continue
            if h.get("verify"):
                for cat, val in (h.get("slots") or {}).items():
                    verified.add((cat, val))
            if h.get("answer") == Answer.YES.value:
                for cat, val in (h.get("slots") or {}).items():
                    yes_counts[(cat, val)] = yes_counts.get((cat, val), 0) + 1
                # Direction buckets are confirmed via the `direction` label,
                # not the slots map — count those yeses too, or a well-
                # confirmed bucket would draw a spurious double-check.
                d = h.get("direction")
                if d in facets.DIRECTION_BUCKETS:
                    pair = ("how", facets.DIRECTION_BUCKETS[d])
                    yes_counts[pair] = yes_counts.get(pair, 0) + 1
        core = self._core_facets()
        muted = self._edit_mutes()
        others = [
            c for c in facets.CATEGORIES if c not in core and c not in muted
        ]
        for cat in core + others:
            # Check the FRONTIER — the value the draft is actually weaving.
            top = facets.frontier(board, cat, self._edges)
            if top is None or top[1] < facets.YES_POINTS:
                # Below one full answer the frontier is a stand-in, not a
                # detail the draft is committing to. Checking it would spend a
                # question confirming a shrug.
                continue
            pair = (cat, top[0])
            if pair in verified or pair in just_asserted:
                continue
            if yes_counts.get(pair, 0) <= 1:
                return pair
        return None

    def _clarify_action(self, state: dict) -> ReasonerAction:
        """The next pointed confirm of a clarification walk — no LLM call.

        Templated on purpose. In clarifying mode the context is carried by the
        MODE — the banner shows the sentence, the conversation is demarcated,
        and the preface announces the walk aloud — which frees each question to
        name exactly one detail. That also makes a whole walk cheaper than a
        single ordinary question, and it cannot fabricate or fail a gate.
        """
        cat, value = state["next"]
        why = (
            "a double-check contradicted a detail the person had confirmed"
            if state["reason"] == "contradicted"
            else "this detail's support rose and then started to fall"
        )
        return ReasonerAction(
            kind="query",
            content=self._clarify_question(cat, value, state["parent"]),
            rationale=f"Clarifying — {why}. Confirming the draft one detail at a time.",
            preface=(
                CLARIFY_PREFACE_OPEN if state["asked"] == 0 else CLARIFY_PREFACE
            ),
            slots={cat: value},
            focus=cat,
            clarify=True,
            clarify_phase="confirm",
            clarify_detail=(cat, value),
        )

    def _score_conflict(self) -> dict | None:
        """A detail whose score ROSE and then FELL — the conflict signal (W2-U).

        Owner's rule (2026-09-10): *a solid detail should exhibit monotonic
        scores.* A value that climbs and then drops means either the belief is
        genuinely in conflict or the questions about it are bad — and in both
        cases the round should stop guessing and go and check. A value that only
        ever fell was never believed: that is an ordinary wrong guess, not a
        conflict, which is why a peak of at least one full answer is required.

        Only drops caused by an ANSWER count. A caregiver edit floors a value
        deliberately, which is the caregiver being right, not a conflict.

        Measured across every recorded round: **one per 26 questions**, with 65%
        of rounds never triggering — a live signal, not a constant one.
        """
        seg_start = len(self._history) - len(self._segment())
        peak: dict[tuple[str, str], float] = {}
        prev: dict[tuple[str, str], float] = {}
        found: dict | None = None
        asked = 0
        for i in range(seg_start, len(self._history) + 1):
            board = self._replay_board(upto=i)
            entry = self._history[i - 1] if i > seg_start else None
            by_answer = entry is not None and entry.get("kind") == "query"
            if by_answer and entry is not None and entry.get("answer"):
                asked += 1
            for cat in facets.CATEGORIES:
                for val, score in board.get(cat, {}).items():
                    key = (cat, val)
                    top = max(peak.get(key, score), score)
                    peak[key] = top
                    before = prev.get(key)
                    prev[key] = score
                    if before is None or not by_answer:
                        continue
                    if score < before and top >= facets.YES_POINTS:
                        found = {
                            "category": cat,
                            "value": val,
                            "peak": top,
                            "score": score,
                            "at_query": asked,
                        }
        return found

    def _clarify_details(self, board: facets.Board) -> list[tuple[str, str]]:
        """The draft's details, coarse→fine — what a clarification walks.

        Mined from the WEAVE (what the banner is actually saying) and expanded
        along each value's refinement chain, so "right foot" yields both "foot"
        and "right foot". The walk therefore confirms the general detail before
        the distinguishing one, which is how the owner specified it:

            "This is about pain, correct?"
            "This is about your foot, correct?"
            "This is about your RIGHT foot, correct?"

        Walking every detail rather than only the suspect one is deliberate. A
        contradiction surfaces on one value, but a composite draft does not say
        WHICH part is wrong — a "no" to pain-in-the-right-foot may be about the
        foot. The walk localizes it; that is the same logic the `pin` directive
        already uses after a rejected proposal.
        """
        out: list[tuple[str, str]] = []
        weave = self._weave(board)
        for cat in self._facet_order():
            val = weave.get(cat)
            if not val:
                continue
            for node in facets.chain(self._edges.get(cat, {}), val):
                if (cat, node) not in out:
                    out.append((cat, node))
        return out

    @staticmethod
    def _clarify_question(category: str, value: str, parent: str = "") -> str:
        """One pointed confirm for a single detail.

        When the detail REFINES another, the words that distinguish it are
        upper-cased, so the contrast against the parent just confirmed is
        visible: "foot" → "This is about RIGHT foot, correct?". That emphasis is
        for the caregiver reading it — piper voices the word the same either
        way — and it is what makes two adjacent questions about the same limb
        read as two different questions.
        """
        shown = value
        if parent:
            pset = {w.strip(".,!?'\"").casefold() for w in parent.split()}
            shown = " ".join(
                w if w.strip(".,!?'\"").casefold() in pset else w.upper()
                for w in value.split()
            )
        if category == "why":
            frame = CLARIFY_FRAME_WHY
        elif category == "how" and not facets.gerund_led(value):
            # "call them" / "bring it" need the infinitive frame; "lifting
            # things" is already a noun phrase and takes the plain one.
            frame = CLARIFY_FRAME_HOW
        else:
            frame = CLARIFY_FRAME
        return frame.format(value=shown)

    def _clarify_state(self) -> dict | None:
        """The open clarification, if any — the mode, its walk, and its next ask.

        DERIVED from history and never stored — like `_verify_due` and
        `_futile_pair` — so undo rewinds the mode for free.

        **Two triggers** (owner, 2026-09-10), both meaning "the board is
        confidently wrong and guessing further is waste":

        1. a double-check answered "no" (that entry is marked `contested`);
        2. a detail whose score rose and then fell — see `_score_conflict`.

        **Gated on there being something to clarify** (owner, 2026-09-10). The
        objects of a clarification are the SCORED details — the values the draft
        is actually built from. With none of those on the board there is nothing
        to talk about and the mode must not open at all; a trigger whose value
        has already fallen out of the draft would otherwise leave the round
        interrogating a phantom, which is what the first live run did.

        **Phase 1 — CONFIRM.** Each scored detail in turn, the conflicted one
        first, coarse→fine. A "no" LOCALIZES the error onto one detail and ends
        the clarification; normal questioning resumes with that detail knocked
        down. "kinda"/"not sure" settle nothing and the walk continues.

        **Phase 2 — DIG.** Every detail came back yes, so the details are not
        the problem: *the framing is* (owner). The anchor is right and the round
        is relating it wrongly, so a dig KEEPS the anchor and changes the AXIS —
        a confirmed `who` is dug at from what / when / where / why / how. A "yes"
        finds the missing frame and ends the clarification with new information;
        anything else moves to the next axis.

        Running out of axes settles nothing, and that is not a failure and never
        ends anything: the state comes back `unresolved`, the focus policy
        releases, and the round goes back to normal questioning (owner: not a
        terminal condition — the interviewee quits when they please).

        Scoped to the live segment: a restart replaces the board wholesale, so a
        conflict about the pre-restart board no longer describes anything.
        """
        seg = self._segment()
        opened, reason = -1, ""
        for i, h in enumerate(seg):
            if h.get("kind") == "query" and h.get("contested"):
                opened, reason = i, "contradicted"
        conflict = self._score_conflict()
        if opened < 0:
            if conflict is None:
                return None
            cat, value = conflict["category"], conflict["value"]
            reason = "non-monotonic"
            opened = len(seg)  # the walk starts from here
        else:
            entry = seg[opened]
            cat, value = next(iter((entry.get("slots") or {}).items()), ("", ""))
        if not cat or not value:
            return None

        board = self._replay_board()
        details = self._clarify_details(board)
        if not details:
            return None  # nothing scored on the draft — nothing to clarify
        # The conflicted detail leads — but only while it is still ON the draft.
        # Once its score is gone it is not one of the objects to clarify any
        # more, and the details that remain are.
        if (cat, value) in details:
            details.remove((cat, value))
            details.insert(0, (cat, value))

        asked: list[dict] = []
        for h in seg[opened + 1 :] if reason == "contradicted" else seg:
            if h.get("kind") != "query" or not h.get("clarify"):
                continue
            asked.append(h)
            answer = h.get("answer")
            phase = h.get("clarify_phase", "confirm")
            if h.get("flipped_from"):
                # The caregiver re-pointed this question, so it is a DIFFERENT
                # question asserting its own slots — its answer scores the board
                # normally but cannot be read as confirming or denying the
                # detail the step was about. The step counts as asked; it just
                # does not decide anything.
                continue
            if phase == "confirm" and answer == Answer.NO.value:
                return None  # localized — that detail is the wrong one
            if phase == "dig" and answer == Answer.YES.value:
                return None  # the missing frame is found

        state = {
            "reason": reason,
            "category": cat,
            "value": value,
            "details": details,
            "asked": len(asked),
            "phase": "",
            "next": None,
            "parent": "",
            "anchor": details[0],
            "axis": "",
            "unresolved": True,
            "conflict": conflict if reason == "non-monotonic" else None,
        }
        if len(asked) >= MAX_CLARIFY_QUERIES:
            return state

        # Phase 1: any scored detail not yet put to the person. Keyed on the
        # DETAIL, not the question text — a flip rewrites the text, and keying
        # on text meant a flipped clarification was re-asked verbatim forever.
        # Text is kept as the fallback for entries written before the detail was
        # recorded.
        done = {tuple(d) for h in asked if (d := h.get("clarify_detail"))}
        done_text = {h.get("text", "") for h in asked}
        for d_cat, d_val in details:
            if (d_cat, d_val) in done:
                continue
            parent = self._edges.get(d_cat, {}).get(d_val, "")
            if self._clarify_question(d_cat, d_val, parent) not in done_text:
                state.update(
                    phase="confirm", next=(d_cat, d_val), parent=parent,
                    unresolved=False,
                )
                return state

        # Phase 2: every detail held, so dig at the anchor from another axis.
        tried = {
            h.get("focus_requested") or h.get("focus", "")
            for h in asked
            if h.get("clarify_phase") == "dig"
        }
        for axis in self._clarify_dig_axes(board, details[0][0]):
            if axis not in tried:
                state.update(phase="dig", axis=axis, unresolved=False)
                return state
        return state

    def _clarify_dig_axes(self, board: facets.Board, anchor_cat: str) -> list[str]:
        """The axes a dig tries, in order — every facet but the anchor's own.

        Owner's rule (2026-09-10): a yes to the clarification says the DETAIL is
        right, so what is wrong is how the round is framing it. Keep the anchor,
        change the axis — a confirmed `who` is dug at from what / when / where /
        why / how. Unestablished slots come first: a missing frame is likelier
        to be a dimension nothing has been pinned on than one that already
        carries a value.
        """
        muted = self._edit_mutes()
        axes = [
            c for c in self._facet_order() if c != anchor_cat and c not in muted
        ]
        axes.sort(key=lambda c: self._family_confident(board, c))
        return axes

    @property
    def clarifications(self) -> list[dict]:
        """Every clarification this round opened, and how each one closed.

        Recorded for autopsy. An `unresolved` entry states a fact about the
        DIALOGUE — the walk did not settle anything — and never a judgement
        about the person, which the engine has no standing to make and could not
        distinguish from its own questions being bad.
        """
        out: list[dict] = []
        episode: dict | None = None
        for h in self._history:
            if h.get("kind") != "query":
                continue
            if h.get("contested"):
                episode = {
                    "reason": "contradicted",
                    "category": "",
                    "value": "",
                    "trigger_question": h.get("text", ""),
                    "attempts": [],
                    "outcome": "open",
                }
                cat, value = next(iter((h.get("slots") or {}).items()), ("", ""))
                episode["category"], episode["value"] = cat, value
                out.append(episode)
                continue
            if not h.get("clarify"):
                continue
            if episode is None or episode["outcome"] != "open":
                episode = {
                    "reason": "non-monotonic",
                    "category": h.get("focus", ""),
                    "value": (h.get("slots") or {}).get(h.get("focus", ""), ""),
                    "trigger_question": "",
                    "attempts": [],
                    "outcome": "open",
                }
                out.append(episode)
            answer = h.get("answer")
            phase = h.get("clarify_phase", "confirm")
            episode["attempts"].append(
                {"text": h.get("text", ""), "answer": answer, "phase": phase}
            )
            if h.get("flipped_from"):
                # Decides nothing — the caregiver re-pointed the question, so
                # its answer is about ITS slots. Mirrors `_clarify_state`.
                continue
            if phase == "confirm" and answer == Answer.NO.value:
                episode["outcome"] = "localized"
            elif phase == "dig" and answer == Answer.YES.value:
                episode["outcome"] = "reframed"
            elif len(episode["attempts"]) >= MAX_CLARIFY_QUERIES:
                episode["outcome"] = "unresolved"
        for ep in out:
            held = [
                a for a in ep["attempts"] if a.get("phase", "confirm") == "confirm"
            ]
            if ep["outcome"] == "open" and held and all(
                a["answer"] == Answer.YES.value for a in held
            ):
                ep["outcome"] = "confirmed"
        return out

    @staticmethod
    def _confirmed_pairs(board: facets.Board) -> set[tuple[str, str]]:
        """Pairs with at least one full yes worth of consensus (score >= 1)."""
        return {
            (cat, val)
            for cat in facets.CATEGORIES
            for val, score in board.get(cat, {}).items()
            if score >= 1.0
        }

    def _stalled(self) -> bool:
        """Whether the last ``stall_window`` answers produced zero progress.

        Progress = some pair newly reaching a confirmed score (>= 1.0), or a
        synthesis attempt. Sparse kindas used to keep resetting the no-streak
        while the round went nowhere (runs of 9/10/12 in one trial) — this
        trigger measures the board, not the answer pattern. 0 disables.
        """
        window = self.tuning.stall_window
        if window <= 0 or self._seed_values is None:
            return False
        start = 0
        for i, h in enumerate(self._history):
            if h.get("kind") == "restart":
                start = i + 1
        answered = [
            i
            for i in range(start, len(self._history))
            if self._history[i].get("kind") == "query"
            and self._history[i].get("answer")
        ]
        if len(answered) < window:
            return False
        cut = answered[-window]
        if any(
            self._history[i].get("kind") == "synthesis"
            for i in range(cut, len(self._history))
        ):
            return False
        before = self._confirmed_pairs(self._replay_board(upto=cut))
        now = self._confirmed_pairs(self._replay_board())
        return now <= before

    def board_record(self) -> dict:
        """The round's board evolution, for the recorded dataset / autopsies.

        Seeds, the final replayed board, and any restart snapshots — so a
        trial autopsy can reconstruct exactly what the controller believed
        without re-deriving it from slots.
        """
        if self._seed_values is None:
            return {}
        record: dict = {
            "seeds": self._seed_values,
            "final": facets.snapshot(self._replay_board()),
        }
        # Refinement edges (child → parent per slot) — autopsies can see the
        # dive structure, not just the flat scores.
        edges = {cat: dict(m) for cat, m in self._edges.items() if m}
        if edges:
            record["edges"] = edges
        # Restart markers are stripped from the recorded `queries` (they are
        # belief control, not conversation), which used to lose WHERE each
        # restart happened. `after_query` carries the position instead — the
        # information without putting an internal marker in front of a
        # caregiver reading the transcript. (W2-O)
        restarts: list[dict] = []
        asked = 0
        for h in self._history:
            if h.get("kind") == "query":
                asked += 1
            elif h.get("kind") == "restart":
                restarts.append(
                    {
                        "reason": h.get("reason", ""),
                        "after_query": asked,
                        "board": h.get("board") or {},
                    }
                )
        if restarts:
            record["restarts"] = restarts
        return record

    # ---- segment helpers (the synthesis state machine reads these) ----

    def _segment(self) -> list[dict]:
        """History since the last restart — the live working segment.

        A restart DUMPS the noisy context, so the synthesis state machine (yes
        counts, attempt runs) reasons only over entries after the most recent
        restart marker.
        """
        start = 0
        for i, h in enumerate(self._history):
            if h.get("kind") == "restart":
                start = i + 1
        return self._history[start:]

    def _just_restarted(self) -> bool:
        """True when nothing has been answered since the last restart marker.

        Another restart would dump nothing new — so the failure path surfaces
        a diagnostic instead of spinning through pointless dumps.
        """
        seg = self._segment()
        if len(seg) == len(self._history):  # no restart marker exists yet
            return False
        return not any(h.get("kind") == "query" and h.get("answer") for h in seg)

    @staticmethod
    def _yes_since_last_synth(seg: list[dict]) -> int:
        """Count INFORMATIVE query yeses after the segment's last synthesis.

        A yes that merely re-confirmed already-established leaders carries no
        new information and does not count toward the synthesis gates (entries
        without the flag — older recordings — count as informative).
        """
        n = 0
        for h in reversed(seg):
            if h.get("kind") == "synthesis":
                break
            if (
                h.get("kind") == "query"
                and h.get("answer") == Answer.YES.value
                and h.get("informative", True)
            ):
                n += 1
        return n

    def _consec_no_streak(self) -> int:
        """Consecutive 'no'-answered queries at the tail of the history.

        Counts back from the latest entry over queries answered "no"; a "yes"
        or "kinda" (real positive signal) ends the run, while "not sure" (no
        information), caregiver context, and diagnostics are transparent. A
        restart or a synthesis attempt is a hard boundary. Drives the
        no-streak restart trigger (see soft_reset_no_streak).
        """
        streak = 0
        for h in reversed(self._history):
            kind = h.get("kind")
            if kind in ("restart", "synthesis"):
                break
            if kind != "query":
                continue  # caregiver context / diagnostics are transparent
            if h.get("contested"):
                continue  # W2-T: an ambiguity, not a no — see _replay_board
            answer = h.get("answer")
            if answer == Answer.NO.value:
                streak += 1
            elif answer == Answer.NOT_SURE.value:
                continue  # no information — neither extends nor breaks the run
            else:
                break  # yes / kinda — a real positive signal ends the run
        return streak

    def _record_yes(self, pending: ReasonerAction) -> None:
        """Log a confirmed-yes query (question + the slot values it asserted)."""
        self._yes_memory.add(
            question=pending.content,
            needs=list(pending.slots.values()),
            topic_id=self.topic.id,
            round_id=self.round_id,
            session_id=self.session_id,
        )

    async def _restart(self, reason: str) -> None:
        """Dump the no/kinda influence and rebuild the board — the recovery move.

        Implements the round's one recovery mechanism (fail-loop, no-streak,
        and synthesis-exhausted all trigger it): the new board keeps ONLY

        - the contributions of this round's confirmed-yes answers, and
        - the caregiver-context boosts (seed context + mid-round notes stay
          visible to prompts as well),

        both round-specific by construction, plus fresh deliberately-broad
        probes from a profile-free seed call. Recorded as a ``restart`` marker
        carrying the rebuilt board, so replay/undo stay exact and post-restart
        prompts can hide the dumped noise. Never raises.
        """
        # 1. Keep the high-trust signal: yes answers (content pairs AND their
        #    direction-bucket credits) + caregiver context. Flip nudges are
        #    no-derived and are dumped with the rest of the no/kinda influence.
        kept = facets.empty_board()
        for h in self._history:
            kind = h.get("kind")
            if kind == "query" and h.get("answer") == Answer.YES.value:
                if h.get("slots"):
                    kept = facets.update(kept, h["slots"], Answer.YES.value)
                direction = h.get("direction")
                if self.topic.direction and direction in facets.DIRECTION_BUCKETS:
                    kept = facets.update(
                        kept,
                        {"how": facets.DIRECTION_BUCKETS[direction]},
                        Answer.YES.value,
                    )
            elif kind == "context" and h.get("slots"):
                kept = facets.apply_context(kept, h["slots"])

        # 2. Fresh, deliberately broad probes. Caregiver seed context AND the
        #    patient profile are both kept — they are caregiver signal, not
        #    guess-priors. (The profile used to be blanked here. In the 09-01
        #    trial that turned `who` from "Rob, Zach, Aaron, Julie, Ashley"
        #    into "my husband / my caregiver / a doctor" the moment a round
        #    got into trouble, and those generic relations drew four straight
        #    no's — exactly the low-information questions the caregiver's
        #    ordered name list exists to prevent. What a restart dumps is the
        #    no/kinda SCORE history, never the identity prior. See §1d C5.)
        fresh: dict[str, list[str]] = {}
        if self._reasoner is not None:
            try:
                ctx = self._reasoner_ctx()
                fresh = await self._reasoner.seed_board(
                    seed_universal_wants=self.topic.seed_universal_wants, **ctx
                )
            except ReasonerError:
                fresh = {}  # recovery must not depend on a flaky seed call

        board = facets.merge_values(kept, self._inject_direction_buckets(fresh))
        self._history.append(
            {"kind": "restart", "reason": reason, "board": facets.snapshot(board)}
        )
        log.info(
            "round restarted (%s): kept yes/context signal, %d fresh probe values",
            reason,
            sum(len(v) for v in fresh.values()),
        )

    def _replay_board(self, upto: int | None = None) -> facets.Board:
        """Rebuild the facet board from seeds + history (optionally truncated).

        Walks events in order so undo is just pop-and-recompute:
        - start from the zero-scored seed board;
        - a [context] entry credits the values the caregiver's note implied
          (high-trust boost);
        - a query adds points to the (category, value) pairs it asserted
          (yes/kinda credit every pair; a no hits only the lowest-scoring
          pair — see facets.update);
        - a query carrying a DIRECTION label also moves the intent buckets:
          yes/kinda credit the asserted bucket, and a no whose who-anchor is
          positive adds a kinda-strength nudge to the MIRROR bucket (the
          "opposite" sign flip — a no on one pole of the direction attribute
          is soft evidence for the other pole);
        - a [restart] marker REPLACES the board with the snapshot it carries
          (yes/context signal kept, noise dumped, fresh probes added);
        - a synthesis entry does NOT change the board — a rejected utterance
          is rephrased, not eliminated.
        """
        board = facets.seed_board(self._seed_values or {})
        history = self._history if upto is None else self._history[:upto]
        for h in history:
            kind = h.get("kind")
            if kind == "restart":
                board = facets.restore(h.get("board") or {})
            elif kind == "context" and h.get("slots"):
                board = facets.apply_context(board, h["slots"])
            elif kind == "edit":
                # A caregiver ban floors the value — out of play, still on the
                # board (never re-minted), struck through in the banner. A
                # replacement note also MINTS what the caregiver said instead,
                # at context strength (it is caregiver signal, not a guess).
                ban = h.get("ban")
                if isinstance(ban, dict):
                    cat = ban.get("category", "")
                    val = ban.get("value", "")
                    if cat in board:
                        key = facets._find(board[cat], val)
                        board[cat][key if key is not None else val] = (
                            facets.ELIMINATE_FLOOR
                        )
                mint = h.get("mint")
                if isinstance(mint, dict):
                    cat = mint.get("category", "")
                    val = mint.get("value", "")
                    if cat in facets.CATEGORIES and val:
                        val = facets.canonical_value(board, cat, val)
                        board = facets.apply_context(board, {cat: [val]})
                rep = h.get("replace")
                if isinstance(rep, dict):
                    # The synthesis editor: refine-or-replace. An extension
                    # ("tickets" → "Avalanche tickets") keeps the old value
                    # as the parent — the lexical edge derives automatically
                    # and the frontier deepens; a genuine swap strikes it.
                    # The new value is minted VERBATIM (no canonical folding
                    # — the caregiver chose these exact words, and folding a
                    # refinement onto its own parent would erase it).
                    cat = rep.get("category", "")
                    old = rep.get("old", "")
                    new = rep.get("new", "")
                    if cat in facets.CATEGORIES and new:
                        if old and not facets.value_extends(new, old):
                            key = facets._find(board.get(cat, {}), old)
                            if key is not None:
                                board[cat][key] = facets.ELIMINATE_FLOOR
                        board = facets.apply_context(board, {cat: [new]})
            elif kind == "query":
                # W2-T: a CONTESTED double-check scores nothing at all — not
                # the pair, not the direction buckets. Its evidence is deferred
                # to the clarification that follows, which asks the same thing
                # with the context restored and is scored normally either way.
                if h.get("contested"):
                    continue
                answer = h.get("answer")
                slots = h.get("slots")
                if answer and slots:
                    board = facets.update(board, slots, answer)
                board = self._apply_direction(board, h)
        if upto is None:
            # Keep the refinement edges in lockstep with the live board
            # (derived, never stored — undo stays pop-and-recompute).
            self._edges = facets.derive_edges(
                board, self._refine_tags(), self._drill_tags()
            )
        return board

    def _refine_tags(self) -> list[tuple[str, str, str]]:
        """(category, child, parent) refinement tags from history, in order."""
        tags: list[tuple[str, str, str]] = []
        for h in self._history:
            if h.get("kind") != "query":
                continue
            refines = h.get("refines")
            slots = h.get("slots") or {}
            if not isinstance(refines, dict):
                continue
            for cat, parent in refines.items():
                child = slots.get(cat)
                if child and isinstance(parent, str):
                    tags.append((cat, child, parent))
        return tags

    def _drill_tags(self) -> list[tuple[str, str, str]]:
        """Drill-inferred (category, child, parent) links — W2-R's last resort.

        A `drill` asks to narrow the focus slot, so a YES to it makes the
        asserted value a refinement of the draft value being drilled. Only
        yeses count (a no narrows nothing) and only the focus category (a
        question may assert other slots in passing; those are not what was
        being drilled). Applied after the explicit tags and the lexical
        fallback, so the model's own answer always wins.
        """
        tags: list[tuple[str, str, str]] = []
        for h in self._history:
            if h.get("kind") != "query" or h.get("answer") != Answer.YES.value:
                continue
            parent = h.get("drill_parent")
            cat = h.get("focus")
            child = (h.get("slots") or {}).get(cat or "")
            if parent and cat and child:
                tags.append((cat, child, parent))
        return tags

    def _family_confident(self, board: facets.Board, cat: str) -> bool:
        """Confidence read at FAMILY level — refinement-aware.

        Non-negative family mass: a child's no never erodes the family, so
        an established parent stays locked while the round weaves through
        its children (see facets.families).
        """
        return facets.family_confident(
            board,
            cat,
            self._edges,
            ready_points=self.tuning.facet_ready_points,
            margin=self.tuning.facet_split_margin,
        )

    def _apply_direction(self, board: facets.Board, h: dict) -> facets.Board:
        """Fold one query's direction evidence into the intent buckets."""
        direction = h.get("direction")
        if not self.topic.direction or direction not in facets.DIRECTION_BUCKETS:
            return board
        answer = h.get("answer")
        bucket = facets.DIRECTION_BUCKETS[direction]
        if answer in (Answer.YES.value, Answer.KINDA.value):
            return facets.update(board, {"how": bucket}, answer)
        if answer == Answer.NO.value:
            # The sign flip — licensed only when the question's who-anchor is
            # positively confirmed (otherwise the no may mean "wrong person").
            who_val = (h.get("slots") or {}).get("who", "")
            key = facets._find(board["who"], who_val) if who_val else None
            if key is not None and board["who"][key] > 0:
                mirror = facets.DIRECTION_BUCKETS[facets.MIRROR[direction]]
                return facets.update(board, {"how": mirror}, Answer.KINDA.value)
        return board

    def _reasoner_ctx(self) -> dict:
        """Shared keyword context passed to every reasoner call."""
        return {
            "topic_label": self.topic.label,
            "seed_context": self.seed_context,
            "profile_context": self.profile_context,
            "topic_hint": self.topic.reasoning_hint or "",
            "emotional_state": self.emotional_state,
            "on_phase": self.on_phase,
        }

    async def _handle_reason_failure(self, reason: str) -> RoundEvent:
        """A reasoning turn failed — recover by restart, else surface a diagnostic.

        There are NO canned fallback questions. First try the context-restart
        recovery (dump the no/kinda noise that traps a model in a fail loop;
        keep caregiver context + this round's yeses) and re-ask once. If the
        model is unreachable, or the restart can't help (nothing new to dump /
        seeding itself failed), or the recovered ask fails too — return a
        DIAGNOSTIC event that tells the caregiver what failed and what to do
        (retry / add context / switch model). Reasoning is never permanently
        abandoned: retry / the next interaction re-runs it.
        """
        self._consec_failures += 1
        self.degrade_reason = reason
        level = (
            log.error
            if self._consec_failures >= MAX_CONSEC_REASON_FAILURES
            else log.warning
        )
        level(
            "reasoner failed (%s) — attempting recovery (×%d)",
            reason,
            self._consec_failures,
        )

        unreachable = "unreachable" in reason
        restart_attempted = False
        if (
            not unreachable
            and self._reasoner is not None
            and self._seed_values is not None
            and not self._just_restarted()
        ):
            restart_attempted = True
            try:
                await self._restart("fail-loop")
                action, board, focus = await self._propose_question(self._segment())
            except ReasonerError as exc:
                reason = f"{reason}; restart recovery also failed: {exc}"
                self.degrade_reason = reason
            else:
                self._consec_failures = 0
                action.rationale = (
                    f"(recovered after a context restart) {action.rationale}".strip()
                )
                self._pending = action
                self._stamp_banner(board)
                return self._event_for(action, facets.facet_view(board, focus, self._edges))

        if unreachable:
            text = (
                "The language model is unreachable. Check that the backend "
                "(e.g. Ollama) is running, then press Retry."
            )
        elif restart_attempted:
            text = (
                "The model could not produce a usable question, even after "
                "dumping unhelpful context and restarting from the confirmed "
                "answers. Press Retry, add a context note to steer it, or "
                "switch models."
            )
        else:
            text = (
                "The model could not produce a usable question. Press Retry, "
                "add a context note to steer it, or switch models."
            )
        # Stamp before the diagnostic entry lands, so the snapshot rides the
        # ANSWER that preceded the failure rather than being lost with it.
        self._stamp_banner()
        self._history.append(
            {"kind": "diagnostic", "text": reason[:300], "answer": None}
        )
        self._pending = None
        return RoundEvent(
            kind="diagnostic",
            text=text,
            engine=self.engine,
            query_index=self.query_count,
            diagnostic={
                "reason": reason[:300],
                "consecutive_failures": self._consec_failures,
                "llm_unreachable": unreachable,
                "restart_attempted": restart_attempted,
            },
        )

    def _pending_event(self) -> RoundEvent:
        """Re-emit the current pending action with its current board view."""
        assert self._pending is not None
        if self._seed_values is not None:
            board = self._replay_board()
            return self._event_for(
                self._pending, facets.facet_view(board, self._pending.focus, self._edges)
            )
        return self._event_for(self._pending)

    def _event_for(
        self, action: ReasonerAction, board_view: list[dict] | None = None
    ) -> RoundEvent:
        idx = self.query_count + (1 if action.kind == "query" else 0)
        return RoundEvent(
            kind=action.kind,
            text=action.content,
            rationale=action.rationale,
            preface=action.preface,
            query_index=idx,
            engine=self.engine,
            facets=board_view or [],
            flipped_from=action.flipped_from,
            clarifying=action.clarify,
        )


class Session:
    """One tool process — holds the patient context and spawns rounds."""

    def __init__(
        self,
        topics: Sequence[Topic],
        *,
        llm: LLMBackend | None = None,
        config: Config | None = None,
        profile: PatientProfile | None = None,
        session_id: str = "",
    ) -> None:
        self.topics = list(topics)
        self.llm = llm
        self.config = config
        self.profile = profile
        #: The API's session id, so the yes-log can be joined to the recording
        #: it came from (W2-Q). Empty for the CLI harness, which records nothing.
        self.session_id = session_id
        self.rounds: list[Round] = []
        self.emotional_state: dict[str, float] = {}
        # One yes-memory per session, shared by every round it spawns. The
        # engine only WRITES to it (a confirmed-yes log for the dataset and the
        # future interview tool); recovery reads yes-context from each round's
        # own history instead — round-specific, never cross-round. File-backed
        # for a real patient (the privacy invariant); in-memory only otherwise.
        self.yes_memory = self._build_yes_memory()

    def _build_yes_memory(self) -> YesMemory:
        if self.config and self.profile and is_real_patient(self.profile):
            return YesMemory(path=dated_path(self.config.recording_dir, self.profile.id))
        return YesMemory()

    def start_round(
        self, topic_id: str, *, seed_context: str = "", round_id: str = ""
    ) -> Round:
        """Open a round. ``round_id`` should be the id the RECORDING will use.

        It defaults to an ordinal for the CLI harness, but the API passes the
        uuid it records under — before W2-Q the two were generated separately,
        so the yes-log's "r3" could never be joined to its own round.
        """
        topic = find_topic(self.topics, topic_id)
        if topic is None:
            raise ValueError(f"Unknown topic: {topic_id!r}")
        round_ = Round(
            topic,
            llm=self.llm,
            max_queries=self.config.max_queries if self.config else 0,
            mode=self.config.mode if self.config else "training",
            seed_context=seed_context,
            profile_context=(self.profile.context or "") if self.profile else "",
            caregivers=list(self.profile.caregivers) if self.profile else [],
            emotional_state=self.emotional_state,
            tuning=self.config.reasoning if self.config else None,
            yes_memory=self.yes_memory,
            round_id=round_id or f"r{len(self.rounds) + 1}",
            session_id=self.session_id,
        )
        self.rounds.append(round_)
        return round_

    @property
    def topic_sequence(self) -> list[str]:
        """Ordered topics of the rounds so far — input to loop detection."""
        return [r.topic.id for r in self.rounds]
