"""Async Ollama client over the /api/chat endpoint."""

from __future__ import annotations

import httpx2

from my20q.llm.base import LLMMessage, LLMUnavailable


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
        # Per-model "does it declare the thinking capability" cache, populated
        # lazily by ``is_thinking_model`` so we probe /api/show at most once per
        # model even as the active model is switched at runtime.
        self._thinking_cache: dict[str, bool] = {}

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 200,
        json_mode: bool = False,
        think: bool | None = None,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": self.temperature},
        }
        if json_mode:
            payload["format"] = "json"
        # A per-call think overrides the backend default (None).
        eff_think = think if think is not None else self.think
        if eff_think is not None:
            payload["think"] = eff_think
        try:
            async with httpx2.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise LLMUnavailable(f"Ollama call failed: {exc}") from exc

        message = data.get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        # A thinking model can put its whole answer in `thinking` and leave
        # `content` empty (especially when reasoning is long). For free-form
        # calls, salvage the reasoning rather than reporting an empty response —
        # the caller's format pass turns it into the final question. We never do
        # this for json_mode, where thinking is not valid JSON.
        thinking = message.get("thinking")
        if not json_mode and isinstance(thinking, str) and thinking.strip():
            return thinking.strip()
        raise LLMUnavailable("Ollama returned empty response")

    async def preload(self, timeout_s: float = 300.0) -> bool:
        """Load the current model into memory now (best-effort).

        Called on a runtime model switch so the next reasoning turn isn't a cold
        load: a model swap makes Ollama evict the old model and load the new one
        from disk, and that first call can overrun the per-call timeout and
        degrade the round to fallback. Hitting ``/api/generate`` with an empty
        prompt forces the load and returns as soon as it is resident. Generous
        timeout since a large model loads slowly; returns False on any failure.
        """
        try:
            async with httpx2.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": self.model, "prompt": "", "stream": False},
                )
                resp.raise_for_status()
            return True
        except (httpx2.HTTPError, ValueError):
            return False

    async def is_thinking_model(self) -> bool:
        """True if the *current* model declares the ``thinking`` capability.

        Probes ``/api/show`` (cached per model name). The reasoner uses this to
        decide whether the ask goes two-phase (deliberate→format, so a thinking
        model can reason without starving the JSON) or stays a single fast call.
        Any failure is treated as non-thinking, which keeps the fast path.
        """
        cached = self._thinking_cache.get(self.model)
        if cached is not None:
            return cached
        result = False
        try:
            async with httpx2.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/show", json={"model": self.model}
                )
                resp.raise_for_status()
                caps = resp.json().get("capabilities") or []
                result = "thinking" in caps
        except (httpx2.HTTPError, ValueError):
            result = False
        self._thinking_cache[self.model] = result
        return result

    async def health(self) -> bool:
        try:
            async with httpx2.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except httpx2.HTTPError:
            return False
