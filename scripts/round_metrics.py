"""Aggregate per-round metrics across every recorded session, by date.

Built to answer one question with data instead of impressions: is the engine
getting better or worse, and when did each change in trajectory happen?

Reads the recording schema (`build_round_record`), so it works on the patient
dataset and the synthetic dev captures alike. **It prints COUNTS ONLY — never a
question, an answer, or an utterance** — so its output is safe to paste into a
review that patient content must never reach.

    python scripts/round_metrics.py patient_data dev_recordings
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median


def rounds(paths: list[str]):
    """Every recorded round, de-duplicated by round_id.

    `watch/` holds repeated full-session snapshots taken during a live trial —
    the same round appears in dozens of them. Skipping that directory AND
    de-duplicating by id keeps one round counted once, however it was captured.
    """
    seen: set[str] = set()
    for root in paths:
        for f in sorted(Path(root).rglob("*.jsonl")):
            if f.name.startswith("yes_memory") or "watch" in f.parts:
                continue  # a different schema / repeated snapshots of one round
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "queries" not in rec:
                    continue
                rid = rec.get("round_id") or ""
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                yield f, rec


def day(rec: dict, f: Path) -> str:
    stamp = rec.get("recorded_at") or ""
    return stamp[:10] if len(stamp) >= 10 else f.stat().st_mtime.__str__()[:10]


def main(paths: list[str]) -> None:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for f, rec in rounds(paths):
        by_day[day(rec, f)].append(rec)

    print(
        f"{'date':<12}{'real':>5}{'acc':>5}{'aband':>6}"
        f"{'med q':>7}{'diag':>6}{'restart':>8}{'chk':>5}{'clar':>6}{'jobB':>7}"
    )
    print("-" * 67)
    for d in sorted(by_day):
        # A 0-query round is a topic switch, not an attempt — it drags the
        # median to 0 and says nothing about the engine.
        rs = [
            r
            for r in by_day[d]
            if any(q.get("kind") == "query" for q in r["queries"])
        ]
        if not rs:
            continue
        qs = [len([q for q in r["queries"] if q.get("kind") == "query"]) for r in rs]
        diag = sum(
            len([q for q in r["queries"] if q.get("kind") == "diagnostic"]) for r in rs
        )
        restarts = sum(len((r.get("board") or {}).get("restarts") or []) for r in rs)
        chk = sum(len([q for q in r["queries"] if q.get("verify")]) for r in rs)
        clar = sum(len(r.get("clarifications") or []) for r in rs)
        acc = len([r for r in rs if r.get("outcome") == "synthesized"])
        aband = len([r for r in rs if r.get("outcome") == "abandoned"])
        jb = [r.get("job_b", 0.0) for r in rs]
        print(
            f"{d:<12}{len(rs):>5}{acc:>5}{aband:>6}"
            f"{median(qs) if qs else 0:>7.0f}{diag:>6}{restarts:>8}"
            f"{chk:>5}{clar:>6}{(sum(jb) / len(jb) if jb else 0):>7.2f}"
        )

    print()
    print("acc = rounds the caregiver ACCEPTED (the only real success measure).")
    print("med q = median questions per round.  diag/restart/chk/clar = totals.")


if __name__ == "__main__":
    main(sys.argv[1:] or ["patient_data", "dev_recordings"])
