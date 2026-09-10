"""Backend selection — the enforcement point for the privacy invariant.

recording => real patient => local LLM. The Anthropic (cloud) backend is
refused outright whenever a real patient profile is loaded; see
`docs/design/beta-retool.md` §5.
"""

from __future__ import annotations

from my20q.config import Config
from my20q.llm.anthropic_client import AnthropicBackend
from my20q.llm.base import LLMBackend
from my20q.llm.ollama_client import OllamaBackend
from my20q.profiles import PatientProfile, is_real_patient


class BackendRefused(RuntimeError):
    """Raised when the requested backend is disallowed for the loaded profile.

    This is a fail-closed error, not a soft fallback: a misconfiguration that
    would send real patient data to the cloud must stop the run, loudly.
    """


def select_backend(config: Config, profile: PatientProfile | None) -> LLMBackend | None:
    """Return the LLM backend for this run, or None for fallback mode.

    Enforces the invariant: a real patient profile forbids the cloud backend.
    """
    if not config.llm_enabled:
        return None

    if config.llm_backend == "anthropic":
        if is_real_patient(profile):
            raise BackendRefused(
                "The Anthropic (cloud) backend is refused: a real patient "
                "profile is loaded. Real-patient inference is local-only. Use "
                "a synthetic persona for cloud trials, or set MY20Q_BACKEND=ollama."
            )
        if not config.anthropic_api_key:
            raise BackendRefused(
                "MY20Q_BACKEND=anthropic but ANTHROPIC_API_KEY is not set."
            )
        return AnthropicBackend(
            api_key=config.anthropic_api_key,
            model=config.anthropic_model,
        )

    return OllamaBackend(
        base_url=config.ollama_base_url,
        model=config.ollama_model,
        timeout_s=config.ollama_timeout_s,
    )
