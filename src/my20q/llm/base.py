"""Abstract LLM backend so Ollama / llama.cpp / vLLM can be swapped."""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict


class LLMMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMUnavailable(RuntimeError):
    """Raised when the LLM cannot be reached. Callers should fall back gracefully."""


class LLMBackend(Protocol):
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
    ) -> str: ...

    async def health(self) -> bool: ...
