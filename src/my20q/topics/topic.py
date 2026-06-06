"""Flat topic-list model — the high-level contexts that anchor each round.

A *topic* is the highest-level conditional dependency for a round. The
caregiver picks one to open a round; every query and the synthesized
utterance stay anchored to it. This replaces the deep taxonomy tree:
reasoning mode drives questioning under the topic, and `fallback_questions`
is a small ordered bank used only when the LLM is unreachable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FallbackQuestion(BaseModel):
    """One question in a topic's fallback bank.

    Used only in fallback mode (LLM unreachable): the bank is walked in
    order and a `yes`/`kinda` answer synthesizes this question's `label`.
    """

    id: str
    label: str = Field(description="The concrete need a 'yes' answer implies.")
    question: str = Field(description="Yes/no question whose 'yes' answer implies `label`.")
    image: str | None = Field(
        default=None,
        description="Optional curated pictogram override; otherwise retrieved by intent.",
    )
    emergency: bool = False


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
    image: str | None = Field(
        default=None,
        description="Optional curated pictogram override for the topic itself.",
    )
    fallback_questions: list[FallbackQuestion] = Field(default_factory=list)
