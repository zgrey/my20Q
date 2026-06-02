"""Belief over candidate needs — the reasoning controller's state.

A reasoning round seeds a small set of concrete candidate needs (``Hypothesis``)
and then, each turn, asks the most-discriminating yes/no question. This module
is the **pure control logic**: weight updates from answers, pruning, leader
selection, and the synthesis trigger. No LLM, no I/O — fully deterministic and
unit-tested. The round engine (``agent/dialogue.py``) owns the seed set and
recomputes weights from history via :func:`recompute`, so undo is just
pop-and-recompute. See docs/design/beta-retool.md §7.
"""

from __future__ import annotations

from dataclasses import dataclass

# Multiplicative likelihood factors for a belief update. A "yes" makes the
# hypotheses the question pointed at ~9x more likely relative to the rest;
# "kinda" ("you're warm") is a softer nudge.
_P_YES = 0.9
_P_KINDA = 0.7
#: Below this normalized weight a hypothesis is treated as eliminated.
PRUNE_EPS = 0.02
#: Synthesize once the leader passes this share of the belief.
SYNTH_THRESHOLD = 0.65
#: A discriminating question's weighted yes-fraction should sit in this band;
#: outside it the question carries little information (nearly everyone, or
#: nearly no one, answers "yes").
BALANCE_LO = 0.15
BALANCE_HI = 0.85


@dataclass(frozen=True)
class Hypothesis:
    """One candidate need the person might be trying to express."""

    id: str
    need: str  # first-person, concrete, sanitized


def seed_weights(hyps: list[Hypothesis]) -> dict[str, float]:
    """Uniform prior over the seed set."""
    if not hyps:
        return {}
    w = 1.0 / len(hyps)
    return {h.id: w for h in hyps}


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:  # degenerate — everything eliminated; fall back to uniform
        n = len(weights)
        return {k: 1.0 / n for k in weights} if n else {}
    return {k: v / total for k, v in weights.items()}


def update_weights(
    weights: dict[str, float], yes_ids: set[str], answer: str
) -> dict[str, float]:
    """Return new normalized weights after folding in one answer.

    ``answer`` is the Answer value: "yes" / "no" / "kinda" / "not_sure".
    "not_sure" carries no information and leaves the belief unchanged.
    """
    if answer == "not_sure":
        return _normalize(dict(weights))
    p = _P_KINDA if answer == "kinda" else _P_YES
    favor_yes = answer != "no"  # "no" flips which side the evidence favors
    out: dict[str, float] = {}
    for hid, w in weights.items():
        # Likelihood this hypothesis produced the observed answer.
        like = p if ((hid in yes_ids) == favor_yes) else (1.0 - p)
        out[hid] = w * like
    return _normalize(out)


def recompute(
    hyps: list[Hypothesis], answered: list[tuple[set[str], str]]
) -> dict[str, float]:
    """Replay ``(yes_ids, answer)`` pairs over the seed prior.

    This is how undo works: the engine pops a history entry and recomputes the
    belief from scratch rather than keeping a snapshot stack.
    """
    weights = seed_weights(hyps)
    for yes_ids, answer in answered:
        weights = update_weights(weights, yes_ids, answer)
    return weights


def live_ids(weights: dict[str, float]) -> list[str]:
    """Ids still in play (weight at or above the prune floor)."""
    return [hid for hid, w in weights.items() if w >= PRUNE_EPS]


def leader(weights: dict[str, float]) -> tuple[str, float] | None:
    """The most-likely hypothesis id and its weight, or None when empty."""
    if not weights:
        return None
    hid = max(weights, key=lambda k: weights[k])
    return hid, weights[hid]


def should_synthesize(weights: dict[str, float]) -> bool:
    """True once the belief has concentrated enough to propose an utterance."""
    if len(live_ids(weights)) <= 1:
        return True
    top = leader(weights)
    return top is not None and top[1] >= SYNTH_THRESHOLD


def split_balance(weights: dict[str, float], yes_ids: set[str]) -> float:
    """Weighted fraction of the *live* belief that would answer "yes".

    Near 0.5 is a maximally informative split; near 0 or 1 is uninformative.
    """
    live = {hid: w for hid, w in weights.items() if w >= PRUNE_EPS}
    total = sum(live.values())
    if total <= 0:
        return 0.0
    return sum(w for hid, w in live.items() if hid in yes_ids) / total


def is_balanced(weights: dict[str, float], yes_ids: set[str]) -> bool:
    """Whether a candidate question splits the live belief informatively."""
    return BALANCE_LO <= split_balance(weights, yes_ids) <= BALANCE_HI


def ranked(
    hyps: list[Hypothesis], weights: dict[str, float], *, live_only: bool = True
) -> list[tuple[Hypothesis, float]]:
    """Hypotheses paired with weight, highest first (live ones by default)."""
    by_id = {h.id: h for h in hyps}
    items = [
        (by_id[hid], w)
        for hid, w in weights.items()
        if hid in by_id and (not live_only or w >= PRUNE_EPS)
    ]
    return sorted(items, key=lambda t: t[1], reverse=True)
