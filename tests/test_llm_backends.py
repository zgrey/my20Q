"""Async tests for the LLM backends.

OllamaBackend tests are gated on MY20Q_INTEGRATION=1 so they only run
against a real local Ollama install. The mock backend is always tested.
"""

from __future__ import annotations

import os

import pytest

from my20q.llm import MockBackend, OllamaBackend


async def test_mock_backend_records_calls() -> None:
    mock = MockBackend(responder=lambda msgs: "hello back")
    out = await mock.chat([{"role": "user", "content": "hi"}])
    assert out == "hello back"
    assert len(mock.calls) == 1


async def test_mock_health_is_true() -> None:
    assert await MockBackend().health() is True


@pytest.mark.skipif(
    os.environ.get("MY20Q_INTEGRATION") != "1",
    reason="Set MY20Q_INTEGRATION=1 to run against a real Ollama instance",
)
async def test_ollama_roundtrip() -> None:
    backend = OllamaBackend(
        base_url=os.environ.get("MY20Q_OLLAMA_URL", "http://localhost:11434"),
        model=os.environ.get("MY20Q_OLLAMA_MODEL", "llama3.2:3b"),
    )
    assert await backend.health() is True
    out = await backend.chat(
        [{"role": "user", "content": "Say the single word: ready"}],
        max_tokens=10,
    )
    assert isinstance(out, str) and out
