"""Round state machine — the my20Q dialogue engine.

A `Session` spans one tool process and spawns `Round`s. A `Round` is one
convergence attempt under a single topic. A round ends ONLY when the caregiver
confirms a synthesized utterance with "yes" (success) — that is the sole
model-side terminator. It never stops itself on a question count or an LLM
failure: it keeps questioning, synthesizing, rephrasing, and (after repeated
rejected utterances) DUMPING its context and reseeding, indefinitely. The
caregiver can still end it out-of-band (an emergency topic short-circuit, a topic
switch, or an operator safety ceiling). Terminology and lifecycle:
docs/design/beta-retool.md §2, §7.

The synthesis loop (all thresholds are env-tunable — see `config.ReasoningTuning`):
question until ``min_yes_for_synthesis`` yeses → propose an utterance → on a
no/kinda, rephrase up to ``rephrase_limit`` times → if still unconfirmed, return
to questioning for ``new_yes_for_resynthesis`` NEW yeses and try again → after
``synth_attempts_before_reseed`` failed attempts, dump context and reseed (half
the seeds from the session's memorized yeses, half fresh random on-topic).

Reasoning mode (LLM available) — the `Reasoner` proposes each query and the
synthesis. Fallback mode (LLM briefly unreachable) — a transient, deterministic
walk of the topic's fallback bank that ONLY asks questions; it never synthesizes
and never ends the round, and the reasoner is retried every turn.

The engine is async: the FastAPI layer awaits it directly; a CLI wraps
it in `asyncio.run`. The round history is a flat list, so `undo()` is
just a truncation — no separate rewind bookkeeping.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from my20q.agent import hypotheses as hyp
from my20q.agent.hypotheses import Hypothesis
from my20q.agent.reasoner import (
    MAX_HYPOTHESES,
    Reasoner,
    ReasonerAction,
    ReasonerError,
)
from my20q.agent.safety import EMERGENCY_SCREEN
from my20q.config import Config, Mode, ReasoningTuning
from my20q.llm.base import LLMBackend
from my20q.profiles import PatientProfile, is_real_patient
from my20q.recording.yes_memory import YesMemory, dated_path
from my20q.topics import FallbackQuestion, Topic, find_topic

log = logging.getLogger(__name__)


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    KINDA = "kinda"
    NOT_SURE = "not_sure"


_AFFIRMED = {Answer.YES.value, Answer.KINDA.value}

EventKind = Literal["query", "synthesis", "emergency", "synthesized", "abandoned"]
Engine = Literal["reasoning", "fallback"]

#: Consecutive reasoning failures after which the log level escalates. Reasoning is
#: NEVER permanently abandoned — every turn retries the reasoner, because only the
#: reasoner can produce the utterance a round needs to end. A failed turn just
#: degrades to a fallback QUESTION for that turn (see _handle_reason_failure).
MAX_CONSEC_REASON_FAILURES = 3

#: Default behavior knobs — the single source of truth for the tuning defaults. A
#: `Round` uses the `ReasoningTuning` it is handed (the API/CLI thread the
#: env-configured one through `Session`); these module aliases are the defaults,
#: kept for readability and the tests. Override via MY20Q_* env vars (see config).
_DEFAULT_TUNING = ReasoningTuning()
MIN_YES_FOR_SYNTHESIS = _DEFAULT_TUNING.min_yes_for_synthesis
SOFT_RESET_NO_STREAK = _DEFAULT_TUNING.soft_reset_no_streak

#: Used only when the LLM is briefly unavailable AND the topic has no fallback bank
#: — a single neutral holding question so the round keeps going (it never ends on
#: a failure; only a "yes" to an utterance ends a round).
_GENERIC_FALLBACK_Q = "Is it something you need help with right now?"


@dataclass
class RoundEvent:
    """The cockpit-facing state produced by open()/answer()/undo()."""

    kind: EventKind
    text: str = ""
    rationale: str = ""
    preface: str = ""  # short spoken lead-in read just before a query
    query_index: int = 0
    engine: Engine = "reasoning"
    emergency_screen: dict | None = None
    # The live belief over candidate needs — [{need, weight}] sorted desc — that
    # drove this question. Empty in fallback/emergency/terminal events. Powers
    # the cockpit's honest reasoning tile.
    hypotheses: list[dict] = field(default_factory=list)


class Round:
    """One convergence attempt under a single topic.

    Drive it: ``await open()`` once, then alternate ``await answer(a)``
    with reading each `RoundEvent`. ``add_context`` injects caregiver
    steering; ``await undo()`` rewinds the last entry. The round is over
    once an event of kind ``synthesized``, ``abandoned``, or
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
        # Caregiver emotional-slider reading; steers question tone (the API
        # keeps it in sync mid-round). See docs/design/beta-retool.md §6.
        self.emotional_state: dict[str, float] = dict(emotional_state or {})
        # Behavior knobs (env-configurable). Defaults match the module aliases.
        self.tuning = tuning or ReasoningTuning()
        # Session-scoped memory of confirmed yeses — survives a context dump and
        # seeds reseeds. Defaults to an ephemeral in-memory store (tests / no
        # session); the Session passes a shared, file-backed one for a real patient.
        self._yes_memory = yes_memory if yes_memory is not None else YesMemory()
        self.round_id = round_id
        # Drives the (decaying) exploration coin-flip; injectable for tests.
        self._rng = rng or random.Random()
        self.engine: Engine = "reasoning" if llm is not None else "fallback"
        #: Why the round dropped from reasoning to fallback mid-round, if it
        #: did — surfaced for diagnostics (e.g. the model bench). Empty unless
        #: a ReasonerError forced the degrade.
        self.degrade_reason: str = ""
        #: Consecutive reasoning failures; reset on any successful reasoning turn.
        #: Drives transient-vs-permanent degrade (see _handle_reason_failure).
        self._consec_failures = 0
        self._reasoner = Reasoner(llm) if llm is not None else None
        # The round's *seed* candidate-need set (the belief prior), generated
        # once by the reasoner on the first advance. The active set also grows
        # with hypotheses the caregiver's context implies; both the active set
        # and the weights are recomputed from history (see _replay_belief).
        self._seed_hypotheses: list[Hypothesis] = []
        self._history: list[dict] = []
        self._pending: ReasonerAction | None = None
        self._pending_qid: str | None = None
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
        # Persist the reasoner's rationale alongside the query so saves,
        # recordings, and the session-review dashboard can show *why* each
        # question was asked — not just the question and the answer.
        entry: dict = {
            "kind": pending.kind,
            "text": pending.content,
            "answer": a.value,
            "rationale": pending.rationale,
        }
        if self._pending_qid is not None:
            entry["qid"] = self._pending_qid
        # Belief-replay metadata: a query carries the candidates it targeted (so
        # undo can recompute scores); a synthesis carries the hypothesis it was
        # built from (retained for rephrasing and the belief view — a rejected
        # synthesis no longer eliminates its need, it gets rephrased instead).
        if pending.yes_ids:
            entry["yes_ids"] = list(pending.yes_ids)
        # A query that explored a brand-new need carries its text, so belief
        # replay can spawn it as a candidate on a yes/kinda (escapes the seeds).
        if pending.new_need:
            entry["new_need"] = pending.new_need
        if pending.hyp_id:
            entry["hyp_id"] = pending.hyp_id
        self._history.append(entry)
        self._pending = None
        self._pending_qid = None
        # Remember every confirmed-YES query — the durable signal that survives a
        # context dump and seeds reseeds / new rounds within this session.
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
        — far more reliable than the generic seeds. In reasoning mode we ask the
        reasoner for any NEW candidate needs the note implies and add them to the
        belief at a boosted prior (see ``_replay_belief`` / ``add_with_boost``),
        so the next question discriminates over what the caregiver just told us.
        The added needs are recorded on the context entry, keeping the belief
        reconstructible for undo. Empty context is a no-op; fallback mode has no
        LLM, so its deterministic walk simply re-proposes.
        """
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        text = text.strip()
        if not text:
            if self._pending is not None:
                return self._pending_event()
            return await self._advance()

        added: list[dict] = []
        boost_ids: list[str] = []
        if (
            self.engine == "reasoning"
            and self._reasoner is not None
            and self._seed_hypotheses
        ):
            active, _ = self._replay_belief()
            new_needs, boost_ids = await self._reasoner.expand_hypotheses(
                context=text,
                existing=[(h.id, h.need) for h in active],
                history=self._history,
                **self._reasoner_ctx(),
            )
            base = len(active)
            added = [
                {"id": f"h{base + i + 1}", "need": need}
                for i, need in enumerate(new_needs)
            ]

        entry: dict = {"kind": "context", "text": text, "answer": None}
        if added:
            entry["added"] = added
        if boost_ids:
            entry["boost"] = boost_ids
        self._history.append(entry)
        self._pending = None
        self._pending_qid = None
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
            self._pending_qid = None

    async def undo(self) -> RoundEvent:
        """Rewind the most recent entry and re-propose forward.

        Everything downstream of the undone entry is discarded; in
        reasoning mode the next action is re-proposed against the
        truncated history, so it may differ from before.
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
        self._pending_qid = None
        return await self._advance()

    # -------------------------------------------------------- internal

    async def _advance(self) -> RoundEvent:
        # No reasoner at all (constructed without an LLM) → permanent fallback
        # questioning. A mid-round reasoning failure does NOT land here: the
        # reasoner is kept and retried every turn (see _handle_reason_failure).
        if self._reasoner is None:
            self.engine = "fallback"
            return self._fallback_advance()

        assert self._reasoner is not None
        T = self.tuning
        try:
            # Seed the candidate-need set once, on the first advance.
            if not self._seed_hypotheses:
                self._seed_hypotheses = await self._reasoner.seed_hypotheses(
                    seed_universal_wants=self.topic.seed_universal_wants,
                    **self._reasoner_ctx(),
                )

            # Decide the next move from the belief SEGMENT (history since the last
            # reseed): question, synthesize, rephrase, or dump-and-reseed.
            seg = self._segment()
            last = seg[-1] if seg else None
            synth_runs, tail_run = self._synth_runs(seg)

            if last is not None and last.get("kind") == "synthesis":
                # Mid synthesis attempt — the last utterance was rejected (a "yes"
                # would have ended the round). Rephrase up to the per-attempt limit,
                # then return to questioning, then (after enough failed attempts)
                # dump everything and reseed.
                if tail_run <= T.rephrase_limit:
                    move = "rephrase"
                elif synth_runs >= T.synth_attempts_before_reseed:
                    move = "reseed"
                else:
                    move = "question"
            else:
                # Questioning — synthesize once enough yeses have accrued. The first
                # attempt needs `min_yes`; every later attempt needs `new_yes` NEW
                # yeses (counted since the last utterance).
                new_yes = self._yes_since_last_synth(seg)
                # The first-ever synthesis of the round needs `min_yes`; every later
                # attempt — after a failed attempt OR a reseed — needs `new_yes`.
                ever_synthesized = any(
                    h.get("kind") == "synthesis" for h in self._history
                )
                need = (
                    T.new_yes_for_resynthesis
                    if ever_synthesized
                    else T.min_yes_for_synthesis
                )
                # Synthesize on the yes-count gate OR on READINESS — a clearly
                # dominant leader with at least new_yes confirmations. Readiness
                # restores early convergence for a belief that concentrates fast,
                # without forcing a half-baked utterance.
                ready = new_yes >= T.new_yes_for_resynthesis and self._belief_ready()
                move = "synthesize" if (new_yes >= need or ready) else "question"

            if move == "reseed":
                await self._reseed()  # appends a reseed marker (dumps the context)
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
            if move in ("synthesize", "rephrase"):
                action, active, scores = await self._propose_synthesis(seg, move)
            if action is None:  # questioning — or synthesis had no leader to use
                action, active, scores = await self._propose_question(seg)
        except ReasonerError as exc:
            return self._handle_reason_failure(str(exc))

        # Reasoning succeeded — clear any failure streak and resume reasoning
        # mode (it may have been "fallback" from a transient degrade last turn).
        self._consec_failures = 0
        self.engine = "reasoning"
        self._pending = action
        self._pending_qid = None
        return self._event_for(action, self._belief_view(active, scores))

    async def _propose_synthesis(
        self, seg: list[dict], move: str
    ) -> tuple[ReasonerAction | None, list[Hypothesis], dict[str, float]]:
        """Build an utterance from the leading need (or a rephrase of it).

        Returns ``(action, active, scores)``; ``action`` is None when there is no
        leading hypothesis to synthesize — the caller then keeps questioning rather
        than ending the round (only a confirmed utterance ends a round).
        """
        assert self._reasoner is not None
        active, scores = self._replay_belief()
        by_id = {h.id: h for h in active}
        top = hyp.leader(scores)
        leader_hyp = by_id.get(top[0]) if top else None
        if leader_hyp is None:
            return None, active, scores
        rejected: list[tuple[str, str]] | None = None
        if move == "rephrase":
            # The rejected utterances of THIS attempt (the trailing synthesis run)
            # so the model phrases it differently — kinda = close, no = wrong angle.
            rejected = []
            for h in reversed(seg):
                if h.get("kind") == "synthesis":
                    rejected.append((h.get("text", ""), h.get("answer", "")))
                else:
                    break
            rejected.reverse()
        action = await self._reasoner.synthesize(
            leading_need=leader_hyp.need,
            history=self._history,
            rejected=rejected,
            **self._reasoner_ctx(),
        )
        action.hyp_id = leader_hyp.id
        return action, active, scores

    async def _propose_question(
        self, seg: list[dict]
    ) -> tuple[ReasonerAction, list[Hypothesis], dict[str, float]]:
        """Ask the next drilling question, with soft-reset + exploratory cadence."""
        assert self._reasoner is not None
        T = self.tuning
        # Soft reset: a long run of "no" means the warm avenue is WRONG — dump the
        # "kinda" rewards, keep only the YES confirmations, force fresh exploration.
        soft_reset = self._consec_no_streak() > T.soft_reset_no_streak
        active, scores = self._replay_belief(drop_kinda=soft_reset)
        # Anchor on warm content (yes/kinda); a soft reset narrows "warm" to
        # YES-only so the dumped kinda needs release the anchor and the field
        # re-opens. Warm is taken over the current segment (after any reseed).
        affirmed = {Answer.YES.value} if soft_reset else _AFFIRMED
        warm = {
            str(x)
            for e in seg
            if e.get("kind") == "query" and e.get("answer") in affirmed
            for x in (e.get("yes_ids") or [])
        }
        focused, anchored = hyp.anchor_focus(hyp.ranked(active, scores), warm)
        candidates = [(h.id, h.need, s) for h, s in focused]
        # Exploration DECAYS as yeses accrue toward synthesis — explore with
        # probability explore_decay**(yeses+1): high early, low as we home in. The
        # yes-count resets after each synthesis attempt and after a reseed (it is
        # counted within the segment), so a reset re-opens exploration. A soft reset
        # forces it.
        yeses = self._yes_since_last_synth(seg)
        exploratory = soft_reset or self._rng.random() < self._explore_probability(yeses)
        action = await self._reasoner.ask(
            candidates=candidates,
            anchored=anchored,
            exploratory=exploratory,
            reset=soft_reset,
            history=self._history,
            **self._reasoner_ctx(),
        )
        return action, active, scores

    def _explore_probability(self, yeses: int) -> float:
        """Probability the next question is exploratory: ``explore_decay**(yeses+1)``.

        High when few yeses have accrued toward synthesis, decaying as the round
        homes in. ``yeses`` is counted since the last synthesis in the current
        segment, so it resets after each synthesis attempt and after a reseed —
        re-opening exploration each time (see config.ReasoningTuning.explore_decay).
        """
        return self.tuning.explore_decay ** (yeses + 1)

    # ---- belief-segment helpers (the synthesis state machine reads these) ----

    def _segment(self) -> list[dict]:
        """History since the last reseed — the live belief segment.

        A reseed DUMPS context, so the synthesis state machine (yes counts, attempt
        runs) reasons only over entries after the most recent reseed marker.
        """
        start = 0
        for i, h in enumerate(self._history):
            if h.get("kind") == "reseed":
                start = i + 1
        return self._history[start:]

    @staticmethod
    def _synth_runs(seg: list[dict]) -> tuple[int, int]:
        """``(number of synthesis attempts, length of the trailing one)``.

        An "attempt" is a maximal run of consecutive synthesis entries (the initial
        utterance plus its rephrases). ``tail_run`` is 0 unless the segment ends in
        a synthesis run.
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

    def _belief_ready(self) -> bool:
        """Whether the belief has concentrated on a clear leader.

        True when the top need leads the runner-up by at least ``readiness_margin``
        points (and is positive). Lets a confident, concentrated belief synthesize
        before the full yes-count — restoring early convergence — without forcing it.
        """
        _, scores = self._replay_belief()
        ranked = sorted(scores.values(), reverse=True)
        if not ranked or ranked[0] <= 0:
            return False
        second = ranked[1] if len(ranked) > 1 else float("-inf")
        return (ranked[0] - second) >= self.tuning.readiness_margin

    @staticmethod
    def _yes_since_last_synth(seg: list[dict]) -> int:
        """Count query "yes" answers after the last synthesis entry in the segment."""
        n = 0
        for h in reversed(seg):
            if h.get("kind") == "synthesis":
                break
            if h.get("kind") == "query" and h.get("answer") == Answer.YES.value:
                n += 1
        return n

    def _record_yes(self, pending: ReasonerAction) -> None:
        """Add a confirmed-yes query to the session's yes-memory."""
        active, _ = self._replay_belief()
        by_id = {h.id: h.need for h in active}
        needs = [by_id[i] for i in pending.yes_ids if i in by_id]
        if pending.new_need and not needs:
            needs = [pending.new_need]
        self._yes_memory.add(
            question=pending.content,
            needs=needs,
            topic_id=self.topic.id,
            round_id=self.round_id,
        )

    async def _reseed(self) -> None:
        """Dump the round's accumulated belief and reseed with fresh candidates.

        Half the new seeds come from the session's memorized YES needs (so hard-won
        confirmations are not lost), half from a fresh, profile-free (deliberately
        random) on-topic seed call. Recorded as a ``reseed`` marker carrying the new
        seeds, so the belief stays reconstructible and undo still works. A no-op if
        no seeds can be assembled (keeps the prior belief rather than emptying it).
        """
        assert self._reasoner is not None
        memorized = self._yes_memory.needs()
        random_needs: list[str] = []
        try:
            ctx = self._reasoner_ctx()
            ctx["profile_context"] = ""  # drop the profile → deliberately broad
            fresh = await self._reasoner.seed_hypotheses(
                seed_universal_wants=self.topic.seed_universal_wants, **ctx
            )
            random_needs = [h.need for h in fresh]
        except ReasonerError:
            random_needs = []

        cap = MAX_HYPOTHESES
        half = max(1, cap // 2)
        chosen: list[str] = []
        seen: set[str] = set()

        def _take(src: list[str], limit: int) -> None:
            for need in src:
                if len(chosen) >= limit:
                    break
                key = need.casefold()
                if need and key not in seen:
                    seen.add(key)
                    chosen.append(need)

        _take(memorized, half)  # up to half from the memorized yeses
        _take(random_needs, cap)  # fill the rest with fresh random on-topic needs
        _take(memorized, cap)  # backfill from memory if the fresh call came up short
        if not chosen:
            return  # nothing to seed with — keep the existing belief, keep going
        seeds = [{"id": f"s{i + 1}", "need": need} for i, need in enumerate(chosen)]
        self._history.append({"kind": "reseed", "seeds": seeds})
        log.info(
            "round reseeded: dumped context → %d seeds (%d from memory)",
            len(seeds),
            sum(1 for s in seeds if s["need"] in set(memorized)),
        )

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

    def _consec_no_streak(self) -> int:
        """Consecutive 'no'-answered queries at the tail of the history.

        Counts back from the latest entry over queries answered "no"; a "yes" or
        "kinda" (real positive signal) ends the run, while "not sure" (no
        information) and caregiver-context entries are transparent. A reseed or a
        synthesis attempt is a hard boundary (the run does not span them). Drives
        the soft reset (see soft_reset_no_streak): a long run means the warm anchor
        is wrong and the round should re-open.
        """
        streak = 0
        for h in reversed(self._history):
            kind = h.get("kind")
            if kind in ("reseed", "synthesis"):
                break  # a reseed or synthesis attempt ends the run of "no"s
            if kind != "query":
                continue  # caregiver context etc. is transparent
            answer = h.get("answer")
            if answer == Answer.NO.value:
                streak += 1
            elif answer == Answer.NOT_SURE.value:
                continue  # no information — neither extends nor breaks the run
            else:
                break  # yes / kinda — a real positive signal ends the run
        return streak

    def _replay_belief(
        self, *, drop_kinda: bool = False
    ) -> tuple[list[Hypothesis], dict[str, float]]:
        """Rebuild the active hypothesis set and its additive scores from history.

        Walks events in order so undo is just pop-and-recompute:
        - start from zeroed seed scores;
        - a [caregiver context] entry inserts the needs it implies and adds
          high-trust points to them;
        - a query adds points to the needs it targeted (yes/kinda positive, no
          subtracts from those needs only — never promotes the others);
        - a [reseed] entry DUMPS everything so far and restarts the belief from the
          fresh seeds it carries;
        - a synthesis entry does NOT change the belief — a rejected utterance is
          rephrased, not eliminated, so its need survives to be tried again.

        ``drop_kinda`` (a soft reset) withholds the "kinda" rewards: the warm
        candidates stay in play but earn no points, so a lukewarm-but-wrong guess
        no longer holds the lead.
        """
        active = list(self._seed_hypotheses)
        scores = hyp.seed_scores(active)
        for h in self._history:
            kind = h["kind"]
            if kind == "reseed":
                # Dump all accumulated context; restart from the carried seeds.
                active = [Hypothesis(s["id"], s["need"]) for s in (h.get("seeds") or [])]
                scores = hyp.seed_scores(active)
            elif kind == "context":
                added = h.get("added") or []
                boost = h.get("boost") or []
                if added or boost:
                    active = active + [Hypothesis(a["id"], a["need"]) for a in added]
                    scores = hyp.apply_context(
                        scores, [a["id"] for a in added], boost
                    )
            elif kind == "query":
                answer = h.get("answer")
                if answer:
                    yes = set(h.get("yes_ids", []))
                    # An exploratory question that proposed a NEW need becomes a
                    # real candidate when the person says yes/kinda to it.
                    new_need = h.get("new_need")
                    if new_need and answer in _AFFIRMED:
                        nid = next(iter(yes), None)
                        if nid and all(hh.id != nid for hh in active):
                            active = active + [Hypothesis(nid, new_need)]
                            scores[nid] = 0.0
                    # Soft reset dumps the warm "kinda" rewards: the candidate
                    # stays in play (spawned above, at its current score) but the
                    # +0.5 is withheld, so a lukewarm-but-wrong guess no longer
                    # holds the lead or the anchor.
                    if drop_kinda and answer == Answer.KINDA.value:
                        continue
                    scores = hyp.update_score(scores, yes, answer)
        return active, scores

    def _belief_view(
        self, active: list[Hypothesis], scores: dict[str, float]
    ) -> list[dict]:
        # The honest tile shows raw accumulated points (can be 0 / negative), not
        # a normalized probability — that is the actual belief now.
        return [
            {"need": h.need, "weight": round(s, 3)}
            for h, s in hyp.ranked(active, scores)
        ]

    def _pending_event(self) -> RoundEvent:
        """Re-emit the current pending query with its current belief view."""
        assert self._pending is not None
        if self.engine == "reasoning" and self._seed_hypotheses:
            active, weights = self._replay_belief()
            return self._event_for(self._pending, self._belief_view(active, weights))
        return self._event_for(self._pending)

    def _handle_reason_failure(self, reason: str) -> RoundEvent:
        """A reasoning call failed THIS turn — degrade to a fallback QUESTION.

        Reasoning is never permanently abandoned: the reasoner is kept and retried
        every turn, because only it can produce the utterance a round needs to end.
        A failure never ends a round and never forces a synthesis — it just asks a
        deterministic holding question this turn while the model recovers.
        """
        self._consec_failures += 1
        self.degrade_reason = reason
        level = (
            log.error
            if self._consec_failures >= MAX_CONSEC_REASON_FAILURES
            else log.warning
        )
        level(
            "reasoner failed (%s) — fallback question this turn, will retry (×%d)",
            reason,
            self._consec_failures,
        )
        self.engine = "fallback"
        return self._fallback_advance()

    def _fallback_advance(self) -> RoundEvent:
        """Degraded QUESTIONING only — a transient stand-in for a failed turn.

        It ONLY asks questions: it never synthesizes and never ends the round (only
        a "yes" to an utterance ends a round, and only the reasoner produces
        utterances). It walks the topic's fallback bank, LOOPING when the bank is
        exhausted so questioning continues until reasoning recovers. Position is
        derived from history, so undo just works.
        """
        # Operator safety ceiling only (off by default) — never a model decision.
        if self.max_queries > 0 and self.query_count >= self.max_queries:
            self._outcome = "abandoned"
            return RoundEvent(
                kind="abandoned", query_index=self.query_count, engine=self.engine
            )

        bank = self.topic.fallback_questions
        if not bank:  # topic has no bank — a single neutral holding question
            action = ReasonerAction(
                kind="query",
                content=_GENERIC_FALLBACK_Q,
                rationale="Fallback: reasoning is briefly unavailable.",
            )
            self._pending = action
            self._pending_qid = None
            return self._event_for(action)

        answers: dict[str, str] = {}
        for h in self._history:
            if h["kind"] == "query" and h.get("qid"):
                answers[h["qid"]] = h["answer"]
        consumed = {q for q, a in answers.items() if a != Answer.NOT_SURE.value}
        deferred = {q for q, a in answers.items() if a == Answer.NOT_SURE.value}

        nxt: FallbackQuestion | None = next(
            (q for q in bank if q.id not in consumed and q.id not in deferred), None
        )
        if nxt is None:  # then retry the deferred (not_sure) questions, in order
            nxt = next((q for q in bank if q.id in deferred), None)
        if nxt is None:  # bank exhausted — a neutral holding question, NOT a repeat
            action = ReasonerAction(
                kind="query",
                content=_GENERIC_FALLBACK_Q,
                rationale="Fallback: reasoning is briefly unavailable.",
            )
            self._pending = action
            self._pending_qid = None
            return self._event_for(action)

        action = ReasonerAction(
            kind="query",
            content=nxt.question,
            rationale=f"Fallback mode: walking the '{self.topic.label}' question bank.",
        )
        self._pending = action
        self._pending_qid = nxt.id
        return self._event_for(action)

    def _event_for(
        self, action: ReasonerAction, belief: list[dict] | None = None
    ) -> RoundEvent:
        idx = self.query_count + (1 if action.kind == "query" else 0)
        return RoundEvent(
            kind=action.kind,
            text=action.content,
            rationale=action.rationale,
            preface=action.preface,
            query_index=idx,
            engine=self.engine,
            hypotheses=belief or [],
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
        # One yes-memory per session, shared by every round it spawns (so a reseed
        # or a later round can draw on every confirmed-yes so far). File-backed for
        # a real patient (the privacy invariant); in-memory only otherwise.
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
