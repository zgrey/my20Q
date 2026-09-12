"""Replay _pick_focus against the board the 09-12 trial actually ended on.

The plan rests on WHICH directive fired at the stall, and the round record does
not carry the directive. So rebuild the board from the recorded snapshot and ask
the policy directly, rather than inferring it from the question text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from my20q.agent import facets
from my20q.agent.dialogue import Round
from my20q.topics import load_topics

snap = Path(sys.argv[1])
lines = snap.read_text(encoding="utf-8").splitlines()
records = [json.loads(line) for line in lines if line.strip()]
rec = records[-1]

board: facets.Board = facets.empty_board()
for cat, vals in rec["board"]["final"].items():
    board[cat] = {v: float(s) for v, s in vals}

topics = load_topics(Path("src/my20q/topics/data/topics.yaml"))
topic = next(t for t in topics if t.id == rec["topic_id"])
rnd = Round(topic)

# `seg` is the post-restart segment the rotation guard reads. Rebuild it from
# the recorded queries so the freshness check sees what the round saw.
seg = [
    {"kind": "query", "focus": q.get("focus"), "answer": q.get("answer")}
    for q in rec["queries"]
    if q.get("kind") == "query"
]

print(f"topic       : {topic.id}   core={rnd._core_facets()}")
print(
    f"ready_points: {rnd.tuning.facet_ready_points}   "
    f"split_margin={rnd.tuning.facet_split_margin}"
)
print()
for cat in facets.CATEGORIES:
    fams = facets.families(board, cat, rnd._edges)
    mass = fams[0][1] if fams else 0.0
    lead = facets.leader(board, cat)
    tie = facets.tied_top(board, cat, margin=rnd.tuning.facet_split_margin)
    print(
        f"{cat:<6} leader={str(lead):<22} family_mass={mass:<5} "
        f"tied={'yes' if tie else 'no':<4} retired={rnd._retired(board, cat)}"
    )

print()
cat, directive, pair = rnd._pick_focus(board, seg)
print(f"==> _pick_focus  ->  focus={cat!r}  directive={directive!r}  pair={pair!r}")

if directive == "drill":
    lead = facets.leader(board, cat)
    print(f"    drill target (the value to narrow BELOW): {lead}")
    print(f"    live values in {cat}: {facets.live(board, cat)}")
