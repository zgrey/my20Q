"""Tests for the autopsy dumper — the record's only human-facing reader.

`scripts/dump_recording.py` is the tool every trial autopsy is written from,
and the W2-O instrumentation is only worth recording if it renders. These
tests also pin the back-compat claim: a recording written before W2-O carries
none of the new fields and must still dump.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dump_recording.py"


@pytest.fixture(scope="module")
def dumper():
    spec = importlib.util.spec_from_file_location("dump_recording", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, record: dict) -> str:
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return str(path)


def _legacy_record() -> dict:
    """A round as recorded before W2-O — none of the new fields present."""
    return {
        "session_id": "s", "round_id": "r1", "topic_id": "physical_health",
        "engine": "reasoning", "outcome": "abandoned", "final_utterance": "",
        "query_count": 1, "job_b": -2.0, "model": "gemma4:e4b",
        "queries": [
            {"kind": "query", "text": "Is it your right leg?", "answer": "no",
             "rationale": "drill", "slots": {"where": "right leg"},
             "focus": "where"},
        ],
        "emotional_state": {},
        "board": {"seeds": {"what": ["pain"]},
                  "restarts": [{"reason": "stalled", "board": {}}]},
        "recorded_at": "2026-09-01T02:00:00+00:00",
    }


def test_legacy_recordings_still_dump(tmp_path, dumper, capsys) -> None:
    dumper.dump(_write(tmp_path, _legacy_record()))
    out = capsys.readouterr().out
    assert "Is it your right leg?" in out
    assert "restarts: 1 (stalled)" in out  # no position on a pre-W2-O record
    assert "SUMMARY" in out


def test_dump_renders_the_instrumentation(tmp_path, dumper, capsys) -> None:
    record = _legacy_record()
    record["seed_context"] = "She was up all night."
    record["seed_ms"] = 4120.0
    record["pending_question"] = {
        "text": "Is it your right thigh?", "rationale": "drill", "focus": "where",
    }
    record["board"]["restarts"] = [
        {"reason": "stalled", "after_query": 7, "board": {}}
    ]
    record["queries"][0].update(
        banner={"ready": True, "text": "Pain in my right leg",
                "parts": [{"category": "what", "value": "pain",
                           "band": "locked"}]},
        timing={"deliberate_ms": 8412.0, "format_ms": 240.0,
                "total_ms": 8703.0, "llm_calls": 3, "attempts": 2},
        rejections=["you already asked that — ask about something different"],
    )
    dumper.dump(_write(tmp_path, record))
    out = capsys.readouterr().out
    assert "seed_context: 'She was up all night.'" in out
    assert "UNANSWERED at end: 'Is it your right thigh?'" in out
    assert "stalled@q007" in out
    assert "banner[READY]" in out
    assert "rejected: you already asked that" in out
    assert "total=8.7s" in out and "attempts=2" in out
    # The banner metric: where readiness landed, and what was asked after it.
    assert "board ready at q001 (0 asked after)" in out
    assert "gate-rejections=1 (on 1 question)" in out
