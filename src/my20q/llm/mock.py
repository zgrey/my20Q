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
        #: The ``think`` value of each chat() call, so tests can assert that
        #: structured-JSON calls force thinking off (see the gemma4 starvation bug).
        self.think_args: list[bool | None] = []
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
        self.think_args.append(think)
        return self.responder(messages)

    async def is_thinking_model(self) -> bool:
        return self.thinking

    async def health(self) -> bool:
        return True
