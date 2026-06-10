"""Format auditor for reasoner output.

The reasoner is *told* to produce a single yes/no question, but a model can still
drift to an open or either/or question the caregiver's four buttons can't answer.
:func:`audit_query` is the one cheap, deterministic backstop the ask loop enforces.

Redundancy and on-topic fit are deliberately NOT enforced here anymore — they are
nudged through the prompt instead. A hard reject used to dump the round into the
deterministic fallback bank (which loops), so a slightly-off real question is
always preferable to that. See docs/design/reasoning-retro.md.
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

    The caregiver has only four buttons (yes / no / kinda / not sure), so a query
    must be answerable by them: an actual question, not open-ended, and not an
    either/or choice.
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
