"""Tests for the session-scoped yes-memory (confirmed-answer store)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from my20q.recording.yes_memory import YesMemory, dated_path


def test_needs_are_deduped_most_recent_first() -> None:
    mem = YesMemory()
    mem.add(question="Are you cold?", needs=["I am cold"], topic_id="t", round_id="r1")
    # A later confirmation of the same need does not duplicate it.
    mem.add(question="Still cold?", needs=["I am cold"], topic_id="t", round_id="r1")
    mem.add(question="Hungry?", needs=["I am hungry"], topic_id="t", round_id="r2")
    assert mem.needs() == ["I am hungry", "I am cold"]  # most recent first, unique


def test_questions_are_deduped_most_recent_first() -> None:
    mem = YesMemory()
    mem.add(question="Are you cold?", needs=["I am cold"], topic_id="t", round_id="r1")
    mem.add(question="Hungry?", needs=["I am hungry"], topic_id="t", round_id="r1")
    assert mem.questions() == ["Hungry?", "Are you cold?"]


def test_file_is_appended_per_yes(tmp_path: Path) -> None:
    path = tmp_path / "yes.jsonl"
    mem = YesMemory(path=path)
    mem.add(question="Q1?", needs=["n1"], topic_id="t", round_id="r1")
    mem.add(question="Q2?", needs=["n2"], topic_id="t", round_id="r1")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2  # one durable JSON line per confirmed yes
    assert "Q1?" in lines[0] and "n2" in lines[1]


def test_no_file_when_path_unset(tmp_path: Path) -> None:
    # The in-memory store always works; with no path nothing is written to disk
    # (the synthetic / privacy-gated case).
    mem = YesMemory()
    mem.add(question="Q?", needs=["n"], topic_id="t", round_id="r1")
    assert mem.needs() == ["n"]
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere


def test_dated_path_shape() -> None:
    # Lives under a memory/ subdir so it never collides with session recordings.
    p = dated_path(Path("data"), "paula", today=_dt.date(2026, 6, 9))
    assert p == Path("data") / "paula" / "memory" / "yes_memory_2026-06-09.jsonl"
