"""Deterministic mock backend for tests and offline dev."""

from __future__ import annotations

from collections.abc import Callable

from my20q.llm.base import LLMMessage


class MockBackend:
    def __init__(
        self,
        responder: Callable[[list[LLMMessage]], str] | None = None,
        *,
        thinking: bool = False,
    ) -> None:
        self.responder = responder or (lambda _msgs: "Okay.")
        self.calls: list[list[LLMMessage]] = []
        # When True the reasoner treats this backend as a thinking model and
        # takes the two-phase deliberate→format ask path (mirrors a gemma4).
        self.thinking = thinking

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
        think: bool | None = None,
    ) -> str:
        self.calls.append(messages)
        return self.responder(messages)

    async def is_thinking_model(self) -> bool:
        return self.thinking

    async def health(self) -> bool:
        return True
