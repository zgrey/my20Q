"""Tests for the pictogram catalog and intent retrieval."""

from __future__ import annotations

from my20q.pictograms import load_catalog, retrieve


def test_catalog_loads_with_unique_ids() -> None:
    catalog = load_catalog()
    assert len(catalog) > 10
    ids = [p.id for p in catalog]
    assert len(ids) == len(set(ids))


def test_retrieve_matches_a_concept_by_keyword() -> None:
    catalog = load_catalog()
    match = retrieve("Are you in pain right now?", catalog)
    assert match is not None and match.id == "pain"


def test_retrieve_matches_a_family_member() -> None:
    catalog = load_catalog()
    match = retrieve("Would you like to call your daughter?", catalog)
    assert match is not None and match.id in {"phone", "daughter"}


def test_retrieve_returns_none_when_nothing_matches() -> None:
    catalog = load_catalog()
    assert retrieve("zzz qqq nothing applicable", catalog) is None
