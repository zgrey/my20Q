"""Pretty-print a Paula recording JSONL for trial autopsies.

Usage: python scripts/dump_recording.py <file.jsonl> [...]

v2/banner-aware: renders direction labels, informative flags, verify-on-lock
double-checks, ⇄ flips, ✗-edits (bans/mutes), restart reasons — and closes
each round with the autopsy summary (answer mix, focus histogram, farming
yeses, verifies/flips/edits/restarts) that previously had to be hand-built.
"""

import json
import sys
from collections import Counter


def _summary(r: dict) -> None:
    entries = r.get("queries", [])
    queries = [q for q in entries if q.get("kind") == "query"]
    answers = Counter(q.get("answer") for q in queries)
    focus = Counter(q.get("focus") for q in queries if q.get("focus"))
    farming = sum(
        1
        for q in queries
        if q.get("answer") == "yes" and q.get("informative") is False
    )
    verifies = sum(1 for q in queries if q.get("verify"))
    flips = sum(1 for q in queries if q.get("flipped_from"))
    bans = [q["ban"] for q in entries if q.get("kind") == "edit" and q.get("ban")]
    mutes = [q["mute"] for q in entries if q.get("kind") == "edit" and q.get("mute")]
    diags = sum(1 for q in entries if q.get("kind") == "diagnostic")
    restarts = len((r.get("board") or {}).get("restarts", []))
    print("    " + "-" * 72)
    print(
        "    SUMMARY  answers: "
        + "  ".join(f"{a}={n}" for a, n in answers.most_common())
    )
    if focus:
        print(
            "             focus:   "
            + "  ".join(f"{c}={n}" for c, n in focus.most_common())
        )
    print(
        f"             farming-yeses={farming}  verifies={verifies}  "
        f"flips={flips}  diagnostics={diags}  restarts={restarts}"
    )
    if bans:
        print("             bans:    " + "; ".join(f"{b['category']}≠{b['value']}" for b in bans))
    if mutes:
        print("             mutes:   " + ", ".join(mutes))


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
            if board.get("restarts"):
                reasons = [x.get("reason", "?") for x in board["restarts"]]
                print(f"    restarts: {len(reasons)} ({', '.join(reasons)})")
            if board.get("final"):
                print("    final board:")
                for cat, pairs in board["final"].items():
                    ranked = sorted(pairs, key=lambda p: -p[1])[:6]
                    line_ = " | ".join(f"{v} {s:+.1f}" for v, s in ranked)
                    print(f"      {cat:6s}: {line_}")
            n = 0
            for q in r.get("queries", []):
                kind = q.get("kind", "?")
                ans = q.get("answer")
                txt = q.get("text", "")
                if kind == "query":
                    n += 1
                    tag = f"q{n:03d}"
                else:
                    tag = kind[:9]
                print(f"  [{tag:9s}] {txt!r}  -> {ans}")
                if q.get("preface"):
                    print(f"             preface: {q['preface']!r}")
                if q.get("rationale"):
                    print(f"             rationale: {q['rationale']!r}")
                if q.get("slots"):
                    extra = f"  focus={q['focus']}" if q.get("focus") else ""
                    print(f"             slots: {q['slots']}{extra}")
                elif q.get("focus"):
                    print(f"             slots: (none)  focus={q['focus']}")
                flags = []
                if q.get("direction"):
                    flags.append(f"dir={q['direction']}")
                if "informative" in q:
                    flags.append(
                        "informative" if q["informative"] else "FARMING-YES"
                    )
                if q.get("verify"):
                    flags.append("VERIFY")
                if q.get("flipped_from"):
                    flags.append(f"FLIPPED (was {q['flipped_from']!r})")
                if q.get("ban"):
                    flags.append(f"BAN {q['ban']['category']}≠{q['ban']['value']!r}")
                if q.get("mute"):
                    flags.append(f"MUTE {q['mute']}")
                if flags:
                    print(f"             {'  '.join(flags)}")
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
            _summary(r)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        dump(p)
