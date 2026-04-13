"""Safety guards: emergency short-circuit and LLM output sanitizer."""

from __future__ import annotations

import re

from my20q.taxonomy.node import Node

EMERGENCY_SCREEN = {
    "title": "Getting help",
    "body": "Stay calm. A caregiver is being notified.",
    "actions": [
        {"id": "call_caregiver", "label": "Call caregiver"},
        {"id": "call_911", "label": "Call 911"},
    ],
}

MAX_LLM_CHARS = 240

_FORBIDDEN_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\b(diagnos[ei]|prescribe|dosage|mg\b|mcg\b)\b", re.IGNORECASE),
    re.compile(r"<[^>]+>"),
)


def is_emergency_path(path: list[Node]) -> bool:
    """Any node on the traversal path marked emergency triggers the short-circuit."""
    return any(n.emergency for n in path)


def sanitize_llm_text(text: str) -> str:
    """Enforce caregiver-safe output from the LLM.

    Returns the cleaned string, or an empty string if the content is
    unacceptable. Callers must fall back to a static template when empty.
    """
    stripped = text.strip()
    if not stripped:
        return ""
    if len(stripped) > MAX_LLM_CHARS:
        stripped = stripped[:MAX_LLM_CHARS].rsplit(" ", 1)[0] + "…"
    for pat in _FORBIDDEN_PATTERNS:
        if pat.search(stripped):
            return ""
    first_line = stripped.splitlines()[0].strip().strip('"').strip("'").strip()
    return first_line
