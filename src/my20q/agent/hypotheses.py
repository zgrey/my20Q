"""Belief over candidate needs — the reasoning controller's state.

**Additive point scoring**, not a normalized probability. Each answer adds points
to the needs a question targeted:

- **yes**   → strong positive (the question was *correct* — that need gains points),
- **kinda** → softer "warm" signal (*nearly* correct),
- **no**    → subtracts from the **targeted** needs only — it NEVER promotes the
  others (no renormalization; scores do not sum to 1), so a leader can only rise
  by being *confirmed*, never by other needs being ruled out,
- **not_sure** → no information, no change.

This mirrors how the caregiver reasons: yes = on the right track, kinda = nearly
there, no = wrong direction. Leader = highest score. Pure and deterministic; the
round engine recomputes from history, so undo is pop-and-recompute.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Points a single answer adds to each need a question targeted.
YES_POINTS = 1.0
KINDA_POINTS = 0.5  # "warm" — nearly correct
NO_POINTS = 1.0  # subtracted from the TARGETED needs only (never promotes others)
#: Strong positive when a caregiver note implies or confirms a need.
CONTEXT_POINTS = 2.0
#: A need at or below this score is treated as rejected and drops out of play.
ELIMINATE_FLOOR = -2.0


@dataclass(frozen=True)
class Hypothesis:
    """One candidate need the person might be trying to express."""

    id: str
    need: str  # first-person, concrete, sanitized


def seed_scores(hyps: list[Hypothesis]) -> dict[str, float]:
    """Every seed starts at zero — points accumulate only from answers."""
    return {h.id: 0.0 for h in hyps}


def update_score(
    scores: dict[str, float], yes_ids: set[str], answer: str
) -> dict[str, float]:
    """Add points to the TARGETED needs only; return a new score map.

    No normalization. A "no" subtracts from the needs the question pointed at and
    leaves every other need untouched — down-voting never promotes the leader.
    "not_sure" carries no information.
    """
    delta = {"yes": YES_POINTS, "kinda": KINDA_POINTS, "no": -NO_POINTS}.get(answer)
    if delta is None:  # not_sure (or anything unrecognized)
        return dict(scores)
    out = dict(scores)
    for hid in yes_ids:
        if hid in out:
            out[hid] += delta
    return out


def apply_context(
    scores: dict[str, float], new_ids: list[str], boost_ids: list[str]
) -> dict[str, float]:
    """Fold a high-trust caregiver note in as a strong positive (additive).

    New needs the note implies, plus existing ``boost_ids`` it confirms, each gain
    ``CONTEXT_POINTS``. No suppression of the rest — points only accumulate.
    """
    out = dict(scores)
    for hid in (*new_ids, *boost_ids):
        out[hid] = out.get(hid, 0.0) + CONTEXT_POINTS
    return out


def recompute(
    hyps: list[Hypothesis], answered: list[tuple[set[str], str]]
) -> dict[str, float]:
    """Replay ``(yes_ids, answer)`` pairs over the zeroed seed scores."""
    scores = seed_scores(hyps)
    for yes_ids, answer in answered:
        scores = update_score(scores, yes_ids, answer)
    return scores


def live_ids(scores: dict[str, float]) -> list[str]:
    """Ids still in play — anything not driven to/below the eliminate floor."""
    return [hid for hid, s in scores.items() if s > ELIMINATE_FLOOR]


def leader(scores: dict[str, float]) -> tuple[str, float] | None:
    """The highest-scoring live need and its score, or None when none are live."""
    live = {hid: s for hid, s in scores.items() if s > ELIMINATE_FLOOR}
    if not live:
        return None
    hid = max(live, key=lambda k: live[k])
    return hid, live[hid]


def ranked(
    hyps: list[Hypothesis], scores: dict[str, float], *, live_only: bool = True
) -> list[tuple[Hypothesis, float]]:
    """Hypotheses paired with score, highest first (live ones by default)."""
    by_id = {h.id: h for h in hyps}
    items = [
        (by_id[hid], s)
        for hid, s in scores.items()
        if hid in by_id and (not live_only or s > ELIMINATE_FLOOR)
    ]
    return sorted(items, key=lambda t: t[1], reverse=True)


def anchor_focus(
    ranked_live: list[tuple[Hypothesis, float]], affirmed: set[str]
) -> tuple[list[tuple[Hypothesis, float]], bool]:
    """Restrict questioning to needs the person has already warmed to.

    Once any need has been confirmed (yes) or warmed (kinda), drop the needs that
    have never been warmed so the next questions DRILL INTO the warm area to get
    more specific, instead of drifting to cold needs. Keeps a single warm need
    (so we can deepen it); falls back to the full set when nothing is warm yet.
    Returns ``(focused, anchored)``.
    """
    if not affirmed:
        return ranked_live, False
    focus = [(h, s) for h, s in ranked_live if h.id in affirmed]
    if focus:
        return focus, True
    return ranked_live, False
