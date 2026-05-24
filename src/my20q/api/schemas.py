"""Pydantic request/response models for the cockpit API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TopicOut(BaseModel):
    id: str
    label: str
    emergency: bool


class HealthOut(BaseModel):
    status: str
    mode: str
    engine: str  # "reasoning" | "fallback"
    real_patient: bool


class CreateSessionOut(BaseModel):
    session_id: str


class StartRoundIn(BaseModel):
    topic_id: str
    seed_context: str = ""


class AnswerIn(BaseModel):
    answer: Literal["yes", "no", "kinda", "not_sure"]


class ContextIn(BaseModel):
    text: str


class EventOut(BaseModel):
    """The current round event — a pending query/synthesis, or a terminal."""

    kind: str
    text: str = ""
    rationale: str = ""
    query_index: int = 0
    engine: str = "reasoning"
    emergency_screen: dict | None = None
    pictogram: str | None = None  # catalog concept id, or null when none matched


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
