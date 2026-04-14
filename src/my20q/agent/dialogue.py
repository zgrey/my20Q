"""Dialogue state machine — LLM-driven in reasoning mode, taxonomy tree
walk as the fallback for when Ollama is unavailable.

Two modes dispatch through the same `DialogueSession.answer()` API so
the CLI and (later) the FastAPI layer don't need to care which is
active.

Reasoning mode
--------------
After the user picks a starting category, the `Reasoner` drives the
game: every turn asks the LLM for a yes/no question or a specific guess,
then updates history with the user's answer (yes/no/kinda/not_sure).
"yes" on a guess ends the session and triggers a caregiver summary.
The 20-turn budget is the game limit — at the final turn the reasoner
is forced to guess.

Fallback mode
-------------
If the LLM is not reachable, the session reverts to a deterministic
breadth-first walk of the taxonomy subtree under the chosen category.
"yes" descends, "no" prunes, "not_sure" defers, "kinda" is treated as
yes (go deeper in this direction). Emergency nodes short-circuit in
both modes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from my20q.agent import prompts
from my20q.agent.reasoner import Reasoner, ReasonerError
from my20q.agent.safety import is_emergency_path, sanitize_llm_text
from my20q.llm.base import LLMBackend, LLMUnavailable
from my20q.taxonomy.node import Node

log = logging.getLogger(__name__)


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    KINDA = "kinda"
    NOT_SURE = "not_sure"


TurnKind = Literal["question", "guess", "emergency", "answer", "dead_end"]


@dataclass
class TurnResult:
    kind: TurnKind
    content: str = ""
    node: Node | None = None
    rationale: str = ""
    path: list[Node] = field(default_factory=list)
    summary: str = ""
    turn: int = 0


@dataclass
class _Frame:
    node: Node
    queue: deque[Node]
    deferred: list[Node] = field(default_factory=list)


class DialogueSession:
    """Single 20Q session.

    Call `select_category(category_id)` to start, then alternate
    `answer(Answer.X)` with reading each `TurnResult`. A session is
    terminal when a TurnResult of kind `answer`, `emergency`, or
    `dead_end` is returned.
    """

    def __init__(
        self,
        root: Node,
        *,
        llm: LLMBackend | None = None,
        max_turns: int = 20,
        seed_context: str = "",
    ) -> None:
        self.root = root
        self.llm = llm
        self.max_turns = max_turns
        self.seed_context = seed_context.strip()
        self.turn = 0
        self._category: Node | None = None
        self._mode: Literal["reasoning", "fallback"] = (
            "reasoning" if llm is not None else "fallback"
        )
        self._reasoner: Reasoner | None = Reasoner(llm) if llm is not None else None

        # reasoning-mode state
        self._history: list[dict] = []
        self._last_kind: Literal["question", "guess"] | None = None
        self._last_content: str = ""
        self._last_rationale: str = ""

        # fallback-mode state
        self._stack: list[_Frame] = []
        self._pending_leaf: Node | None = None
        self._last_probe: Node | None = None

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    @property
    def path(self) -> list[Node]:
        return [self.root] if self._category is None else [self.root, self._category]

    def select_category(self, category_id: str) -> TurnResult:
        if self._category is not None:
            raise RuntimeError("select_category can only be called once")
        chosen = self.root.find(category_id)
        if chosen is None or chosen not in self.root.children:
            raise ValueError(f"Unknown top-level category: {category_id!r}")
        self._category = chosen
        if chosen.emergency:
            return TurnResult(kind="emergency", node=chosen, path=[self.root, chosen])
        if self._mode == "reasoning":
            return self._next_reasoning()
        if self.seed_context:
            # No LLM to refine freeform text — echo it as the answer.
            return TurnResult(
                kind="answer",
                content=self.seed_context,
                node=chosen,
                path=[self.root, chosen],
                summary=f"They are communicating: {self.seed_context}",
            )
        self._stack = [_Frame(chosen, deque(chosen.children))]
        return self._next_fallback()

    def answer(self, answer: Answer) -> TurnResult:
        if self._category is None:
            raise RuntimeError("answer() called before select_category")
        self.turn += 1
        if self._mode == "reasoning":
            return self._resolve_reasoning(answer)
        return self._resolve_fallback(answer)

    # ------------------------------------------------------------------ reasoning

    def _next_reasoning(self) -> TurnResult:
        assert self._category is not None and self._reasoner is not None
        final = self.turn + 1 >= self.max_turns
        try:
            action = _run_sync(
                self._reasoner.next_action(
                    category_label=self._category.label,
                    history=self._history,
                    turn=self.turn + 1,
                    max_turns=self.max_turns,
                    final=final,
                    seed_context=self.seed_context,
                )
            )
        except ReasonerError as exc:
            log.warning("reasoner failed (%s) — degrading to fallback mode", exc)
            return self._degrade_to_fallback()

        self._last_kind = action.kind
        self._last_content = action.content
        self._last_rationale = action.rationale
        return TurnResult(
            kind=action.kind,
            content=action.content,
            rationale=action.rationale,
            path=self.path,
            turn=self.turn + 1,
        )

    def _resolve_reasoning(self, answer: Answer) -> TurnResult:
        assert self._category is not None
        if self._last_kind is None:
            raise RuntimeError("reasoning resolve before any action was issued")
        self._history.append(
            {
                "action": self._last_kind,
                "content": self._last_content,
                "answer": answer.value,
            }
        )
        if self._last_kind == "guess" and answer is Answer.YES:
            content = self._last_content
            summary = self._summarize_game(content)
            return TurnResult(
                kind="answer",
                content=content,
                path=self.path,
                summary=summary,
                turn=self.turn,
            )
        # Any other combination continues the game.
        self._last_kind = None
        if self.turn >= self.max_turns:
            return TurnResult(kind="dead_end", path=self.path, turn=self.turn)
        return self._next_reasoning()

    def _summarize_game(self, final_content: str) -> str:
        assert self._category is not None
        fallback = f"They are communicating: {final_content}."
        if self.llm is None:
            return fallback
        try:
            raw = _run_sync(
                self.llm.chat(
                    prompts.summarize_game_messages(
                        self._category.label,
                        final_content,
                        self._history,
                        seed_context=self.seed_context,
                    ),
                    max_tokens=80,
                )
            )
        except LLMUnavailable as exc:
            log.info("LLM unavailable for game summary: %s", exc)
            return fallback
        cleaned = sanitize_llm_text(raw)
        return cleaned or fallback

    def _degrade_to_fallback(self) -> TurnResult:
        """Switch from reasoning to tree-walk mode mid-session."""
        assert self._category is not None
        self._mode = "fallback"
        self._reasoner = None
        self._stack = [_Frame(self._category, deque(self._category.children))]
        return self._next_fallback()

    # ------------------------------------------------------------------ fallback

    def _next_fallback(self) -> TurnResult:
        if self.turn >= self.max_turns:
            node = self._stack[-1].node if self._stack else self.root
            return TurnResult(kind="dead_end", node=node, path=self._fallback_path())

        while self._stack:
            frame = self._stack[-1]
            node = frame.node
            if is_emergency_path([self.root, *(f.node for f in self._stack)]):
                return TurnResult(kind="emergency", node=node, path=self._fallback_path())

            if node.is_leaf:
                self._pending_leaf = node
                question = self._phrase_question(node)
                return TurnResult(
                    kind="guess",
                    content=question,
                    node=node,
                    path=self._fallback_path(),
                    turn=self.turn,
                )

            if frame.queue:
                probe = frame.queue.popleft()
                self._last_probe = probe
                question = self._phrase_question(probe)
                return TurnResult(
                    kind="question",
                    content=question,
                    node=probe,
                    path=self._fallback_path(),
                    turn=self.turn,
                )

            if frame.deferred:
                frame.queue.extend(frame.deferred)
                frame.deferred.clear()
                continue

            if len(self._stack) == 1:
                return TurnResult(kind="dead_end", node=node, path=self._fallback_path())
            self._stack.pop()

        return TurnResult(kind="dead_end", node=self.root, path=self._fallback_path())

    def _resolve_fallback(self, answer: Answer) -> TurnResult:
        # Map KINDA to YES in tree-walk mode: "warmer" == "go deeper here".
        effective = Answer.YES if answer is Answer.KINDA else answer
        if self._pending_leaf is not None:
            leaf = self._pending_leaf
            self._pending_leaf = None
            if effective is Answer.YES:
                summary = self._summarize_fallback(leaf)
                return TurnResult(
                    kind="answer",
                    content=leaf.label,
                    node=leaf,
                    path=self._fallback_path(),
                    summary=summary,
                    turn=self.turn,
                )
            if self._stack:
                self._stack.pop()
            return self._next_fallback()

        if self._last_probe is None:
            raise RuntimeError("fallback resolve before any probe was issued")
        probe = self._last_probe
        self._last_probe = None
        frame = self._stack[-1]
        if effective is Answer.YES:
            self._stack.append(_Frame(probe, deque(probe.children)))
        elif effective is Answer.NOT_SURE:
            frame.deferred.append(probe)
        # NO: already popped; stay at this frame.
        return self._next_fallback()

    def _fallback_path(self) -> list[Node]:
        return [self.root, *(f.node for f in self._stack)]

    def _phrase_question(self, probe: Node) -> str:
        if self.llm is None:
            return probe.question
        try:
            raw = _run_sync(
                self.llm.chat(prompts.rephrase_question_messages(probe.question), max_tokens=60)
            )
        except LLMUnavailable as exc:
            log.info("LLM unavailable, using static question: %s", exc)
            return probe.question
        cleaned = sanitize_llm_text(raw)
        return cleaned or probe.question

    def _summarize_fallback(self, leaf: Node) -> str:
        path = self._fallback_path() + [leaf] if leaf not in self._stack else self._fallback_path()
        fallback = f"They are communicating: {leaf.label}."
        if self.llm is None:
            return fallback
        try:
            raw = _run_sync(
                self.llm.chat(prompts.summarize_path_messages(path), max_tokens=80)
            )
        except LLMUnavailable as exc:
            log.info("LLM unavailable for summary: %s", exc)
            return fallback
        cleaned = sanitize_llm_text(raw)
        return cleaned or fallback


def _run_sync(coro):  # type: ignore[no-untyped-def]
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "DialogueSession is synchronous and cannot run from within an active "
        "event loop. Use the async API directly from FastAPI/tests."
    )
