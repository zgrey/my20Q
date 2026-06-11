"""Pretty-print a Paula recording JSONL for trial autopsies.

Usage: python scripts/dump_recording.py <file.jsonl> [...]
"""

import json
import sys


def dump(path: str) -> None:
    print("=" * 110)
    print("FILE:", path)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            print(
                f"\n### ROUND {r['round_id']}  topic={r['topic_id']}  "
                f"model={r.get('model')}  engine={r.get('engine')}  "
                f"outcome={r.get('outcome')}  queries={r.get('query_count')}"
            )
            if r.get("final_utterance"):
                print(f"    FINAL: {r['final_utterance']!r}")
            jb = r.get("job_b")
            if isinstance(jb, dict) and jb.get("seed_context"):
                print(f"    seed_context: {jb['seed_context']!r}")
            board = r.get("board") or {}
            if board.get("seeds"):
                for cat, vals in board["seeds"].items():
                    if vals:
                        print(f"    seed {cat:6s}: {', '.join(vals)}")
            if board.get("final"):
                print("    final board:")
                for cat, pairs in board["final"].items():
                    ranked = sorted(pairs, key=lambda p: -p[1])[:6]
                    line = " | ".join(f"{v} {s:+.1f}" for v, s in ranked)
                    print(f"      {cat:6s}: {line}")
            for q in r.get("queries", []):
                kind = q.get("kind", "?")
                ans = q.get("answer")
                txt = q.get("text", "")
                print(f"  [{kind:9s}] {txt!r}  -> {ans}")
                if q.get("preface"):
                    print(f"             preface: {q['preface']!r}")
                if q.get("rationale"):
                    print(f"             rationale: {q['rationale']!r}")
                if q.get("slots"):
                    extra = f"  focus={q['focus']}" if q.get("focus") else ""
                    print(f"             slots: {q['slots']}{extra}")
                elif q.get("focus"):
                    print(f"             slots: (none){'  focus=' + q['focus']}")
                # legacy fields (pre-2026-06-10 recordings)
                if q.get("yes_ids"):
                    print(f"             yes_ids: {q['yes_ids']}")
                if q.get("new_need"):
                    print(f"             new_need: {q['new_need']!r}")
                if q.get("seeds"):
                    print(f"             seeds: {[s.get('need') for s in q['seeds']]}")
                if q.get("hypotheses"):
                    top = [
                        f"{h.get('need')}={h.get('weight')}"
                        for h in q["hypotheses"][:6]
                    ]
                    print(f"             belief: {top}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        dump(p)
