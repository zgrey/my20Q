"""Kokoro text-to-speech — local, offline, warmer than piper.

Wraps `kokoro-onnx` (https://github.com/thewh1teagle/kokoro-onnx), which runs the
open-weight Kokoro-82M model through ONNX Runtime — near real-time on CPU,
Apache-2.0. Like piper this is **local-only**: patient audio never leaves the
host, mirroring the LLM privacy invariant.

Install (on the API host, e.g. cerberus):
    pip install -e '.[kokoro]'
    # Download the model + voices once (from the kokoro-onnx releases):
    #   kokoro-v1.0.onnx      and      voices-v1.0.bin
    # then select and point at them:
    export MY20Q_TTS_ENGINE=kokoro
    export MY20Q_KOKORO_MODEL=/path/to/kokoro-v1.0.onnx
    export MY20Q_KOKORO_VOICES=/path/to/voices-v1.0.bin
    # optional: MY20Q_KOKORO_VOICE (e.g. af_heart, am_michael, bf_emma),
    #           MY20Q_KOKORO_SPEED, MY20Q_KOKORO_LANG

Availability is checked without loading the model (package importable + both
files present); the model is loaded lazily on first synthesize and cached, since
loading is the slow part.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import wave
from pathlib import Path

from my20q.tts.base import TTSUnavailable

log = logging.getLogger(__name__)


class KokoroTTS:
    """Synthesize speech locally with the Kokoro-82M ONNX model."""

    def __init__(
        self,
        *,
        model: Path | None = None,
        voices: Path | None = None,
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> None:
        self.model = Path(model) if model else None
        self.voices = Path(voices) if voices else None
        self.voice_name = voice
        self.speed = speed
        self.lang = lang
        self._engine = None  # lazily-loaded kokoro_onnx.Kokoro, cached

    @property
    def _package_present(self) -> bool:
        return importlib.util.find_spec("kokoro_onnx") is not None

    @property
    def available(self) -> bool:
        return (
            self._package_present
            and self.model is not None
            and self.model.is_file()
            and self.voices is not None
            and self.voices.is_file()
        )

    @property
    def voice(self) -> str | None:
        return self.voice_name

    @property
    def reason(self) -> str:
        if not self._package_present:
            return "kokoro-onnx not installed (pip install -e '.[kokoro]')"
        if self.model is None:
            return "no model configured (set MY20Q_KOKORO_MODEL)"
        if not self.model.is_file():
            return f"model not found: {self.model}"
        if self.voices is None:
            return "no voices file configured (set MY20Q_KOKORO_VOICES)"
        if not self.voices.is_file():
            return f"voices file not found: {self.voices}"
        return "ready"

    def _load(self):
        """Load (and cache) the Kokoro model. Slow — first call only."""
        if self._engine is None:
            from kokoro_onnx import Kokoro

            log.info("loading Kokoro model %s", self.model)
            self._engine = Kokoro(str(self.model), str(self.voices))
        return self._engine

    def synthesize(self, text: str) -> bytes:
        """Render text to WAV bytes via Kokoro."""
        if not self.available:
            raise TTSUnavailable(self.reason)
        text = text.strip()
        if not text:
            raise TTSUnavailable("empty text")
        try:
            samples, sample_rate = self._load().create(
                text, voice=self.voice_name, speed=self.speed, lang=self.lang
            )
        except TTSUnavailable:
            raise
        except Exception as exc:  # any kokoro / onnxruntime / espeak failure
            raise TTSUnavailable(f"kokoro synthesis failed: {exc}") from exc
        if samples is None or len(samples) == 0:
            raise TTSUnavailable("kokoro produced no audio")
        return _to_wav(samples, int(sample_rate))


def _to_wav(samples, sample_rate: int) -> bytes:
    """Mono float32 samples in [-1, 1] -> 16-bit PCM WAV bytes (stdlib + numpy).

    numpy is always present when kokoro-onnx is (ONNX Runtime depends on it), so
    importing it lazily here keeps it off the core dependency list.
    """
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0) * 32767.0).astype(
        "<i2"
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()
