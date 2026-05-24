"""TTS engine interface.

Text-to-speech is **local-only** in my20Q — patient-facing audio must
never be synthesized by a cloud service (the same privacy stance as the
LLM backend). The only engine is piper; this protocol exists so the API
layer can depend on a shape rather than a concrete class, and so an
engine can report *why* it is unavailable instead of crashing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class TTSUnavailable(RuntimeError):
    """Raised when synthesis cannot run (no binary, no model, or it failed)."""


@runtime_checkable
class TTSEngine(Protocol):
    @property
    def available(self) -> bool:
        """True when synthesis can actually run right now."""
        ...

    @property
    def voice(self) -> str | None:
        """A human label for the configured voice, or None."""
        ...

    @property
    def reason(self) -> str:
        """Why the engine is/ isn't available — surfaced to the cockpit."""
        ...

    def synthesize(self, text: str) -> bytes:
        """Render `text` to WAV bytes, or raise `TTSUnavailable`."""
        ...
