"""Confirmed-("yes")-answer log.

The most durable signal in a session is what the caregiver actually CONFIRMED.
Unlike the full round recording, this keeps ONLY the yes-answers — the question
and the slot value(s) it confirmed.

The engine WRITES this log but no longer reads it: restart recovery rebuilds
its yes-context from the round's own history, which keeps recovered signal
ROUND-specific by design (a restart must never import yeses from another
round). The log exists for the recorded dataset and the future caregiver
interview tooling.

Privacy: like the recording, the on-disk file is written only for a real patient,
local-only, under the git-ignored data dir. The in-memory list always works;
only the file write is gated — the caller passes ``path`` for a real patient
and leaves it ``None`` otherwise.

This is deliberately WITHIN-session: it is not auto-loaded into future sessions,
preserving the two-layer rule (the volatile current-need layer never persists
across sessions). See docs/design/beta-retool.md §9 and CLAUDE.md.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class YesMemory:
    """A session's confirmed-yes answers — in memory, plus an optional dated file."""

    #: When set (real patient), each yes is also appended here as one JSON line.
    path: Path | None = None
    items: list[dict] = field(default_factory=list)

    def add(
        self, *, question: str, needs: list[str], topic_id: str, round_id: str
    ) -> None:
        """Record one confirmed-yes answer (in memory + the dated file if any)."""
        rec = {
            "question": question,
            "needs": [n for n in needs if n],
            "topic_id": topic_id,
            "round_id": round_id,
            "at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        }
        self.items.append(rec)
        self._append_file(rec)

    def needs(self) -> list[str]:
        """De-duplicated confirmed needs, most-recent first."""
        seen: set[str] = set()
        out: list[str] = []
        for rec in reversed(self.items):
            for need in rec.get("needs", []):
                key = need.casefold()
                if need and key not in seen:
                    seen.add(key)
                    out.append(need)
        return out

    def questions(self) -> list[str]:
        """The confirmed-yes question texts, most-recent first."""
        seen: set[str] = set()
        out: list[str] = []
        for rec in reversed(self.items):
            q = rec.get("question", "")
            key = q.casefold()
            if q and key not in seen:
                seen.add(key)
                out.append(q)
        return out

    def _append_file(self, rec: dict) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


def dated_path(
    data_dir: Path, patient_id: str, *, today: _dt.date | None = None
) -> Path:
    """``<data_dir>/<patient_id>/memory/yes_memory_<YYYY-MM-DD>.jsonl`` — the file.

    Lives in a ``memory/`` subdir, NOT directly under the patient dir, so it never
    collides with the session recordings the Recorder globs there (``*.jsonl``).
    """
    day = (today or _dt.datetime.now().date()).isoformat()
    return Path(data_dir) / patient_id / "memory" / f"yes_memory_{day}.jsonl"
