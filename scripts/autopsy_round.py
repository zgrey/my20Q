"""Trial 2 autopsy: how much of the churn was the _ruled_out bug?

The round ran entirely on the buggy version (the fix landed mid-round and the
server was never restarted). Two questions, both answerable from the record:

1. How many YES-SUPPORTED values did the old rule eliminate? Each one is a
   belief the round had to rediscover, which is what the `what`-slot churn
   (discomfort -> pain -> tingling -> pins and needles -> dull ache -> general
   tiredness -> burning sensation) looks like from the inside.
2. What does _pick_focus want on the FINAL board? `where: arms = 4.0` was
   confirmed at q9 and never drilled to "wrist" across 29 further questions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from my20q.agent import facets
from my20q.agent.dialogue import Round
from my20q.topics import load_topics

rec = [
    json.loads(line)
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    if line.strip()
][-1]

topics = load_topics(Path("src/my20q/topics/data/topics.yaml"))
topic = next(t for t in topics if t.id == rec["topic_id"])
rnd = Round(topic)
rnd._history = rec["queries"]


def ruled_out_old(history: list[dict]) -> dict[str, set[str]]:
    """The version that ran during the trial."""
    out: dict[str, set[str]] = {}
    for h in history:
        if h.get("kind") != "query" or h.get("answer") != "no":
            continue
        if h.get("contested"):
            continue
        focus = h.get("focus") or ""
        value = (h.get("slots") or {}).get(focus)
        if focus and value:
            out.setdefault(focus, set()).add(value)
    return out


yes_values: dict[str, set[str]] = {}
for h in rec["queries"]:
    if h.get("kind") == "query" and h.get("answer") == "yes":
        for cat, val in (h.get("slots") or {}).items():
            yes_values.setdefault(cat, set()).add(val)

old = ruled_out_old(rec["queries"])
new = rnd._ruled_out()

print("=== 1. what the two rules eliminate ===")
print(f"  OLD (ran in the trial): {old}")
print(f"  NEW (the fix)         : {new}")
print()
damage = {
    cat: sorted(vals & yes_values.get(cat, set())) for cat, vals in old.items()
}
damage = {c: v for c, v in damage.items() if v}
print(f"  YES-supported values the OLD rule destroyed: {damage}")
new_damage = {
    c: sorted(v & yes_values.get(c, set()))
    for c, v in new.items()
    if v & yes_values.get(c, set())
}
print(f"  ...and under the NEW rule: {new_damage or 'none'}")
print()

print("=== 2. what the policy wants on the final board ===")
board = facets.empty_board()
for cat, vals in rec["board"]["final"].items():
    board[cat] = {v: float(s) for v, s in vals}
for cat in facets.CATEGORIES:
    fams = facets.families(board, cat, rnd._edges)
    mass = fams[0][1] if fams else 0.0
    print(
        f"  {cat:<6} leader={str(facets.leader(board, cat)):<26} mass={mass:<5} "
        f"retired={rnd._retired(board, cat)}"
    )
cat, directive, pair = rnd._pick_focus(board, [])
print(f"  -> focus={cat!r} directive={directive!r} pair={pair!r}")
print()
print("=== 3. cost of the churn ===")
qs = [q for q in rec["queries"] if q.get("kind") == "query"]
diags = [q for q in rec["queries"] if q.get("kind") == "diagnostic"]
clr = [q for q in qs if q.get("clarify")]
chk = [q for q in qs if q.get("verify")]
splits = sum((q.get("timing") or {}).get("split_either_or", 0) for q in qs)
print(f"  questions={len(qs)}  diagnostics={len(diags)}  clarify={len(clr)}  checks={len(chk)}")
print(f"  either/or questions rescued by the split: {splits}")
print(f"  restarts={len(rec['board']['restarts'])}")
