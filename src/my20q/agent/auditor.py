"""Format, redundancy, and on-topic auditors for reasoner output.

The reasoner is *told* to produce single, novel, on-topic yes/no queries, but a
model can still drift. These cheap deterministic backstops catch the common
failures so the Reasoner can re-prompt before a bad query reaches the cockpit:

- :func:`audit_query` — format: a single yes/no question (no wh-/either-or).
- :func:`is_repeat` — redundancy: the question restates one already asked.
- :func:`topic_violation` — on-topic: the question fits the round's high-level
  topic (feelings = emotion; body = physical; people = a person).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WH_WORDS = {"what", "where", "when", "which", "who", "whom", "whose", "why", "how"}
_OR_RE = re.compile(r"\bor\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z]+")

# Filler words dropped when reducing a question to its "content" (its subject/
# action/modifier words) for the redundancy check.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "do", "does", "did", "you", "your", "yours",
        "feel", "feeling", "feelings", "to", "of", "in", "on", "about", "right",
        "now", "that", "this", "it", "with", "and", "but", "or", "like", "something",
        "someone", "anyone", "they", "them", "their", "be", "being", "i", "am", "me",
        "my", "we", "us", "can", "could", "would", "will", "when", "because", "if",
        "have", "has", "had", "there", "more", "most", "really", "ever", "any",
        "some", "want", "wanting", "currently", "today", "mostly", "up", "not", "at",
        "for", "from", "make", "makes", "made", "what", "how", "was", "were", "been",
        "then", "so", "just", "very", "much", "get", "getting", "feels",
    }
)

#: Two questions are "the same question reworded" when their content words overlap
#: this much (Jaccard). Catches lexical near-duplicates; the prompt catches the rest.
REPEAT_JACCARD = 0.4

# "My feelings" must stay on emotion — these body words mean it drifted to the body.
_FEELINGS_FORBIDDEN = frozenset(
    {
        "pain", "hurt", "hurts", "ache", "aching", "sore", "dizzy", "thirsty",
        "thirst", "drink", "water", "hungry", "hunger", "eat", "eating", "toilet",
        "bathroom", "bladder", "arm", "arms", "leg", "legs", "foot", "feet", "toe",
        "toes", "stomach", "belly", "chest", "physical", "nausea", "breathe",
        "breathing",
    }
)
# "My people" must reference a person (generic word or a capitalized name).
_PEOPLE_WORDS = frozenset(
    {
        "family", "someone", "somebody", "people", "person", "everyone",
        "everybody", "them", "they", "their", "him", "her", "visit", "visiting",
        "call", "calling", "tell", "telling", "husband", "wife", "son", "daughter",
        "mother", "father", "mom", "dad", "friend", "sister", "brother", "loved",
        "ones", "company", "together",
    }
)


@dataclass
class AuditResult:
    ok: bool
    reason: str = ""


def content_words(text: str) -> frozenset[str]:
    """A question reduced to its meaningful subject/action/modifier words."""
    return frozenset(
        w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2
    )


def is_repeat(question: str, prior_questions: list[str]) -> bool:
    """True if ``question`` restates one already asked (content-word overlap)."""
    cw = content_words(question)
    if not cw:
        return False
    for prev in prior_questions:
        pw = content_words(prev)
        if pw and len(cw & pw) / len(cw | pw) >= REPEAT_JACCARD:
            return True
    return False


def topic_violation(topic_id: str, question: str) -> str:
    """Reason the question is off-topic for the round, or "" if it fits.

    Keeps each topic in its lane: feelings = emotion only, people = about a
    person. (Body has no clean keyword rule — the prompt handles it.)
    """
    words = set(_WORD_RE.findall(question.lower()))
    if topic_id == "mental_health" and (words & _FEELINGS_FORBIDDEN):
        return (
            "off-topic for 'My feelings': this asks about the body. Ask ONLY about "
            "an emotion or mental state."
        )
    if topic_id == "my_people":
        has_name = bool(re.search(r"(?<!^)(?<![.?!]\s)\b[A-Z][a-z]+", question.strip()))
        if not (words & _PEOPLE_WORDS) and not has_name:
            return (
                "off-topic for 'My people': name or refer to a specific person, or "
                "an action to/from them."
            )
    return ""


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
