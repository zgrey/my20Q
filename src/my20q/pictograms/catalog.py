"""Pictogram catalog and intent-based retrieval.

Maps a query's text to the closest curated AAC concept — retrieval, never
generation (a locked decision in CLAUDE.md). Each concept points at an
open ARASAAC pictogram, fetched once by `scripts/fetch_icons.py`. When
nothing matches, retrieval returns None and the cockpit shows a neutral
placeholder rather than a misleading symbol.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CATALOG_PATH = Path(__file__).parent / "data" / "concepts.yaml"
_WORD_RE = re.compile(r"[a-z']+")


class Pictogram(BaseModel):
    """One curated AAC concept."""

    id: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    arasaac_query: str = Field(
        description="Search term scripts/fetch_icons.py resolves via the ARASAAC API.",
    )


def load_catalog(path: Path | None = None) -> list[Pictogram]:
    src = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    with src.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"Pictogram catalog at {src} must be a YAML sequence")
    catalog = [Pictogram.model_validate(item) for item in raw]
    seen: set[str] = set()
    for pictogram in catalog:
        if pictogram.id in seen:
            raise ValueError(f"Duplicate pictogram id: {pictogram.id!r}")
        seen.add(pictogram.id)
    return catalog


def retrieve(text: str, catalog: list[Pictogram]) -> Pictogram | None:
    """Return the concept whose keywords best match `text`, or None.

    A deterministic keyword-overlap score — cheap, predictable, and
    explainable. Ties resolve to catalog order.
    """
    words = set(_WORD_RE.findall(text.lower()))
    best: Pictogram | None = None
    best_score = 0
    for pictogram in catalog:
        score = sum(1 for kw in pictogram.keywords if kw in words)
        if score > best_score:
            best, best_score = pictogram, score
    return best
