"""Anthropic (Claude) backend — for synthetic-persona development trials only.

This backend is gated OFF whenever a real patient profile is loaded — see
`my20q.llm.factory.select_backend` and `docs/design/beta-retool.md` §5. The
cloud backend and a real patient's data must never coexist.

The `anthropic` SDK is an optional dependency (the `trials` extra); it is
imported lazily so the package works without it for local-only deployments.
"""

from __future__ import annotations

from my20q.llm.base import ChatResult, LLMMessage, LLMUnavailable

_JSON_DIRECTIVE = (
    "Respond with a single valid JSON object and nothing else — "
    "no prose, no explanation, no markdown fences."
)


class AnthropicBackend:
    """`LLMBackend` implementation over the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-7",
        *,
        max_retries: int = 2,
    ) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - import guard
            raise LLMUnavailable(
                "The 'anthropic' package is not installed. Install the trials "
                "extra: pip install -e '.[trials]'"
            ) from exc
        self.model = model
        self._client = AsyncAnthropic(api_key=api_key, max_retries=max_retries)

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
        think: bool | None = None,  # noqa: ARG002 - no Ollama-style toggle here
    ) -> ChatResult:
        import anthropic

        # The Anthropic API takes the system prompt separately from the
        # alternating user/assistant turns.
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        turns = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] in ("user", "assistant")
        ]
        if json_mode:
            system_parts.append(_JSON_DIRECTIVE)
        system = "\n\n".join(p for p in system_parts if p.strip())

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": turns,
            # Auto-caches the stable prefix (system prompt) across a round's
            # queries; silently no-ops if the prefix is below the cache floor.
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            kwargs["system"] = system

        try:
            resp = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise LLMUnavailable(f"Anthropic call failed: {exc}") from exc

        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        thinking = "".join(
            getattr(b, "thinking", "") for b in resp.content if b.type == "thinking"
        ).strip()
        if not text:
            raise LLMUnavailable("Anthropic returned an empty response")
        return ChatResult(content=text, thinking=thinking)

    async def health(self) -> bool:
        import anthropic

        try:
            await self._client.models.retrieve(self.model)
            return True
        except anthropic.APIError:
            return False
