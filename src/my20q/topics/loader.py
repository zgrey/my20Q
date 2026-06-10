"""YAML loader for the flat topic list."""

from __future__ import annotations

from pathlib import Path

import yaml

from my20q.topics.topic import Topic

DEFAULT_TOPICS_PATH = Path(__file__).parent / "data" / "topics.yaml"


def load_topics(path: Path | None = None) -> list[Topic]:
    """Load and validate the topic list, preserving file order.

    By convention the YAML pins the emergency topic first and the
    catch-all ("Other") last; order is otherwise caregiver-editable.
    """
    src = Path(path) if path is not None else DEFAULT_TOPICS_PATH
    with src.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"Topic list at {src} must be a YAML sequence at the root")
    topics = [Topic.model_validate(item) for item in raw]
    if not topics:
        raise ValueError(f"Topic list at {src} is empty")
    _check_unique_ids(topics)
    return topics


def find_topic(topics: list[Topic], topic_id: str) -> Topic | None:
    """Return the topic with `topic_id`, or None."""
    return next((t for t in topics if t.id == topic_id), None)


def _check_unique_ids(topics: list[Topic]) -> None:
    seen: set[str] = set()
    for topic in topics:
        if topic.id in seen:
            raise ValueError(f"Duplicate topic id: {topic.id!r}")
        seen.add(topic.id)
