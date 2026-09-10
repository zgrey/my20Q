"""piper text-to-speech — local, offline, fast.

Wraps the ``piper`` CLI (https://github.com/rhasspy/piper): text in on
stdin, a WAV file out. Availability is checked without running anything
(binary on PATH + voice model present), so the cockpit can degrade to
silent when piper has not been installed yet — no cloud fallback, ever.

Install (on the host that runs the API, e.g. cerberus):
    pip install piper-tts            # provides the `piper` entry point
    # download a voice model (.onnx + .onnx.json), then point at it:
    export MY20Q_PIPER_MODEL=/path/to/en_US-amy-medium.onnx

This module is the integration point verified at install time; until
then `available` is False and `synthesize` raises `TTSUnavailable`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from my20q.tts.base import TTSUnavailable

log = logging.getLogger(__name__)


class PiperTTS:
    """Synthesize speech with the local piper binary."""

    def __init__(
        self, *, bin: str = "piper", model: Path | None = None, timeout_s: float = 20.0
    ) -> None:
        self.bin = bin
        self.model = Path(model) if model else None
        self.timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return (
            shutil.which(self.bin) is not None
            and self.model is not None
            and self.model.is_file()
        )

    @property
    def voice(self) -> str | None:
        return self.model.stem if self.model else None

    @property
    def reason(self) -> str:
        if shutil.which(self.bin) is None:
            return f"piper binary {self.bin!r} not found on PATH"
        if self.model is None:
            return "no voice model configured (set MY20Q_PIPER_MODEL)"
        if not self.model.is_file():
            return f"voice model not found: {self.model}"
        return "ready"

    def synthesize(self, text: str) -> bytes:
        """Render text to WAV bytes via the piper CLI."""
        if not self.available:
            raise TTSUnavailable(self.reason)
        text = text.strip()
        if not text:
            raise TTSUnavailable("empty text")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name
        try:
            proc = subprocess.run(
                [self.bin, "--model", str(self.model), "--output_file", out_path],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
            if proc.returncode != 0:
                detail = proc.stderr.decode("utf-8", "replace")[:200]
                raise TTSUnavailable(f"piper exited {proc.returncode}: {detail}")
            data = Path(out_path).read_bytes()
            if not data:
                raise TTSUnavailable("piper produced no audio")
            return data
        except (OSError, subprocess.SubprocessError) as exc:
            raise TTSUnavailable(f"piper invocation failed: {exc}") from exc
        finally:
            with contextlib.suppress(OSError):
                os.unlink(out_path)
