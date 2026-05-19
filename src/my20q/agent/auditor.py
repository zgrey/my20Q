"""Format auditor for reasoner output — a cheap backstop.

The reasoner is *told* to produce single yes/no queries (see
`prompts.GAME_SYSTEM_PROMPT`), but a model can still drift — e.g. into an
either/or question the caregiver's four buttons cannot answer.
`audit_query` catches the common format failures so the Reasoner can
re-prompt before such a query ever reaches the cockpit.

This is the *format* audit. A full on-topic audit — does the query stay
within the round's topic? — needs an LLM judge and is deferred; this
module is where that check will land.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WH_WORDS = {"what", "where", "when", "which", "who", "whom", "whose", "why", "how"}
_OR_RE = re.compile(r"\bor\b", re.IGNORECASE)


@dataclass
class AuditResult:
    ok: bool
    reason: str = ""


def audit_query(text: str) -> AuditResult:
    """Check that `text` is a single yes/no-answerable question.

    The caregiver has only four buttons (yes / no / kinda / not sure), so
    a query must be answerable by them: an actual question, not
    open-ended, and not an either/or choice.
    """
    stripped = text.strip()
    if "?" not in stripped:
        return AuditResult(False, "a query must be phrased as a question ending with '?'")
    words = stripped.lower().split()
    first = words[0].strip("\"'.,") if words else ""
    if first in _WH_WORDS:
        return AuditResult(
            False, f"'{first}…' is an open question; ask a yes/no question instead"
        )
    if _OR_RE.search(stripped):
        return AuditResult(
            False,
            "this reads as an either/or question; ask about ONE option as a "
            "plain yes/no and probe the other in a later query",
        )
    return AuditResult(True)
