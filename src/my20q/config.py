"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["training", "operational"]
BackendChoice = Literal["ollama", "anthropic"]


@dataclass(frozen=True)
class Config:
    """Process-wide settings. See `from_env` for the environment variables.

    The privacy invariant (recording => real patient => local LLM) is
    enforced at backend-selection time, not here — this object only
    carries the raw settings.
    """

    ollama_base_url: str
    ollama_model: str
    ollama_timeout_s: float
    anthropic_api_key: str | None
    anthropic_model: str
    llm_backend: BackendChoice
    topics_path: Path
    profile_path: Path | None
    llm_enabled: bool
    max_queries: int
    mode: Mode
    recording_dir: Path
    recording_threshold_bytes: int
    # Text-to-speech (piper, local-only — never a cloud voice). The binary
    # and a voice model are installed on the host; until then the cockpit
    # reports audio as unavailable and stays silent. See tts/.
    tts_enabled: bool
    piper_bin: str
    piper_model: Path | None
    piper_timeout_s: float

    @classmethod
    def from_env(cls) -> Config:
        default_topics = Path(__file__).parent / "topics" / "data" / "topics.yaml"

        mode = os.environ.get("MY20Q_MODE", "training").strip().lower()
        if mode not in ("training", "operational"):
            raise ValueError(f"MY20Q_MODE must be 'training' or 'operational', got {mode!r}")

        backend = os.environ.get("MY20Q_BACKEND", "ollama").strip().lower()
        if backend not in ("ollama", "anthropic"):
            raise ValueError(f"MY20Q_BACKEND must be 'ollama' or 'anthropic', got {backend!r}")

        profile_env = os.environ.get("MY20Q_PROFILE", "").strip()
        piper_model_env = os.environ.get("MY20Q_PIPER_MODEL", "").strip()

        return cls(
            ollama_base_url=os.environ.get("MY20Q_OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("MY20Q_OLLAMA_MODEL", "gemma3:12b"),
            ollama_timeout_s=float(os.environ.get("MY20Q_OLLAMA_TIMEOUT", "120")),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.environ.get("MY20Q_ANTHROPIC_MODEL", "claude-opus-4-7"),
            llm_backend=backend,  # type: ignore[arg-type]
            topics_path=Path(os.environ.get("MY20Q_TOPICS", str(default_topics))),
            profile_path=Path(profile_env) if profile_env else None,
            llm_enabled=os.environ.get("MY20Q_LLM", "1") != "0",
            # 0 = unlimited (the default): synthesis is readiness-driven, not
            # capped by a question count. Set a positive value only as a hard
            # safety ceiling that ends the round without forcing an utterance.
            max_queries=int(os.environ.get("MY20Q_MAX_QUERIES", "0")),
            mode=mode,  # type: ignore[arg-type]
            recording_dir=Path(os.environ.get("MY20Q_DATA_DIR", "patient_data")),
            recording_threshold_bytes=(
                int(os.environ.get("MY20Q_RECORDING_THRESHOLD_MB", "25")) * 1024 * 1024
            ),
            tts_enabled=os.environ.get("MY20Q_TTS", "1") != "0",
            piper_bin=os.environ.get("MY20Q_PIPER_BIN", "piper"),
            piper_model=Path(piper_model_env) if piper_model_env else None,
            piper_timeout_s=float(os.environ.get("MY20Q_PIPER_TIMEOUT", "20")),
        )
