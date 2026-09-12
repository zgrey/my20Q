"""Does keeping `kinda` across a restart turn the 09-12 drill into a split?

Keeping the kinda is a W3-K win on its own terms, but it CHANGES THE BOARD, and
the board picks the directive. At the 09-12 stall the round had arm=1.0 (yes)
and hands=kinda. If the kinda now scores 0.5, arm and hand are within the split
margin and both positive -> tied_top fires -> `split`, not `drill`. Both of
those values had already been asked, so a split would hit the repeat gate and we
would have swapped one deadlock for another.

Check it rather than assume it.
"""
from __future__ import annotations

from pathlib import Path

from my20q.agent import facets
from my20q.agent.dialogue import Round
from my20q.topics import load_topics

topics = load_topics(Path("src/my20q/topics/data/topics.yaml"))
topic = next(t for t in topics if t.id == "physical_health")
rnd = Round(topic)

seg = [{"kind": "query", "focus": "where", "answer": "yes"}]

# The 09-12 round asked about BOTH of them before the stall — which is exactly
# what makes a split unaskable and the drill the only useful move left.
rnd._history = [
    {"kind": "query", "text": "Is the pain located in your hands?",
     "focus": "where", "answer": "kinda", "slots": {"where": "hand"}},
    {"kind": "query", "text": "Is the pain in your arm?",
     "focus": "where", "answer": "yes", "slots": {"where": "arm"}},
]

for label, where in [
    ("BEFORE W3-K (kinda dumped)", {"arm": 1.0, "hand": 0.0, "leg": 0.0, "foot": 0.0}),
    ("AFTER  W3-K (kinda kept)  ", {"arm": 1.0, "hand": 0.5, "leg": 0.0, "foot": 0.0}),
]:
    board = facets.empty_board()
    board["what"] = {"pain": 3.0}
    board["when"] = {"now": 2.0}
    board["where"] = dict(where)
    tie = facets.tied_top(board, "where", margin=rnd.tuning.facet_split_margin)
    cat, directive, pair = rnd._pick_focus(board, seg)
    print(f"{label}  tied={bool(tie)}  -> focus={cat!r} directive={directive!r} pair={pair!r}")
