"""Conversation export — the caregiver 'save conversation' feature.

The export mirrors the recording dataset (`recorder.py`): both are
training-data artifacts, so they share one record schema
(`recorder.build_round_record`). The default export is JSONL — byte-for-
byte the same shape a recording file holds, one round per line — so
exported and recorded data form one uniform corpus. A Markdown view is
also offered for human reading, rendered from the same records.

Unlike the recording dataset (real-patient-only, written to git-ignored
local storage), the export works regardless of the recording gate, so it
is also useful in synthetic-persona dev trials. It carries only the
conversation — never the patient profile or knowledge graph — and is
caregiver-initiated onto the caregiver's own device.
"""

from __future__ import annotations

import json
from typing import Any

_ANSWER_LABEL = {
    "yes": "Yes",
    "no": "No",
    "kinda": "Kinda",
    "not_sure": "Not sure",
}
_OUTCOME_LABEL = {
    "synthesized": "Confirmed utterance",
    "abandoned": "Ended without a confirmed message",
    "emergency": "Emergency — short-circuited",
    None: "In progress",
}


def to_jsonl(records: list[dict[str, Any]]) -> str:
    """One record per line — identical to a recording dataset file."""
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


def to_markdown(session_id: str, records: list[dict[str, Any]]) -> str:
    """Human-readable transcript rendered from the recording records."""
    lines: list[str] = [
        f"# my20Q conversation — session `{session_id[:8]}`",
        "",
        f"_{len(records)} round(s)_",
        "",
    ]
    if not records:
        lines.append("_(no rounds in this session yet)_")
        return "\n".join(lines) + "\n"

    for i, rec in enumerate(records, start=1):
        label = rec.get("topic_id", "?")
        engine = rec.get("engine", "")
        lines.append(f"## Round {i} — {label} ({engine})")
        outcome = rec.get("outcome")
        lines.append(f"**Status:** {_OUTCOME_LABEL.get(outcome, outcome)}")
        if outcome == "synthesized" and rec.get("final_utterance"):
            lines.append("")
            lines.append(f"> **“{rec['final_utterance']}”**")
        lines.append("")

        step = 0
        for entry in rec.get("queries", []):
            kind = entry.get("kind")
            text = entry.get("text", "")
            if kind == "context":
                lines.append(f"  - _caregiver context:_ {text}")
            else:  # query / synthesis
                step += 1
                ans = entry.get("answer")
                tag = "Proposed" if kind == "synthesis" else "Q"
                suffix = f" — **{_ANSWER_LABEL.get(ans, ans)}**" if ans else ""
                lines.append(f"  {step}. {tag}: {text}{suffix}")
                rationale = entry.get("rationale")
                if rationale:
                    lines.append(f"     _reasoning: {rationale}_")

        emotion = {k: v for k, v in (rec.get("emotional_state") or {}).items() if v}
        if emotion:
            reading = ", ".join(f"{k} {v:+.2f}" for k, v in emotion.items())
            lines.append("")
            lines.append(f"  _emotional reading:_ {reading}")

        if "job_b" in rec:
            lines.append("")
            lines.append(f"  _Job-B: {rec['job_b']}_")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
