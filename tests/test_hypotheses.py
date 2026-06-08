"""Tests for the additive point-scoring belief behind the reasoning controller."""

from __future__ import annotations

from my20q.agent.hypotheses import (
    KINDA_POINTS,
    NO_POINTS,
    YES_POINTS,
    Hypothesis,
    anchor_focus,
    apply_context,
    leader,
    live_ids,
    ranked,
    recompute,
    seed_scores,
    update_score,
)

H = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 5)]  # h1..h4


def test_seed_scores_start_at_zero() -> None:
    s = seed_scores(H)
    assert set(s) == {"h1", "h2", "h3", "h4"}
    assert all(v == 0.0 for v in s.values())


def test_yes_adds_points_to_targeted_only() -> None:
    s = update_score(seed_scores(H), {"h1"}, "yes")
    assert s["h1"] == YES_POINTS
    assert s["h2"] == 0.0 and s["h3"] == 0.0  # untargeted needs are untouched


def test_kinda_is_a_partial_positive() -> None:
    s = update_score(seed_scores(H), {"h2"}, "kinda")
    assert s["h2"] == KINDA_POINTS
    assert 0.0 < KINDA_POINTS < YES_POINTS


def test_no_subtracts_targeted_only_and_never_promotes_others() -> None:
    s = update_score(seed_scores(H), {"h1"}, "no")
    assert s["h1"] == -NO_POINTS
    assert s["h2"] == 0.0 and s["h3"] == 0.0  # a "no" must NOT promote the others


def test_not_sure_changes_nothing() -> None:
    s = update_score(seed_scores(H), {"h1"}, "yes")
    assert update_score(s, {"h1"}, "not_sure") == s


def test_scores_do_not_sum_to_one() -> None:
    s = update_score(seed_scores(H), {"h1", "h2"}, "yes")
    assert abs(sum(s.values()) - 2 * YES_POINTS) < 1e-9  # additive, not normalized


def test_leader_is_highest_points() -> None:
    s = recompute(H, [({"h1"}, "yes"), ({"h2"}, "yes"), ({"h2"}, "yes")])
    assert leader(s)[0] == "h2"


def test_repeated_no_eliminates_a_need() -> None:
    s = seed_scores(H)
    for _ in range(3):  # drive h1 below the eliminate floor
        s = update_score(s, {"h1"}, "no")
    assert "h1" not in live_ids(s)
    assert leader(s)[0] != "h1"


def test_apply_context_adds_strong_points() -> None:
    s = apply_context(seed_scores(H), ["h5"], ["h1"])
    assert s["h5"] > 0.0 and s["h1"] > 0.0


def test_anchor_focus_restricts_to_warm_cluster() -> None:
    s = recompute(H, [({"h2"}, "kinda")])
    focused, anchored = anchor_focus(ranked(H, s), {"h2"})
    assert anchored is True
    assert {h.id for h, _ in focused} == {"h2"}  # cold needs dropped


def test_anchor_focus_keeps_full_set_when_nothing_warm() -> None:
    focused, anchored = anchor_focus(ranked(H, seed_scores(H)), set())
    assert anchored is False
    assert len(focused) == 4
