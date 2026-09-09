"""Tests for the recording pruner.

This tool deletes and moves real patient records, so the rules it applies are
worth pinning precisely: what counts as vacuous, what is exempt, that a kept
round comes back byte-identical, and that a dry run writes nothing at all.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prune_recordings.py"


@pytest.fixture(scope="module")
def pruner():
    spec = importlib.util.spec_from_file_location("prune_recordings", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    yield module
    sys.modules.pop(spec.name, None)


def _round(**over) -> dict:
    base = {
        "session_id": "s",
        "round_id": "r",
        "topic_id": "my_people",
        "outcome": "abandoned",
        "queries": [],
        "recorded_at": "2026-06-11T18:00:00+00:00",
    }
    base.update(over)
    return base


ANSWERED = _round(
    outcome="synthesized",
    queries=[{"kind": "query", "text": "q", "answer": "yes"}],
)


def _session(dir_: Path, name: str, rounds: list[dict]) -> Path:
    path = dir_ / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in rounds), encoding="utf-8"
    )
    return path


# ------------------------------------------------------------- the rule


def test_a_round_with_no_entries_is_vacuous(pruner) -> None:
    assert pruner.is_vacuous(_round()) is True


def test_an_answered_round_is_never_vacuous(pruner) -> None:
    assert pruner.is_vacuous(ANSWERED) is False


def test_an_unanswered_round_that_did_something_is_kept(pruner) -> None:
    # A caregiver note, or a diagnostic the caregiver saw, is content — the
    # round recorded something even though no question was answered.
    assert not pruner.is_vacuous(
        _round(queries=[{"kind": "context", "text": "she slept badly"}])
    )
    assert not pruner.is_vacuous(
        _round(queries=[{"kind": "diagnostic", "text": "ask failed"}])
    )


def test_emergency_rounds_are_exempt(pruner) -> None:
    # The emergency topic short-circuits the LLM by design, so zero queries is
    # CORRECT there — and the record is the only evidence the safety path ran.
    # Exempt by outcome, not topic id, so a renamed emergency topic still holds.
    assert pruner.is_vacuous(_round(outcome="emergency", topic_id="anything")) is False


# ------------------------------------------------------------- the plan


def test_dry_run_writes_nothing(pruner, tmp_path) -> None:
    doomed = _session(tmp_path, "aaaa1111", [_round()])
    before = doomed.read_bytes()
    result = pruner.plan(tmp_path)
    assert [n for n, _ in result["delete"]] == ["aaaa1111.jsonl"]
    assert doomed.exists() and doomed.read_bytes() == before


def test_a_file_of_only_vacuous_rounds_is_deleted_whole(pruner, tmp_path) -> None:
    _session(tmp_path, "aaaa1111", [_round(), _round()])
    _session(tmp_path, "bbbb2222", [ANSWERED])
    result = pruner.plan(tmp_path)
    pruner.apply_plan(tmp_path, result, backup=False)
    assert not (tmp_path / "aaaa1111.jsonl").exists()
    assert (tmp_path / "bbbb2222.jsonl").exists()


def test_kept_rounds_come_back_byte_identical(pruner, tmp_path) -> None:
    # The whole point of filtering LINES rather than re-serialising: a round we
    # keep must not be altered, not even its JSON formatting.
    path = _session(tmp_path, "cccc3333", [_round(), ANSWERED, _round()])
    original = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    pruner.apply_plan(tmp_path, pruner.plan(tmp_path), backup=False)
    after = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    assert after == [original[1]]


def test_backup_captures_every_file_before_the_change(pruner, tmp_path) -> None:
    _session(tmp_path, "aaaa1111", [_round()])
    _session(tmp_path, "bbbb2222", [ANSWERED])
    backup = pruner.apply_plan(tmp_path, pruner.plan(tmp_path), backup=True)
    assert backup is not None and backup.is_dir()
    assert {p.name for p in backup.glob("*.jsonl")} == {
        "aaaa1111.jsonl",
        "bbbb2222.jsonl",
    }
    assert not (tmp_path / "aaaa1111.jsonl").exists()  # deleted from the live dir


# ------------------------------------------------------------- archiving


def test_archive_moves_only_fully_older_sessions(pruner, tmp_path) -> None:
    _session(tmp_path, "old00001", [ANSWERED])
    _session(
        tmp_path,
        "straddle",
        [ANSWERED, dict(ANSWERED, recorded_at="2026-09-01T10:00:00+00:00")],
    )
    result = pruner.plan(tmp_path, archive_before="2026-07")
    assert result["archive"] == ["old00001.jsonl"]  # the straddling one stays live
    pruner.apply_plan(tmp_path, result, backup=False)
    assert (tmp_path / "archive" / "old00001.jsonl").is_file()
    assert (tmp_path / "straddle.jsonl").is_file()


def test_nothing_is_archived_without_a_cutoff(pruner, tmp_path) -> None:
    _session(tmp_path, "old00001", [ANSWERED])
    assert pruner.plan(tmp_path)["archive"] == []


def test_archived_sessions_leave_the_cockpit_but_stay_readable(
    pruner, tmp_path
) -> None:
    # The picker globs the patient dir top level, so archive/ is out of the UI —
    # that is the point — but the file itself must be intact.
    from my20q.recording import Recorder

    patient = tmp_path / "Paula"
    patient.mkdir()
    _session(patient, "old00001", [ANSWERED])
    _session(patient, "new00002", [dict(ANSWERED, recorded_at="2026-09-01T10:00:00+00:00")])
    pruner.apply_plan(patient, pruner.plan(patient, "2026-07"), backup=False)

    recorder = Recorder(tmp_path, "Paula")
    listed = {s["session_id"] for s in recorder.list_sessions()}
    assert listed == {"new00002"}
    archived = (patient / "archive" / "old00001.jsonl").read_text(encoding="utf-8")
    assert json.loads(archived.splitlines()[0])["outcome"] == "synthesized"


def test_archiving_drops_the_reported_dataset_size(pruner, tmp_path) -> None:
    # dataset_bytes() globs the top level only, so the size warning falls when
    # sessions are archived even though the bytes are still on disk.
    from my20q.recording import Recorder

    patient = tmp_path / "Paula"
    patient.mkdir()
    _session(patient, "old00001", [ANSWERED])
    recorder = Recorder(tmp_path, "Paula")
    before = recorder.dataset_bytes()
    pruner.apply_plan(patient, pruner.plan(patient, "2026-07"), backup=False)
    assert before > 0 and recorder.dataset_bytes() == 0


# ------------------------------------------------------------- the CLI


def test_cli_dry_run_reports_without_writing(pruner, tmp_path, capsys) -> None:
    _session(tmp_path, "aaaa1111", [_round()])
    assert pruner.main([str(tmp_path)]) == 0
    assert "dry run" in capsys.readouterr().out
    assert (tmp_path / "aaaa1111.jsonl").exists()


def test_cli_rejects_a_malformed_cutoff(pruner, tmp_path) -> None:
    with pytest.raises(SystemExit):
        pruner.main([str(tmp_path), "--archive-before", "June"])


def test_cli_on_an_empty_directory_is_a_no_op(pruner, tmp_path, capsys) -> None:
    assert pruner.main([str(tmp_path)]) == 0
    assert "nothing to do" in capsys.readouterr().out
