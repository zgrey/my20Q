"""Portable conversation export — the caregiver 'save conversation' feature.

Distinct from the recording dataset (`recorder.py`), which is the
real-patient-only training corpus written to git-ignored local storage.
This module builds a human-portable transcript of a session's rounds for
the caregiver to download — and it works regardless of the recording gate,
so it is also useful during synthetic-persona dev trials.

The export contains only the conversation (queries, answers, caregiver
context, the synthesized utterance, the emotional reading) — never the
patient profile or knowledge graph. It is caregiver-initiated and lands on
the caregiver's own device, consistent with the privacy model
(local, caregiver-controlled).
"""

from __future__ import annotations

import datetime as _dt
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


def session_payload(session_id: str, rounds: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the structured export payload for a session.

    `rounds` is an ordered list of dicts, each with: round_id, topic_id,
    topic_label, engine, outcome, final_utterance, history (the round's
    ordered query/synthesis/context entries), emotional_state, job_b.
    """
    return {
        "tool": "my20Q",
        "kind": "conversation-export",
        "session_id": session_id,
        "exported_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "round_count": len(rounds),
        "rounds": rounds,
    }


def to_json(payload: dict[str, Any]) -> str:
    """Pretty-printed JSON — the structured, machine-readable export."""
    return json.dumps(payload, indent=2, ensure_ascii=False)


def to_markdown(payload: dict[str, Any]) -> str:
    """Human-readable Markdown transcript — the default export format."""
    rounds = payload.get("rounds", [])
    lines: list[str] = [
        f"# my20Q conversation — session `{payload['session_id'][:8]}`",
        "",
        f"_Exported {payload['exported_at']} · "
        f"{payload['round_count']} round(s)_",
        "",
    ]
    if not rounds:
        lines.append("_(no rounds in this session yet)_")
        return "\n".join(lines) + "\n"

    for i, rnd in enumerate(rounds, start=1):
        label = rnd.get("topic_label") or rnd.get("topic_id", "?")
        engine = rnd.get("engine", "")
        lines.append(f"## Round {i} — {label} ({engine})")
        outcome = rnd.get("outcome")
        lines.append(f"**Status:** {_OUTCOME_LABEL.get(outcome, outcome)}")
        if outcome == "synthesized" and rnd.get("final_utterance"):
            lines.append("")
            lines.append(f"> **“{rnd['final_utterance']}”**")
        lines.append("")

        step = 0
        for entry in rnd.get("history", []):
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

        emotion = {k: v for k, v in (rnd.get("emotional_state") or {}).items() if v}
        if emotion:
            reading = ", ".join(f"{k} {v:+.2f}" for k, v in emotion.items())
            lines.append("")
            lines.append(f"  _emotional reading:_ {reading}")

        if "job_b" in rnd:
            lines.append("")
            lines.append(f"  _Job-B: {rnd['job_b']}_")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
