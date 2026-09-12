"""W2-X(a) changed the explore decay's ARGUMENT. By how much, on real boards?

main:           exploratory = directive not in (split, pin)
                              and rng < decay ** (yeses_this_round + 1)
cockpit-shell:  exploratory = directive == "probe"
                              and rng < decay ** (slot_mass/ready + 1)

The round-level counter grows monotonically, so exploration decays as a round
goes on. The per-slot ratio is 0 for an EMPTY slot no matter how long the round
has run — so late-round probing of an empty slot explores at the opening rate.

Trial 2 spent its back half probing `why`, which cannot be filled for an
injury. This prices that difference.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DECAY = 2 / 3
READY = 2.0

rec = [
    json.loads(line)
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    if line.strip()
][-1]

queries = [q for q in rec["queries"] if q.get("kind") == "query"]
yeses = len([q for q in queries if q.get("answer") == "yes"])
board = {c: {v: float(s) for v, s in vals} for c, vals in rec["board"]["final"].items()}

print(f"round: {len(queries)} questions, {yeses} of them answered yes")
print()
print(f"{'slot':<8}{'mass':>6}{'depth':>8}{'P(explore) NEW':>16}{'P(explore) OLD':>16}{'ratio':>9}")
print("-" * 63)
old_p = DECAY ** (yeses + 1)
for cat, vals in board.items():
    mass = max((s for s in vals.values()), default=0.0)
    depth = max(0.0, mass) / READY
    new_p = DECAY ** (depth + 1)
    ratio = new_p / old_p if old_p else float("inf")
    print(f"{cat:<8}{mass:>6.1f}{depth:>8.2f}{new_p:>16.4f}{old_p:>16.4f}{ratio:>8.0f}x")

print()
print("`why` and `how` are the slots trial 2 burned its back half on.")
print("OLD: exploration had decayed to near zero by then. NEW: it is at the")
print("opening rate, because those slots are empty and the ratio does not")
print("know the round is 38 questions old.")
