"""Dialogue state machine.

Deterministic depth-first traversal of the taxonomy tree. At each internal
node we ask about children one at a time; "yes" descends, "no" prunes that
subtree, "not sure" defers the child to the back of the queue. When one
candidate remains we descend into it. Leaves confirm and end the session.

The LLM (optional) is only used to rephrase questions and summarize the
final selection for the caregiver. The tree traversal is fully functional
without it, so the app always keeps working if Ollama is down.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from my20q.agent import prompts
from my20q.agent.safety import is_emergency_path, sanitize_llm_text
from my20q.llm.base import LLMBackend, LLMUnavailable
from my20q.taxonomy.node import Node

log = logging.getLogger(__name__)


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    NOT_SURE = "not_sure"


@dataclass
class TurnResult:
    kind: Literal["question", "confirm_leaf", "emergency", "answer", "dead_end"]
    node: Node
    question: str = ""
    path: list[Node] = field(default_factory=list)
    summary: str = ""


@dataclass
class _Frame:
    node: Node
    queue: deque[Node]
    deferred: list[Node] = field(default_factory=list)


class DialogueSession:
    def __init__(
        self,
        root: Node,
        *,
        llm: LLMBackend | None = None,
        max_turns: int = 20,
    ) -> None:
        self.root = root
        self.llm = llm
        self.max_turns = max_turns
        self.turn = 0
        self._path: list[Node] = [root]
        self._stack: list[_Frame] = [_Frame(root, deque(root.children))]
        self._pending_leaf: Node | None = None
        self._last_probe: Node | None = None

    @property
    def path(self) -> list[Node]:
        return list(self._path)

    def start(self) -> TurnResult:
        """Return the first prompt. For root, that is category selection."""
        return self._next_prompt()

    def select_category(self, category_id: str) -> TurnResult:
        """Explicit top-level category pick (skips the yes/no loop at root)."""
        if self._stack[-1].node is not self.root:
            raise RuntimeError("select_category can only be called at the root")
        chosen = self.root.find(category_id)
        if chosen is None or chosen.id == self.root.id or chosen not in self.root.children:
            raise ValueError(f"Unknown top-level category: {category_id!r}")
        # Once a category is chosen, don't let backtracking re-offer siblings —
        # restart the session instead if the subtree dead-ends.
        self._stack[0].queue.clear()
        self._descend(chosen)
        return self._next_prompt()

    def answer(self, answer: Answer) -> TurnResult:
        """Consume a yes/no/not-sure for the last question asked."""
        self.turn += 1
        if self._pending_leaf is not None:
            return self._resolve_leaf_confirmation(answer)
        if self._last_probe is None:
            raise RuntimeError("answer() called before a question was issued")
        probe = self._last_probe
        self._last_probe = None
        frame = self._stack[-1]
        if answer is Answer.YES:
            self._descend(probe)
        elif answer is Answer.NO:
            pass  # probe already popped from queue; stay at this frame.
        else:  # NOT_SURE
            frame.deferred.append(probe)
        return self._next_prompt()

    # ------------------------------------------------------------------ internals

    def _descend(self, node: Node) -> None:
        self._path.append(node)
        self._stack.append(_Frame(node, deque(node.children)))

    def _ascend(self) -> None:
        if len(self._stack) <= 1:
            return
        self._stack.pop()
        self._path.pop()

    def _next_prompt(self) -> TurnResult:
        if self.turn >= self.max_turns:
            return TurnResult(kind="dead_end", node=self._stack[-1].node, path=self.path)

        while self._stack:
            frame = self._stack[-1]
            node = frame.node

            if is_emergency_path(self._path):
                return TurnResult(kind="emergency", node=node, path=self.path)

            if node.is_leaf:
                self._pending_leaf = node
                return TurnResult(
                    kind="confirm_leaf",
                    node=node,
                    question=node.question,
                    path=self.path,
                )

            if frame.queue:
                probe = frame.queue.popleft()
                self._last_probe = probe
                question = self._phrase_question(probe)
                return TurnResult(kind="question", node=probe, question=question, path=self.path)

            if frame.deferred:
                # Retry deferred children in the order they were deferred.
                frame.queue.extend(frame.deferred)
                frame.deferred.clear()
                continue

            # Exhausted this frame: back up to the parent.
            if len(self._stack) == 1:
                return TurnResult(kind="dead_end", node=node, path=self.path)
            self._ascend()

        return TurnResult(kind="dead_end", node=self.root, path=self.path)

    def _resolve_leaf_confirmation(self, answer: Answer) -> TurnResult:
        leaf = self._pending_leaf
        assert leaf is not None
        self._pending_leaf = None
        if answer is Answer.YES:
            summary = self._summarize(self._path)
            return TurnResult(kind="answer", node=leaf, path=self.path, summary=summary)
        # "no" or "not sure" on a leaf backtracks and keeps searching.
        self._ascend()
        return self._next_prompt()

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

    def _summarize(self, path: list[Node]) -> str:
        fallback = f"They are communicating: {path[-1].label}."
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
    """Bridge async LLM calls into the sync state machine.

    The CLI is synchronous; the FastAPI layer (Phase 2) will call the
    async LLM methods directly and skip this helper.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside an event loop (e.g. tests via pytest-asyncio): schedule on it.
    return loop.run_until_complete(coro)  # pragma: no cover - not used in sync CLI
