"""Tests for the session-scoped yes-memory (confirmed-answer store)."""

from __future__ import annotations

import datetime as _dt
import json
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


# ------------------------------------------- W2-Q: joinable, correctly filed


def test_the_file_day_matches_the_timestamps_inside() -> None:
    # The day used to be the LOCAL date while entries were stamped UTC, so an
    # evening session filed itself under the wrong day: the real dataset has a
    # yes_memory_2026-08-31.jsonl containing only 2026-09-01T02:… timestamps.
    p = dated_path(Path("data"), "Paula")
    today = _dt.datetime.now(_dt.UTC).date().isoformat()
    assert p.name == f"yes_memory_{today}.jsonl"


def test_a_confirmed_yes_can_be_joined_to_its_recording(tmp_path) -> None:
    mem = YesMemory(path=tmp_path / "yes.jsonl")
    mem.add(question="Is it a drink?", needs=["a drink"], topic_id="general",
            round_id="2607063ce3a444b9982597286657a030", session_id="abc123")
    rec = mem.items[0]
    # The two ids a recording is addressed by — both present, both joinable.
    assert rec["round_id"] == "2607063ce3a444b9982597286657a030"
    assert rec["session_id"] == "abc123"
    on_disk = json.loads((tmp_path / "yes.jsonl").read_text(encoding="utf-8"))
    assert on_disk["session_id"] == "abc123"


def test_session_id_is_omitted_when_there_is_none() -> None:
    # The CLI harness records nothing and has no session id; the field should
    # simply be absent rather than empty.
    mem = YesMemory()
    mem.add(question="q", needs=["x"], topic_id="general", round_id="r1")
    assert "session_id" not in mem.items[0]


async def test_the_round_id_matches_the_one_the_recording_uses(topics) -> None:
    # Session.start_round takes the id the API will record under, so the round,
    # its recording and its yes-log entries all agree. Before W2-Q the API
    # minted a uuid separately and the log kept an ordinal.
    from my20q.agent.dialogue import Session

    session = Session(topics, llm=None, session_id="sess-1")
    rnd = session.start_round("general", round_id="round-uuid-1")
    assert rnd.round_id == "round-uuid-1"
    assert rnd.session_id == "sess-1"
    # And it still falls back to an ordinal for the CLI.
    assert session.start_round("general").round_id == "r2"
