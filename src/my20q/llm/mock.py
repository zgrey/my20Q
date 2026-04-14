"""Deterministic mock backend for tests and offline dev."""

from __future__ import annotations

from collections.abc import Callable

from my20q.llm.base import LLMMessage


class MockBackend:
    def __init__(self, responder: Callable[[list[LLMMessage]], str] | None = None) -> None:
        self.responder = responder or (lambda _msgs: "Okay.")
        self.calls: list[list[LLMMessage]] = []

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
    ) -> str:
        self.calls.append(messages)
        return self.responder(messages)

    async def health(self) -> bool:
        return True
