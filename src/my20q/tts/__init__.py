"""Text-to-speech — local-only (piper / kokoro). See base.py for the privacy stance."""

from __future__ import annotations

from my20q.tts.base import TTSEngine, TTSUnavailable
from my20q.tts.factory import select_tts
from my20q.tts.kokoro_tts import KokoroTTS
from my20q.tts.piper_tts import PiperTTS

__all__ = ["KokoroTTS", "PiperTTS", "TTSEngine", "TTSUnavailable", "select_tts"]
