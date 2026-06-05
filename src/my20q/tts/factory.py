"""TTS engine selection.

Returns a local engine — `piper` (fast, flat) or `kokoro` (more natural) — or
None when TTS is disabled. There is deliberately no cloud-voice option:
patient-facing audio stays on the host, mirroring the LLM privacy invariant.
"""

from __future__ import annotations

from my20q.config import Config
from my20q.tts.base import TTSEngine
from my20q.tts.kokoro_tts import KokoroTTS
from my20q.tts.piper_tts import PiperTTS


def select_tts(config: Config) -> TTSEngine | None:
    """Return the configured TTS engine, or None when TTS is disabled.

    The engine is returned even when its model/binary is not yet installed — its
    `available`/`reason` let the API report the state so the cockpit can stay
    silent gracefully rather than erroring.
    """
    if not config.tts_enabled:
        return None
    if config.tts_engine == "kokoro":
        return KokoroTTS(
            model=config.kokoro_model,
            voices=config.kokoro_voices,
            voice=config.kokoro_voice,
            speed=config.kokoro_speed,
            lang=config.kokoro_lang,
        )
    return PiperTTS(
        bin=config.piper_bin,
        model=config.piper_model,
        timeout_s=config.piper_timeout_s,
    )
