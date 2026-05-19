"""Flat topic list — the high-level contexts that anchor each round."""

from __future__ import annotations

from my20q.topics.loader import DEFAULT_TOPICS_PATH, find_topic, load_topics
from my20q.topics.topic import FallbackQuestion, Topic

__all__ = [
    "DEFAULT_TOPICS_PATH",
    "FallbackQuestion",
    "Topic",
    "find_topic",
    "load_topics",
]
