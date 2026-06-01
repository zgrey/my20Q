"""Round state machine — the my20Q dialogue engine.

A `Session` spans one tool process and spawns `Round`s. A `Round` is one
convergence attempt under a single topic: it issues queries and ends when
the caregiver confirms a synthesized utterance (success), the query
budget is exhausted, or the round is abandoned. Terminology and the round
lifecycle: docs/design/beta-retool.md §2, §7.

Reasoning mode (LLM available) — the `Reasoner` proposes each query and
the synthesis. Fallback mode (LLM unreachable) — a deterministic,
stateless walk of the topic's fallback question bank.

The engine is async: the FastAPI layer awaits it directly; a CLI wraps
it in `asyncio.run`. The round history is a flat list, so `undo()` is
just a truncation — no separate rewind bookkeeping.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from my20q.agent.reasoner import Reasoner, ReasonerAction, ReasonerError
from my20q.agent.safety import EMERGENCY_SCREEN
from my20q.config import Config, Mode
from my20q.llm.base import LLMBackend
from my20q.profiles import PatientProfile
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
        max_queries: int = 20,
        mode: Mode = "training",
        seed_context: str = "",
        profile_context: str = "",
        emotional_state: dict[str, float] | None = None,
    ) -> None:
        self.topic = topic
        self.llm = llm
        self.max_queries = max(1, max_queries)
        self.mode = mode
        self.seed_context = seed_context.strip()
        self.profile_context = profile_context.strip()
        # Caregiver emotional-slider reading; steers question tone (the API
        # keeps it in sync mid-round). See docs/design/beta-retool.md §6.
        self.emotional_state: dict[str, float] = dict(emotional_state or {})
        self.engine: Engine = "reasoning" if llm is not None else "fallback"
        self._reasoner = Reasoner(llm) if llm is not None else None
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
        self._history.append(entry)
        self._pending = None
        self._pending_qid = None

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
        """Inject caregiver context mid-round and refresh the pending query.

        The current (unanswered) query lives in ``_pending``, not in
        history — so we append the context and re-propose against the
        updated history, returning a fresh query that actually accounts
        for what the caregiver just said. Empty context is a no-op that
        leaves the current query in place. In fallback mode there is no
        LLM to steer, so the deterministic walk re-proposes the same
        question.
        """
        if self._outcome is not None:
            raise RuntimeError("round is already terminal")
        text = text.strip()
        if not text:
            if self._pending is not None:
                return self._event_for(self._pending)
            return await self._advance()
        self._history.append({"kind": "context", "text": text, "answer": None})
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
        if self.engine == "fallback":
            return self._fallback_advance()

        final = self.query_count >= self.max_queries
        last = self._history[-1] if self._history else None
        if (
            final
            and last is not None
            and last["kind"] == "synthesis"
            and last["answer"] != Answer.YES.value
        ):
            # The forced final synthesis was rejected — nothing left to try.
            self._outcome = "abandoned"
            return RoundEvent(
                kind="abandoned", query_index=self.query_count, engine=self.engine
            )

        assert self._reasoner is not None
        try:
            action = await self._reasoner.next_action(
                topic_label=self.topic.label,
                history=self._history,
                query_index=self.query_count + 1,
                max_queries=self.max_queries,
                final=final,
                seed_context=self.seed_context,
                profile_context=self.profile_context,
                topic_hint=self.topic.reasoning_hint or "",
                emotional_state=self.emotional_state,
                on_phase=self.on_phase,
            )
        except ReasonerError as exc:
            log.warning("reasoner failed (%s) — degrading to fallback mode", exc)
            return self._degrade_to_fallback()

        self._pending = action
        self._pending_qid = None
        return self._event_for(action)

    def _degrade_to_fallback(self) -> RoundEvent:
        """Switch to deterministic fallback mid-round (LLM became unusable)."""
        self.engine = "fallback"
        self._reasoner = None
        self._pending = None
        self._pending_qid = None
        return self._fallback_advance()

    def _fallback_advance(self) -> RoundEvent:
        """Deterministic, stateless walk of the topic's fallback bank.

        Position is derived from history each call, so undo just works.
        """
        bank = self.topic.fallback_questions
        last = self._history[-1] if self._history else None

        # A just-affirmed fallback query → synthesize from its label.
        if (
            last is not None
            and last["kind"] == "query"
            and last.get("qid")
            and last["answer"] in _AFFIRMED
        ):
            fq = self._find_fq(last["qid"])
            if fq is not None:
                action = ReasonerAction(
                    kind="synthesis",
                    content=fq.label,
                    rationale="Fallback mode: confirming the matched need.",
                )
                self._pending = action
                self._pending_qid = None
                return self._event_for(action)

        # Otherwise advance to the next un-consumed bank question.
        answers: dict[str, str] = {}
        for h in self._history:
            if h["kind"] == "query" and h.get("qid"):
                answers[h["qid"]] = h["answer"]
        consumed = {q for q, a in answers.items() if a != Answer.NOT_SURE.value}
        deferred = {q for q, a in answers.items() if a == Answer.NOT_SURE.value}

        nxt: FallbackQuestion | None = next(
            (q for q in bank if q.id not in consumed and q.id not in deferred), None
        )
        if nxt is None:  # retry the deferred (not_sure) questions, in file order
            nxt = next((q for q in bank if q.id in deferred), None)

        if nxt is None or self.query_count >= self.max_queries:
            self._outcome = "abandoned"
            return RoundEvent(
                kind="abandoned", query_index=self.query_count, engine=self.engine
            )

        action = ReasonerAction(
            kind="query",
            content=nxt.question,
            rationale=f"Fallback mode: walking the '{self.topic.label}' question bank.",
        )
        self._pending = action
        self._pending_qid = nxt.id
        return self._event_for(action)

    def _find_fq(self, qid: str) -> FallbackQuestion | None:
        return next((q for q in self.topic.fallback_questions if q.id == qid), None)

    def _event_for(self, action: ReasonerAction) -> RoundEvent:
        idx = self.query_count + (1 if action.kind == "query" else 0)
        return RoundEvent(
            kind=action.kind,
            text=action.content,
            rationale=action.rationale,
            preface=action.preface,
            query_index=idx,
            engine=self.engine,
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

    def start_round(self, topic_id: str, *, seed_context: str = "") -> Round:
        topic = find_topic(self.topics, topic_id)
        if topic is None:
            raise ValueError(f"Unknown topic: {topic_id!r}")
        round_ = Round(
            topic,
            llm=self.llm,
            max_queries=self.config.max_queries if self.config else 20,
            mode=self.config.mode if self.config else "training",
            seed_context=seed_context,
            profile_context=(self.profile.context or "") if self.profile else "",
            emotional_state=self.emotional_state,
        )
        self.rounds.append(round_)
        return round_

    @property
    def topic_sequence(self) -> list[str]:
        """Ordered topics of the rounds so far — input to loop detection."""
        return [r.topic.id for r in self.rounds]
