"""TTS engine selection.

Only ever returns a local piper engine (or None when TTS is disabled).
There is deliberately no cloud-voice option — patient-facing audio stays
on the host, mirroring the LLM privacy invariant.
"""

from __future__ import annotations

from my20q.config import Config
from my20q.tts.base import TTSEngine
from my20q.tts.piper_tts import PiperTTS


def select_tts(config: Config) -> TTSEngine | None:
    """Return the TTS engine, or None when TTS is disabled.

    A PiperTTS is returned even when piper is not yet installed — its
    `available`/`reason` let the API report the state so the cockpit can
    stay silent gracefully rather than erroring.
    """
    if not config.tts_enabled:
        return None
    return PiperTTS(
        bin=config.piper_bin,
        model=config.piper_model,
        timeout_s=config.piper_timeout_s,
    )
