"""Three-tier interaction-trace recorder — the patient training dataset.

In training mode with a real patient, every finalized round is appended
as one JSON line to a per-session file under the patient's data
directory: the file is the *session*, each line is a *round*, and the
round carries its *queries* — the three tiers of
docs/design/beta-retool.md §8.

The dataset is the most sensitive artifact in the system: it is written
only for a real patient (the privacy invariant), local-only, under a
git-ignored directory. It grows with use, so the cockpit surfaces its
size against a configurable threshold; periodic compaction is a flagged
follow-up (see the design doc §8).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

# Job-B answer weights — docs/design/beta-retool.md §9.1.
_ANSWER_WEIGHTS = {"yes": 2.0, "kinda": 1.0, "no": -2.0, "not_sure": 0.0}
_W_FINAL = 10.0


def job_b_score(history: list[dict], outcome: str | None) -> float:
    """The Job-B trial metric: ``W_final·confirmed + mean(query answer weights)``.

    A coarse round-quality scalar for comparing prompt variants during
    trials — not a graph-weighting signal. See the design doc §9.
    """
    answers = [
        h["answer"] for h in history if h["kind"] == "query" and h.get("answer")
    ]
    answer_term = (
        sum(_ANSWER_WEIGHTS.get(a, 0.0) for a in answers) / len(answers)
        if answers
        else 0.0
    )
    confirmed = 1.0 if outcome == "synthesized" else 0.0
    return round(_W_FINAL * confirmed + answer_term, 3)


def build_round_record(
    *,
    session_id: str,
    round_id: str,
    topic_id: str,
    engine: str,
    history: list[dict],
    outcome: str | None,
    final_utterance: str,
    model: str,
    emotional_state: dict | None = None,
    board: dict | None = None,
) -> dict:
    """The canonical per-round training record.

    Shared by the recorder (which appends it as JSONL to the patient
    dataset) and the caregiver conversation export (which serves the same
    shape) — so recorded data and exported data are one uniform corpus.
    See docs/design/beta-retool.md §8.
    """
    # Internal belief-control markers (a restart, or a legacy reseed) are not
    # conversation — drop them from the human-facing / training record.
    # Diagnostic entries ARE kept: a failure the caregiver saw is part of the
    # round's honest trace (and what trial autopsies need most).
    history = [h for h in history if h.get("kind") not in ("reseed", "restart")]
    return {
        "session_id": session_id,
        "round_id": round_id,
        "topic_id": topic_id,
        "engine": engine,
        "outcome": outcome,
        "final_utterance": final_utterance,
        "query_count": sum(1 for h in history if h["kind"] == "query"),
        "job_b": job_b_score(history, outcome),
        "queries": history,
        "emotional_state": emotional_state or {},
        # Board evolution (seeds / final / restart snapshots) — so trial
        # autopsies can read what the controller believed, not re-derive it.
        "board": board or {},
        "model": model,
        "recorded_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
    }


class Recorder:
    """Appends finalized rounds to one patient's on-disk dataset."""

    def __init__(self, data_dir: Path, patient_id: str) -> None:
        self.patient_dir = Path(data_dir) / patient_id

    def record_round(
        self,
        *,
        session_id: str,
        round_id: str,
        topic_id: str,
        engine: str,
        history: list[dict],
        outcome: str | None,
        final_utterance: str,
        model: str,
        emotional_state: dict | None = None,
        board: dict | None = None,
    ) -> None:
        """Append one finalized round to its session's JSONL file."""
        self.patient_dir.mkdir(parents=True, exist_ok=True)
        record = build_round_record(
            session_id=session_id,
            round_id=round_id,
            topic_id=topic_id,
            engine=engine,
            history=history,
            outcome=outcome,
            final_utterance=final_utterance,
            model=model,
            emotional_state=emotional_state,
            board=board,
        )
        path = self.patient_dir / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def list_sessions(self) -> list[dict]:
        """Recorded sessions for this patient — newest first.

        One entry per ``*.jsonl`` file (a session), with its round count
        and size, for the session-review dashboard's picker.
        """
        if not self.patient_dir.is_dir():
            return []
        out: list[dict] = []
        for f in self.patient_dir.glob("*.jsonl"):
            stat = f.stat()
            with f.open("r", encoding="utf-8") as fh:
                rounds = sum(1 for line in fh if line.strip())
            out.append(
                {
                    "session_id": f.stem,
                    "rounds": rounds,
                    "bytes": stat.st_size,
                    "modified": _dt.datetime.fromtimestamp(
                        stat.st_mtime, _dt.UTC
                    ).isoformat(timespec="seconds"),
                }
            )
        out.sort(key=lambda e: e["modified"], reverse=True)
        return out

    def read_session(self, session_id: str) -> list[dict]:
        """Parsed round records for one recorded session.

        `session_id` is validated to be a bare filename within this
        patient's directory — no path traversal.
        """
        if "/" in session_id or "\\" in session_id or ".." in session_id:
            raise FileNotFoundError(session_id)
        path = self.patient_dir / f"{session_id}.jsonl"
        if path.parent.resolve() != self.patient_dir.resolve() or not path.is_file():
            raise FileNotFoundError(session_id)
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def dataset_bytes(self) -> int:
        """Total size of this patient's recorded dataset, in bytes."""
        if not self.patient_dir.is_dir():
            return 0
        return sum(f.stat().st_size for f in self.patient_dir.glob("*.jsonl"))

    def rounds_recorded(self) -> int:
        """Total rounds recorded for this patient (one per JSONL line)."""
        if not self.patient_dir.is_dir():
            return 0
        total = 0
        for f in self.patient_dir.glob("*.jsonl"):
            with f.open("r", encoding="utf-8") as fh:
                total += sum(1 for line in fh if line.strip())
        return total
