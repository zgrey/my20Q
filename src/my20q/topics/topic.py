"""Flat topic-list model — the high-level contexts that anchor each round.

A *topic* is the highest-level conditional dependency for a round. The
caregiver picks one to open a round; every query and the synthesized
utterance stay anchored to it. Reasoning mode drives all questioning under
the topic — there is no canned question bank (a reasoning failure surfaces a
diagnostic, never a pre-written question).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

_FACETS = ("who", "what", "when", "where", "why", "how")


class Topic(BaseModel):
    """One high-level context for a round."""

    id: str
    label: str
    emergency: bool = False
    description: str | None = Field(
        default=None,
        description="Short context for the LLM; never shown to the patient.",
    )
    reasoning_hint: str | None = Field(
        default=None,
        description="Topic-scoped guidance threaded into the reasoning-mode prompt.",
    )
    seed_universal_wants: bool = Field(
        default=True,
        description=(
            "Whether seeding always injects the universal physical wants "
            "(thirst, hunger, toilet, pain, temperature). True for body / "
            "catch-all topics where a basic need is easy to miss; set False for "
            "topics like feelings or people, where those wants are off-topic "
            "noise that crowds out profile-grounded candidates."
        ),
    )
    core_facets: list[str] = Field(
        default_factory=lambda: ["what", "how"],
        description=(
            "The 5W1H categories that must be confidently determined before a "
            "synthesis is considered board-ready under this topic (the rest "
            "are modifiers, placeholdered when unknown). Subset of "
            "who/what/when/where/why/how."
        ),
    )
    direction: bool = Field(
        default=False,
        description=(
            "Track intent DIRECTION for this topic (person topics): the four "
            "buckets (I-do-for-them / they-do-for-me / tell / ask) become "
            "standing 'how' contenders, credited by a code classifier, with "
            "the sign-flip rule (a no on one pole nudges the mirror pole) and "
            "the caregiver ask-order prior."
        ),
    )
    image: str | None = Field(
        default=None,
        description="Optional curated pictogram override for the topic itself.",
    )

    @field_validator("core_facets")
    @classmethod
    def _known_facets(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in _FACETS]
        if unknown:
            raise ValueError(f"unknown facet categories: {unknown}")
        return v
