"""Safety guards: the emergency screen and LLM-output sanitizers.

Every patient-facing string the LLM produces — each query and the
synthesized utterance — passes through a sanitizer here before it can be
shown or spoken. See docs/design/beta-retool.md §5.
"""

from __future__ import annotations

import re

EMERGENCY_SCREEN = {
    "title": "Getting help",
    "body": "Stay calm. A caregiver is being notified.",
    "actions": [
        {"id": "call_caregiver", "label": "Call caregiver"},
        {"id": "call_911", "label": "Call 911"},
    ],
}

MAX_LLM_CHARS = 240
MAX_UTTERANCE_CHARS = 200

_FORBIDDEN_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"<[^>]+>"),
    re.compile(r"\b(?:diagnos\w*|prescrib\w*|dosage)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:mg|mcg|ml)\b", re.IGNORECASE),
)


def _has_forbidden(text: str) -> bool:
    return any(p.search(text) for p in _FORBIDDEN_PATTERNS)


def sanitize_llm_text(text: str) -> str:
    """Sanitize a single LLM-produced line — e.g. a query.

    Returns the cleaned string, or "" if the content is unacceptable;
    callers must fall back to a static template when empty.
    """
    stripped = text.strip()
    if not stripped:
        return ""
    if len(stripped) > MAX_LLM_CHARS:
        stripped = stripped[:MAX_LLM_CHARS].rsplit(" ", 1)[0] + "…"
    if _has_forbidden(stripped):
        return ""
    return stripped.splitlines()[0].strip().strip('"').strip("'").strip()


def sanitize_utterance(text: str) -> str:
    """Sanitize a synthesized utterance — the patient's candidate 'voice'.

    Unlike a query, an utterance is a full sentence, so whitespace and
    stray newlines are collapsed rather than truncated away. Returns ""
    if unacceptable.
    """
    collapsed = " ".join(text.split()).strip().strip('"').strip("'").strip()
    if not collapsed:
        return ""
    if _has_forbidden(collapsed):
        return ""
    if len(collapsed) > MAX_UTTERANCE_CHARS:
        collapsed = collapsed[:MAX_UTTERANCE_CHARS].rsplit(" ", 1)[0] + "…"
    return collapsed
