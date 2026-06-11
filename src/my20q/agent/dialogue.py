"""Round state machine — the my20Q dialogue engine.

A `Session` spans one tool process and spawns `Round`s. A `Round` is one
convergence attempt under a single topic. A round ends ONLY when the caregiver
confirms a synthesized utterance with "yes" (success) — that is the sole
model-side terminator. The caregiver can still end it out-of-band (an
emergency topic short-circuit, a topic switch, or an operator safety ceiling).
Terminology and lifecycle: docs/design/beta-retool.md §2, §7.

The belief is a 5W1H **facet board** (``agent/facets.py``): per-category
contender values with additive consensus points, recomputed from history so
``undo()`` is pop-and-recompute. Each turn the controller picks a FOCUS slot
and a directive (probe an unestablished core slot / split tied contenders /
drill the vague leader); the `Reasoner` turns that into language. Crediting is
anchored to the question text, so an answer can never move a contender the
question didn't mention.

The synthesis loop (thresholds env-tunable — see `config.ReasoningTuning`):
question until ``min_yes_for_synthesis`` yeses (or the board's core slots are
confidently led) → weave the slot leaders into an utterance, placeholdering
unknown slots → on a no/kinda, rephrase up to ``rephrase_limit`` times → if
still unconfirmed, return to questioning for ``new_yes_for_resynthesis`` NEW
yeses → after ``synth_attempts_before_restart`` failed attempts, RESTART.

A **restart** is the round's one recovery mechanism, with three triggers
(failed synthesis attempts, a long run of "no", and the reasoner fail-loop):
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from my20q.agent import facets
from my20q.agent.auditor import is_repeat
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
#: env-configured one through `Session`); these module aliases are the defaults,
#: kept for readability and the tests. Override via MY20Q_* env vars (see config).
_DEFAULT_TUNING = ReasoningTuning()
MIN_YES_FOR_SYNTHESIS = _DEFAULT_TUNING.min_yes_for_synthesis
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


class Round:
    """One convergence attempt under a single topic.

    Drive it: ``await open()`` once, then alternate ``await answer(a)``
    with reading each `RoundEvent`. ``add_context`` injects caregiver
    steering; ``await undo()`` rewinds the last entry; ``await retry()``
    re-proposes after a diagnostic; ``await flip()`` re-renders the pending
    question in its opposite connotation (an action, not an answer). The
    round is over once an event of kind ``synthesized``, ``abandoned``, or
    ``emergency`` is returned (also reflected by ``is_terminal``).
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
        self._outcome: str | None = None
        self._final_utterance = ""
        self._opened = False
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
        if pending.kind == "query" and pending.direction:
            entry["direction"] = pending.direction
        if pending.kind == "query" and pending.flipped_from:
            entry["flipped_from"] = pending.flipped_from
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
        if self.topic.direction:
            who_names = [v for v, _ in facets.live(board, "who")]
            if action.slots.get("who"):
                who_names.append(action.slots["who"])
            action.direction = (
                facets.classify_direction(action.content, who_names) or ""
            )
        self._pending = action
        return self._event_for(action, facets.facet_view(board, action.focus))

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
                seeds = await self._reasoner.seed_board(
                    seed_universal_wants=self.topic.seed_universal_wants,
                    **self._reasoner_ctx(),
                )
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

            # Decide the next move from the current SEGMENT (history since the
            # last restart): question, synthesize, rephrase, or restart.
            seg = self._segment()
            last = seg[-1] if seg else None
            synth_runs, tail_run = self._synth_runs(seg)

            if last is not None and last.get("kind") == "synthesis":
                # Mid synthesis attempt — the last utterance was rejected (a
                # "yes" would have ended the round). A "kinda" rejection means
                # the CONTENT has a gap, not the wording — wording shuffles
                # can't add the missing detail (one trial burned 16 isomorphic
                # kinda-utterances), so a kinda gets at most ONE rephrase
                # before returning to questioning; a "no" keeps the full
                # budget (wrong framing genuinely needs a different angle).
                if last.get("answer") == Answer.KINDA.value:
                    limit = min(1, T.rephrase_limit)
                else:
                    limit = T.rephrase_limit
                if tail_run <= limit:
                    move = "rephrase"
                elif synth_runs >= T.synth_attempts_before_restart:
                    move = "restart"
                else:
                    move = "question"
            else:
                # Questioning — synthesize once enough yeses have accrued, or
                # EARLY once the board's core slots are confidently led (the
                # consensus-readiness path; placeholders cover the rest).
                new_yes = self._yes_since_last_synth(seg)
                ever_synthesized = any(
                    h.get("kind") == "synthesis" for h in self._history
                )
                need = (
                    T.new_yes_for_resynthesis
                    if ever_synthesized
                    else T.min_yes_for_synthesis
                )
                board = self._replay_board()
                ready = (
                    new_yes >= T.new_yes_for_resynthesis
                    and self._board_ready(board)
                )
                move = "synthesize" if (new_yes >= need or ready) else "question"

            if move == "restart":
                await self._restart("synthesis-exhausted")
                seg = self._segment()  # now fresh (post-marker)
                move = "question"

            # Operator safety ceiling — the ONLY count-based stop, off by default
            # (<= 0). It never forces a half-baked utterance; it just abandons.
            if (
                move == "question"
                and self.max_queries > 0
                and self.query_count >= self.max_queries
            ):
                self._outcome = "abandoned"
                return RoundEvent(
                    kind="abandoned", query_index=self.query_count, engine=self.engine
                )

            action: ReasonerAction | None = None
            board = self._replay_board()
            focus = ""
            if move in ("synthesize", "rephrase"):
                action = await self._propose_synthesis(board, seg, move)
            if action is None:  # questioning — or synthesis had no leaders yet
                action, board, focus = await self._propose_question(seg)
        except ReasonerError as exc:
            return await self._handle_reason_failure(str(exc))

        # Reasoning succeeded — clear any failure streak.
        self._consec_failures = 0
        self.engine = "reasoning"
        self._pending = action
        return self._event_for(action, facets.facet_view(board, focus))

    async def _propose_synthesis(
        self, board: facets.Board, seg: list[dict], move: str
    ) -> ReasonerAction | None:
        """Weave the slot leaders into an utterance (or a rephrase of one).

        Returns None when no slot has a positively-confirmed leader yet — the
        caller then keeps questioning rather than ending the round (only a
        confirmed utterance ends a round).
        """
        assert self._reasoner is not None
        leaders: dict[str, str] = {}
        for cat in facets.CATEGORIES:
            top = facets.leader(board, cat)
            if top is not None and top[1] > 0:
                leaders[cat] = top[0]
        if not leaders:
            return None
        rejected: list[tuple[str, str]] | None = None
        if move == "rephrase":
            # The rejected utterances of THIS attempt (the trailing synthesis
            # run) so the model phrases it differently — kinda = close, no =
            # wrong angle.
            rejected = []
            for h in reversed(seg):
                if h.get("kind") == "synthesis":
                    rejected.append((h.get("text", ""), h.get("answer", "")))
                else:
                    break
            rejected.reverse()
        action = await self._reasoner.synthesize(
            leaders=leaders,
            history=self._history,
            rejected=rejected,
            **self._reasoner_ctx(),
        )
        # A rephrase that near-duplicates an already-rejected utterance would
        # just earn the same rejection — bail to questioning instead (the
        # caller treats None as "keep questioning").
        if rejected and is_repeat(action.content, [u for u, _ in rejected if u]):
            log.info("rephrase near-duplicates a rejected utterance — requestioning")
            return None
        return action

    async def _propose_question(
        self, seg: list[dict]
    ) -> tuple[ReasonerAction, facets.Board, str]:
        """Ask the next question at the controller-chosen focus slot."""
        assert self._reasoner is not None
        T = self.tuning
        board = self._replay_board()
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
        asked = [
            h["text"] for h in self._history if h.get("kind") == "query" and h.get("text")
        ] + self._superseded
        # Established pairs (confident leaders) — Gate 4's zero-information set.
        established: set[tuple[str, str]] = set()
        for cat in facets.CATEGORIES:
            top = facets.leader(board, cat)
            if top is not None and top[1] >= T.facet_ready_points:
                established.add((cat, top[0]))
        action = await self._reasoner.ask(
            board=board,
            focus=focus,
            directive=directive,
            split_pair=split_pair,
            history=self._history,
            asked=asked,
            established=established,
            banned=self._futile_pair(seg),
            caregiver_hint=self._caregiver_hint(board),
            exploratory=exploratory,
            **self._reasoner_ctx(),
        )
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

        Priorities (code decides strategy; the model only does language):
        0. PIN the weakest slot of a just-rejected utterance — a kinda/no on a
           proposal means one of its details is off; target it instead of
           re-confirming the parts that already scored (this automates the
           "FOCUS on WHAT" note the caregiver had to type in two trials).
        1. PROBE a core slot with no positively-led contender — coverage first,
           so synthesis is never missing its essential pieces.
        2. SPLIT a slot whose top two contenders are tied — matching scores
           carry no decision, so separate them.
        3. DRILL the core slot with the weakest leader toward specificity; once
           every core slot is confident, probe an empty modifier slot instead.

        A category is never focused more than MAX_CATEGORY_RUN turns in a row
        when an alternative exists (the "why"-hammering guard).
        """
        T = self.tuning
        core = [c for c in (self.topic.core_facets or []) if c in facets.CATEGORIES]
        if not core:
            core = ["what", "how"]
        others = [c for c in facets.CATEGORIES if c not in core]
        recent = [
            h.get("focus")
            for h in seg
            if h.get("kind") == "query" and h.get("focus")
        ][-MAX_CATEGORY_RUN:]

        def first_fresh(cands: list[str]) -> str | None:
            for c in cands:
                if recent.count(c) < MAX_CATEGORY_RUN:
                    return c
            return None

        # 0. After a rejected utterance: pin its weakest used slot until it is
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
                if cat not in facets.CATEGORIES:
                    continue
                if facets.confident(
                    board,
                    cat,
                    ready_points=T.facet_ready_points,
                    margin=T.facet_split_margin,
                ):
                    continue
                if recent.count(cat) < MAX_CATEGORY_RUN:
                    return cat, "pin", None

        # 1. Unestablished core slots — no contender confirmed above zero yet.
        open_core = []
        for cat in core:
            top = facets.leader(board, cat)
            if top is None or top[1] <= 0:
                open_core.append(cat)
        cat = first_fresh(open_core)
        if cat is not None:
            return cat, "probe", None

        # 2. Tied top contenders anywhere (core first) — split them.
        for cat in core + others:
            pair = facets.tied_top(board, cat, margin=T.facet_split_margin)
            if pair is not None and recent.count(cat) < MAX_CATEGORY_RUN:
                return cat, "split", (pair[0][0], pair[1][0])

        # 3. Every core slot is confident → enrich an empty modifier slot;
        #    otherwise drill the weakest core leader toward specificity.
        if all(
            facets.confident(
                board, c, ready_points=T.facet_ready_points, margin=T.facet_split_margin
            )
            for c in core
        ):
            open_other = []
            for c in others:
                top = facets.leader(board, c)
                if top is None or top[1] <= 0:
                    open_other.append(c)
            alt = first_fresh(open_other)
            if alt is not None:
                return alt, "probe", None
        ranked_core = sorted(
            core, key=lambda c: (facets.leader(board, c) or ("", 0.0))[1]
        )
        cat = first_fresh(ranked_core) or ranked_core[0]
        return cat, "drill", None

    def _board_ready(self, board: facets.Board) -> bool:
        """Whether every core slot has a clear, confirmed leader.

        The consensus-readiness path to synthesis: when the core slots are all
        confidently led, the round can propose early (placeholders cover the
        modifier slots) instead of grinding out the full yes-count.
        """
        T = self.tuning
        core = [c for c in (self.topic.core_facets or []) if c in facets.CATEGORIES]
        if not core:
            core = ["what", "how"]
        return all(
            facets.confident(
                board, c, ready_points=T.facet_ready_points, margin=T.facet_split_margin
            )
            for c in core
        )

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
            if answer == Answer.NOT_SURE.value:
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
        restarts = [
            {"reason": h.get("reason", ""), "board": h.get("board") or {}}
            for h in self._history
            if h.get("kind") == "restart"
        ]
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
    def _synth_runs(seg: list[dict]) -> tuple[int, int]:
        """``(number of synthesis attempts, length of the trailing one)``.

        An "attempt" is a maximal run of consecutive synthesis entries (the
        initial utterance plus its rephrases). ``tail_run`` is 0 unless the
        segment ends in a synthesis run.
        """
        runs = 0
        prev = None
        for h in seg:
            kind = h.get("kind")
            if kind == "synthesis" and prev != "synthesis":
                runs += 1
            prev = kind
        tail = 0
        for h in reversed(seg):
            if h.get("kind") == "synthesis":
                tail += 1
            else:
                break
        return runs, tail

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

        # 2. Fresh, deliberately broad probes (profile-free; caregiver seed
        #    context is kept — it is caregiver signal, not a guess-prior).
        fresh: dict[str, list[str]] = {}
        if self._reasoner is not None:
            try:
                ctx = self._reasoner_ctx()
                ctx["profile_context"] = ""
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
            elif kind == "query":
                answer = h.get("answer")
                slots = h.get("slots")
                if answer and slots:
                    board = facets.update(board, slots, answer)
                board = self._apply_direction(board, h)
        return board

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
                return self._event_for(action, facets.facet_view(board, focus))

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
                self._pending, facets.facet_view(board, self._pending.focus)
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
    ) -> None:
        self.topics = list(topics)
        self.llm = llm
        self.config = config
        self.profile = profile
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

    def start_round(self, topic_id: str, *, seed_context: str = "") -> Round:
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
            round_id=f"r{len(self.rounds) + 1}",
        )
        self.rounds.append(round_)
        return round_

    @property
    def topic_sequence(self) -> list[str]:
        """Ordered topics of the rounds so far — input to loop detection."""
        return [r.topic.id for r in self.rounds]
