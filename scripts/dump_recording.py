"""Pretty-print a Paula recording JSONL for trial autopsies.

Usage: python scripts/dump_recording.py <file.jsonl> [...]

v2/banner-aware: renders direction labels, informative flags, verify-on-lock
double-checks, ⇄ flips, ✗-edits (bans/mutes), restart reasons — and closes
each round with the autopsy summary (answer mix, focus histogram, farming
yeses, verifies/flips/edits/restarts) that previously had to be hand-built.

v3 (W2-O): renders the instrumentation the record now carries — the round's
seed context and seeding cost, the per-query banner (with the query at which
the board first turned propose-ready, and how many were asked after it), the
gate rejections behind each re-ask, per-question latency, restart POSITIONS,
and the question left unanswered when a round was abandoned. Recordings
written before W2-O simply omit these fields and dump exactly as before.
"""

import contextlib
import json
import sys
from collections import Counter

# Recordings carry unicode (≠ / ⇄ / curly quotes); the legacy Windows console
# encodes cp1252 and would crash mid-dump — force UTF-8 on the streams.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")


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
    contested = sum(1 for q in queries if q.get("contested"))
    unresolved = sum(
        1 for c in (r.get("clarifications") or []) if c.get("outcome") == "unresolved"
    )
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
    if contested or unresolved:
        print(
            f"             contradictions={contested}  unresolved={unresolved}"
        )
    # The banner metric (W2-O): the query at which the board first turned
    # propose-ready, and how many were asked after that point. A round that
    # was answerable at q9 and ran to q18 spent half its questions past the
    # point where proposing WAS the best question.
    ready_at = next(
        (
            i
            for i, q in enumerate(queries, start=1)
            if (q.get("banner") or {}).get("ready")
        ),
        None,
    )
    if ready_at is not None:
        print(
            f"             board ready at q{ready_at:03d} "
            f"({len(queries) - ready_at} asked after)"
        )
    # Gate pressure and latency — how hard the model had to be pushed, and
    # what it cost. Only present on W2-O-era records.
    rejected = sum(1 for q in queries if q.get("rejections"))
    timed = [q["timing"] for q in queries if q.get("timing")]
    if rejected or timed:
        bits = []
        if rejected:
            reasks = sum(len(q.get("rejections") or []) for q in queries)
            plural = "question" if rejected == 1 else "questions"
            bits.append(f"gate-rejections={reasks} (on {rejected} {plural})")
        if timed:
            total = sum(t.get("total_ms", 0.0) for t in timed)
            bits.append(f"mean {total / len(timed) / 1000:.1f}s/question")
            if r.get("seed_ms"):
                bits.append(f"seed {r['seed_ms'] / 1000:.1f}s")
        print("             " + "  ".join(bits))
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
            # (Pre-W2-O this probed r["job_b"]["seed_context"] — job_b is a
            # float, so it never once fired. The field is top-level now.)
            if r.get("seed_context"):
                print(f"    seed_context: {r['seed_context']!r}")
            if r.get("pending_question"):
                pq = r["pending_question"]
                extra = f"  focus={pq['focus']}" if pq.get("focus") else ""
                print(f"    UNANSWERED at end: {pq.get('text', '')!r}{extra}")
            # Every clarification the round opened, why, and how it closed. An
            # "unresolved" line is a fact about the DIALOGUE — the walk did not
            # settle anything — never a claim about the person.
            for c in r.get("clarifications") or []:
                trigger = c.get("reason", "?")
                print(
                    f"    CLARIFY [{trigger}] {c.get('category', '?')}="
                    f"{c.get('value', '')!r} -> {c.get('outcome', '?')}"
                    f" after {len(c.get('attempts') or [])} q"
                )
                if c.get("trigger_question") or c.get("verify_question"):
                    q = c.get("trigger_question") or c.get("verify_question")
                    print(f"      triggered by: {q!r}")
                for a in c.get("attempts") or []:
                    print(f"      · {a.get('text', '')!r} -> {a.get('answer')}")
            board = r.get("board") or {}
            if board.get("seeds"):
                for cat, vals in board["seeds"].items():
                    if vals:
                        print(f"    seed {cat:6s}: {', '.join(vals)}")
            if board.get("restarts"):
                # `after_query` is the position (W2-O); older records lack it.
                reasons = [
                    x.get("reason", "?")
                    + (
                        f"@q{x['after_query']:03d}"
                        if x.get("after_query") is not None
                        else ""
                    )
                    for x in board["restarts"]
                ]
                print(f"    restarts: {len(reasons)} ({', '.join(reasons)})")
            if board.get("edges"):
                for cat, links in board["edges"].items():
                    pairs = ", ".join(f"{p} › {c}" for c, p in links.items())
                    print(f"    edges {cat:6s}: {pairs}")
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
                    flags.append("CONTESTED" if q.get("contested") else "VERIFY")
                if q.get("clarify"):
                    flags.append("CLARIFY")
                if q.get("flipped_from"):
                    flags.append(f"FLIPPED (was {q['flipped_from']!r})")
                if q.get("ban"):
                    flags.append(f"BAN {q['ban']['category']}≠{q['ban']['value']!r}")
                if q.get("mint"):
                    flags.append(
                        f"MINT {q['mint']['category']}={q['mint']['value']!r}"
                    )
                if q.get("mute"):
                    flags.append(f"MUTE {q['mute']}")
                if q.get("focus_requested"):
                    # W2-P: the controller asked for one slot, the question
                    # asserted another. Recorded so the divergence stays
                    # visible rather than erased by the fix for it.
                    flags.append(f"ASKED-FOR {q['focus_requested']}")
                if q.get("drill_parent"):
                    # W2-R: the draft value the controller was narrowing. A yes
                    # links the asserted value under it, so the ladder a round
                    # walked is readable even when the model tagged no refines.
                    flags.append(f"DRILLING {q['drill_parent']!r}")
                if flags:
                    print(f"             {'  '.join(flags)}")
                # W2-O instrumentation.
                banner = q.get("banner")
                # An empty banner (nothing woven yet) is the normal early-round
                # state — recorded, but not worth a line per query.
                if banner and banner.get("parts"):
                    mark = "READY" if banner.get("ready") else "draft"
                    parts = " + ".join(
                        f"{p['value']}({p['band'][:4]})"
                        for p in banner["parts"]
                    )
                    print(f"             banner[{mark}]: {banner.get('text', '')!r}")
                    print(f"             weave:   {parts}")
                for why in q.get("rejections") or []:
                    print(f"             rejected: {why}")
                t = q.get("timing")
                if t:
                    phases = "  ".join(
                        f"{k[:-3]}={v / 1000:.1f}s"
                        for k, v in t.items()
                        if k.endswith("_ms") and k != "total_ms"
                    )
                    print(
                        f"             timing:  total={t.get('total_ms', 0) / 1000:.1f}s"
                        f"  {phases}  calls={t.get('llm_calls')}"
                        f"  attempts={t.get('attempts')}"
                    )
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
