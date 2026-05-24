"""Tests for the local TTS scaffold (piper).

The piper binary/model are not installed in CI, so these verify the
graceful-unavailable behavior — the install itself is verified on the
host. See src/my20q/tts/.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from my20q.config import Config
from my20q.tts import PiperTTS, TTSUnavailable, select_tts


def test_piper_unavailable_without_model() -> None:
    engine = PiperTTS(bin="definitely-not-a-real-binary-xyz", model=None)
    assert engine.available is False
    assert "not found" in engine.reason or "no voice model" in engine.reason
    with pytest.raises(TTSUnavailable):
        engine.synthesize("hello")


def test_piper_reports_missing_model(tmp_path: Path) -> None:
    engine = PiperTTS(bin="python", model=tmp_path / "nope.onnx")
    # binary may resolve, but the model file does not exist
    assert engine.available is False
    assert "voice model not found" in engine.reason


def test_select_tts_disabled_returns_none() -> None:
    cfg = replace(Config.from_env(), tts_enabled=False)
    assert select_tts(cfg) is None


def test_select_tts_enabled_returns_piper() -> None:
    cfg = replace(Config.from_env(), tts_enabled=True, piper_model=None)
    engine = select_tts(cfg)
    assert isinstance(engine, PiperTTS)
    assert engine.available is False  # not installed in CI


pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from my20q.api.app import create_app  # noqa: E402


def test_tts_status_endpoint_reports_unavailable() -> None:
    client = TestClient(create_app(replace(Config.from_env(), llm_enabled=False), backend=None))
    status = client.get("/api/tts/status").json()
    assert status["available"] is False
    assert "reason" in status


def test_tts_synthesize_503_when_unavailable() -> None:
    client = TestClient(create_app(replace(Config.from_env(), llm_enabled=False), backend=None))
    resp = client.post("/api/tts", json={"text": "hello"})
    assert resp.status_code == 503


def test_tts_disabled_via_config() -> None:
    cfg = replace(Config.from_env(), llm_enabled=False, tts_enabled=False)
    client = TestClient(create_app(cfg, backend=None))
    assert client.get("/api/tts/status").json()["available"] is False
    assert client.post("/api/tts", json={"text": "hi"}).status_code == 503
