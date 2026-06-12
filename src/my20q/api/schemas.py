"""Pydantic request/response models for the cockpit API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TopicOut(BaseModel):
    id: str
    label: str
    emergency: bool


class HealthOut(BaseModel):
    status: str
    mode: str
    engine: str  # "reasoning" | "fallback"
    real_patient: bool


class ModelsOut(BaseModel):
    """Local Ollama models available for human-trial selection."""

    models: list[str] = Field(default_factory=list)
    current: str | None = None
    can_select: bool = False  # false when the backend isn't local Ollama


class ModelSelectIn(BaseModel):
    model: str


class CreateSessionOut(BaseModel):
    session_id: str


class StartRoundIn(BaseModel):
    topic_id: str
    seed_context: str = ""


class AnswerIn(BaseModel):
    answer: Literal["yes", "no", "kinda", "not_sure"]


class ContextIn(BaseModel):
    text: str


class FacetContenderOut(BaseModel):
    """One contender value of a facet category, with its consensus points."""

    value: str
    score: float
    # The contender this one refines ("tingling" → "discomfort"), or null —
    # lets the tile render the dive: parent › child.
    parent: str | None = None


class FacetOut(BaseModel):
    """One 5W1H category of the live board — the honest reasoning tile."""

    category: str  # who | what | when | where | why | how
    label: str
    contenders: list[FacetContenderOut] = Field(default_factory=list)
    focus: bool = False  # the slot the current question targets


class EventOut(BaseModel):
    """The current round event — a pending query/synthesis, a diagnostic, or a terminal."""

    kind: str
    text: str = ""
    rationale: str = ""
    preface: str = ""  # short spoken lead-in read aloud just before the query
    query_index: int = 0
    engine: str = "reasoning"
    emergency_screen: dict | None = None
    pictogram: str | None = None  # catalog concept id, or null when none matched
    # The live 5W1H facet board (per-category contender scores) that drove this
    # question. Empty for emergency/terminal/diagnostic events.
    facets: list[FacetOut] = Field(default_factory=list)
    # For kind == "diagnostic": what failed and what was attempted (reason,
    # llm_unreachable, restart_attempted, consecutive_failures).
    diagnostic: dict | None = None
    # The question this one replaced via the opposition button ("" otherwise).
    flipped_from: str = ""


class BannerPartOut(BaseModel):
    """One woven slot of the live draft, with its confidence band."""

    category: str
    value: str
    band: str  # "locked" | "working"


class BannerBanOut(BaseModel):
    """A value the caregiver struck from the proposal (✗-edit)."""

    category: str
    value: str


class BannerOut(BaseModel):
    """The living proposal banner — the evolving draft utterance.

    `state` is "pending" (render the glowing "Pending synthesis…") or
    "draft"; `ready` lights the propose-ready vibrance (board-readiness).
    `banned` values render struck through; `muted` slots dimmed + struck.
    """

    state: str = "pending"
    text: str = ""
    ready: bool = False
    parts: list[BannerPartOut] = Field(default_factory=list)
    banned: list[BannerBanOut] = Field(default_factory=list)
    muted: list[str] = Field(default_factory=list)


class EditIn(BaseModel):
    """The ✗-note: a real-time edit applied against the live draft."""

    text: str


class ReplaceIn(BaseModel):
    """The synthesis editor: a clicked draft segment's precise edit.

    `old` is the segment's current value (may be "" for a pure addition);
    `new` is the dropdown pick or typed replacement — "" means "remove this
    detail" (the category is muted).
    """

    category: str
    old: str = ""
    new: str = ""


class HistoryEntryOut(BaseModel):
    kind: str
    text: str
    answer: str | None = None
    rationale: str = ""  # the reasoner's "why" for this query/synthesis


class RoundStateOut(BaseModel):
    """Full round state — the cockpit renders from this single source."""

    session_id: str
    round_id: str
    topic_id: str
    event: EventOut
    banner: BannerOut = Field(default_factory=BannerOut)
    history: list[HistoryEntryOut]
    outcome: str | None = None
    final_utterance: str = ""
    query_count: int = 0
    engine: str = "reasoning"


class RecordingStatusOut(BaseModel):
    """Recorded-dataset status for the cockpit's monitor."""

    enabled: bool  # a real patient profile is loaded
    paused: bool
    bytes: int
    threshold_bytes: int
    status: str  # "ok" | "warning" | "over" | "disabled"
    rounds: int


class PauseIn(BaseModel):
    paused: bool


class EmotionIn(BaseModel):
    """Caregiver emotional-slider reading: pair id -> value in [-1, 1]."""

    values: dict[str, float]


class RecordingFileOut(BaseModel):
    """A recorded session on disk — for the review dashboard's picker."""

    session_id: str
    rounds: int
    bytes: int
    modified: str


class TTSStatusOut(BaseModel):
    """Whether local (piper) speech is available, and why/why not."""

    available: bool
    voice: str | None = None
    reason: str


class TTSIn(BaseModel):
    text: str
