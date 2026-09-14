"""Concatenate bench result files for the same revision into one.

`bench_reasoning.py --compare` takes one file per side, but a 9-scenario run
does not fit inside a single background-task window, so each revision is
benched in batches. This merges a revision's batches back into one file with
every round, so the comparison is over the full scenario set rather than two
partial ones.

Only rounds are merged. The per-model header (model, availability, noise) is
taken from the first file and asserted identical across the rest — merging
results from different models or noise levels would be meaningless.

    python scripts/merge_bench_json.py out.json in1.json in2.json [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    out_path, *in_paths = argv

    merged: list[dict] = []
    for p in in_paths:
        entries = json.loads(Path(p).read_text(encoding="utf-8"))
        for entry in entries:
            key = (entry["model"], entry.get("noise", 0.0))
            existing = next(
                (e for e in merged if (e["model"], e.get("noise", 0.0)) == key), None
            )
            if existing is None:
                merged.append(json.loads(json.dumps(entry)))  # deep copy
            else:
                existing["rounds"].extend(entry["rounds"])

    for entry in merged:
        names = [r["scenario"] for r in entry["rounds"]]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            print(f"ERROR: scenario run twice for {entry['model']}: {dupes}")
            return 1
        print(
            f"{entry['model']}  noise={entry.get('noise', 0.0)}  "
            f"{len(entry['rounds'])} rounds: {', '.join(sorted(names))}"
        )

    Path(out_path).write_text(json.dumps(merged), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
