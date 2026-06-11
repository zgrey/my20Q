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

from my20q.agent.facets import _content_tokens, _tokens_match

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
#: Content-token overlap (Dice, stem/prefix-tolerant) at or above this counts
#: as the same question in new clothes ("Will Zach help with tasks when he
#: comes over?" vs "Do you want Zach to come over to help with tasks?" — same
#: content, different framing; "comes"/"come" still count as the same word).
_REPEAT_OVERLAP = 0.8
#: Auxiliaries / framing words that restate a question without changing what
#: it asks — excluded from the content comparison.
_FRAMING = frozenset(
    [
        "will", "would", "can", "could", "should", "shall", "does", "did",
        "when", "while", "now", "right", "really", "just", "please", "about",
        "going", "trying", "hoping",
    ]
)


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
    tokens = _content_tokens(question) - _FRAMING
    for prior in asked:
        prior_norm = _normalize(prior)
        if not prior_norm:
            continue
        if norm == prior_norm:
            return prior
        if SequenceMatcher(None, norm, prior_norm).ratio() >= _REPEAT_RATIO:
            return prior
        prior_tokens = _content_tokens(prior) - _FRAMING
        if tokens and prior_tokens and _overlap(tokens, prior_tokens) >= _REPEAT_OVERLAP:
            return prior
    return None


def _overlap(a: set[str], b: set[str]) -> float:
    """Dice overlap of two token sets, tolerant of stemming artifacts.

    Plain set intersection misses pairs the stemmer splits ("comes" → "com"
    but "come" → "come"), so each side counts via the same prefix-tolerant
    match the facet board uses for mention anchoring.
    """
    matched = sum(1 for t in a if any(_tokens_match(t, p) for p in b))
    matched += sum(1 for p in b if any(_tokens_match(p, t) for t in a))
    return matched / (len(a) + len(b))
