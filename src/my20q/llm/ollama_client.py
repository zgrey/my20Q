"""Async Ollama client over the /api/chat endpoint."""

from __future__ import annotations

import httpx

from my20q.llm.base import ChatResult, LLMMessage, LLMUnavailable


class OllamaBackend:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2:3b",
        timeout_s: float = 30.0,
        temperature: float = 0.4,
        think: bool | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.temperature = temperature
        # Thinking-model control. None = leave it to the model's default;
        # False disables the reasoning phase so all tokens go to content (e.g.
        # gemma4's thinking would otherwise exhaust num_predict before the JSON
        # answer is emitted). Harmlessly ignored by non-thinking models.
        self.think = think

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
        think: bool | None = None,
    ) -> str:
        result = await self.chat_full(
            messages, max_tokens=max_tokens, json_mode=json_mode, think=think
        )
        return result.content

    async def chat_full(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
        think: bool | None = None,
    ) -> ChatResult:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": self.temperature},
        }
        if json_mode:
            payload["format"] = "json"
        # Per-call think overrides the backend default (None).
        eff_think = think if think is not None else self.think
        if eff_think is not None:
            payload["think"] = eff_think
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMUnavailable(f"Ollama call failed: {exc}") from exc

        message = data.get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailable("Ollama returned empty response")
        thinking = message.get("thinking")
        return ChatResult(
            content=content.strip(),
            thinking=thinking.strip() if isinstance(thinking, str) else "",
        )

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
