"""Format auditors for reasoner output.

The reasoner is *told* to produce a single, fresh yes/no question, but a model
can still drift to an open or either/or question, leak its reasoning language
into the question, or repeat itself verbatim (gemma3:12b asked the same
question seven times in one trial). These are the cheap, deterministic
backstops the ask loop enforces:

- :func:`audit_query` — answerable as yes/no, and free of meta/reasoning
  language that should never be read to the patient.
- :func:`is_repeat` — a near-duplicate of an already-asked question. Enforced
  in CODE because prompt nudges demonstrably failed; with the canned fallback
  bank removed, a hard reject can no longer dump the round into a worse path —
  it just retries, and a persistent repeat loop triggers the round's
  context-restart recovery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_WH_WORDS = {"what", "where", "when", "which", "who", "whom", "whose", "why", "how"}
_OR_RE = re.compile(r"\bor\b", re.IGNORECASE)

#: Reasoning/meta vocabulary that must never appear in a patient-facing
#: question — its presence means the model leaked its deliberation into the
#: question text instead of asking plainly.
_META_PHRASES = (
    "the draft",
    "candidate",
    "contender",
    "the board",
    "this question",
    "the patient",
    "the user",
    "yes/no",
    "slot",
    "category",
)

#: Similarity at or above this ratio counts as a reworded repeat.
_REPEAT_RATIO = 0.85


@dataclass
class AuditResult:
    ok: bool
    reason: str = ""


def audit_query(text: str) -> AuditResult:
    """Check that `text` is a single, plainly-worded yes/no-answerable question.

    The caregiver has only four buttons (yes / no / kinda / not sure), so a
    query must be answerable by them: an actual question, not open-ended, not
    an either/or choice, and free of leaked reasoning language.
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
    lowered = stripped.lower()
    for phrase in _META_PHRASES:
        if phrase in lowered:
            return AuditResult(
                False,
                f"the question leaks reasoning language ('{phrase}'); ask the "
                "person plainly, in everyday words",
            )
    return AuditResult(True)


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.casefold()).split())


def is_repeat(question: str, asked: list[str]) -> str | None:
    """The prior question `question` duplicates, or None when genuinely new.

    Catches exact matches after normalization and high-similarity rewords
    ("Do you need help with a specific task right now?" vs "Do you need help
    completing a specific task right now?"). Compared against EVERY question
    asked this round — including ones from dumped context segments, so a
    context restart never causes the round to re-ask what it already asked.
    """
    norm = _normalize(question)
    if not norm:
        return None
    for prior in asked:
        prior_norm = _normalize(prior)
        if not prior_norm:
            continue
        if norm == prior_norm:
            return prior
        if SequenceMatcher(None, norm, prior_norm).ratio() >= _REPEAT_RATIO:
            return prior
    return None
