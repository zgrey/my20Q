"""Abstract LLM backend so Ollama / llama.cpp / vLLM can be swapped."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict


class LLMMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class ChatResult:
    """A completion plus the model's reasoning, when it exposes one.

    ``thinking`` is the hidden reasoning channel of a "thinking" model (e.g.
    gemma4 via Ollama's ``message.thinking``); empty for models without one.
    Surfaced — summarized — in the cockpit so the explicit augmented-reasoning
    strategy can be measured against a model's opaque reasoning.
    """

    content: str
    thinking: str = ""


class LLMUnavailable(RuntimeError):
    """Raised when the LLM cannot be reached. Callers should fall back gracefully."""


class LLMBackend(Protocol):
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
        think: bool | None = None,
    ) -> str:
        """Generate a completion (content only).

        ``think`` overrides the backend's thinking mode for this one call
        (None = use the backend default). The reasoner uses it to let a thinking
        model deliberate freely in the reasoning phase, then forces thinking off
        for the cheap JSON-formatting phase. Backends without a thinking mode
        ignore it.
        """
        ...

    async def chat_full(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
        think: bool | None = None,
    ) -> ChatResult:
        """Like :meth:`chat` but also returns the model's thinking channel."""
        ...

    async def health(self) -> bool: ...
