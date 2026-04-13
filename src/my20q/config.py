"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    ollama_base_url: str
    ollama_model: str
    ollama_timeout_s: float
    taxonomy_path: Path
    llm_enabled: bool
    max_turns: int

    @classmethod
    def from_env(cls) -> Config:
        default_taxonomy = Path(__file__).parent / "taxonomy" / "data" / "tree.yaml"
        return cls(
            ollama_base_url=os.environ.get("MY20Q_OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("MY20Q_OLLAMA_MODEL", "gemma3:12b"),
            ollama_timeout_s=float(os.environ.get("MY20Q_OLLAMA_TIMEOUT", "30")),
            taxonomy_path=Path(os.environ.get("MY20Q_TAXONOMY", str(default_taxonomy))),
            llm_enabled=os.environ.get("MY20Q_LLM", "1") != "0",
            max_turns=int(os.environ.get("MY20Q_MAX_TURNS", "20")),
        )
