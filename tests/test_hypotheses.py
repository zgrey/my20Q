"""Tests for the pure belief logic behind the reasoning controller."""

from __future__ import annotations

from my20q.agent.hypotheses import (
    PRUNE_EPS,
    SYNTH_THRESHOLD,
    Hypothesis,
    apply_context,
    is_balanced,
    leader,
    live_ids,
    ranked,
    recompute,
    seed_weights,
    should_synthesize,
    split_balance,
    update_weights,
)

H = [Hypothesis(f"h{i}", f"need {i}") for i in range(1, 5)]  # h1..h4


def test_seed_weights_uniform() -> None:
    w = seed_weights(H)
    assert set(w) == {"h1", "h2", "h3", "h4"}
    assert all(abs(v - 0.25) < 1e-9 for v in w.values())
    assert abs(sum(w.values()) - 1) < 1e-9


def test_seed_weights_empty() -> None:
    assert seed_weights([]) == {}


def test_yes_upweights_the_yes_set_and_normalizes() -> None:
    w = seed_weights(H)
    w2 = update_weights(w, {"h1", "h2"}, "yes")
    assert w2["h1"] > w["h1"]
    assert w2["h3"] < w["h3"]
    assert abs(sum(w2.values()) - 1) < 1e-9


def test_no_upweights_the_complement() -> None:
    w = seed_weights(H)
    w2 = update_weights(w, {"h1"}, "no")
    assert w2["h1"] < w["h1"]
    assert w2["h2"] > w["h2"]


def test_kinda_is_a_softer_yes() -> None:
    w = seed_weights(H)
    yes = update_weights(w, {"h1"}, "yes")
    kinda = update_weights(w, {"h1"}, "kinda")
    assert w["h1"] < kinda["h1"] < yes["h1"]


def test_not_sure_carries_no_information() -> None:
    w = seed_weights(H)
    assert update_weights(w, {"h1", "h2"}, "not_sure") == w


def test_recompute_equals_sequential_folding() -> None:
    pairs = [({"h1", "h2"}, "yes"), ({"h1"}, "no")]
    manual = seed_weights(H)
    for yes_ids, ans in pairs:
        manual = update_weights(manual, yes_ids, ans)
    assert recompute(H, pairs) == manual


def test_live_ids_prunes_eliminated() -> None:
    w = seed_weights(H)
    for _ in range(5):
        w = update_weights(w, {"h1", "h2"}, "yes")
    assert set(live_ids(w)) == {"h1", "h2"}
    assert all(w[k] < PRUNE_EPS for k in ("h3", "h4"))


def test_leader_and_synthesis_trigger() -> None:
    w = seed_weights(H)
    assert not should_synthesize(w)  # uniform over 4 — keep asking
    for _ in range(6):
        w = update_weights(w, {"h1"}, "yes")
    lid, lw = leader(w)
    assert lid == "h1" and lw >= SYNTH_THRESHOLD
    assert should_synthesize(w)


def test_should_synthesize_when_one_live() -> None:
    assert should_synthesize({"h1": 0.99, "h2": 0.001})


def test_split_balance_and_is_balanced() -> None:
    w = seed_weights(H)
    assert abs(split_balance(w, {"h1", "h2"}) - 0.5) < 1e-9
    assert is_balanced(w, {"h1", "h2"})
    assert not is_balanced(w, set())  # nobody answers yes
    assert not is_balanced(w, {"h1", "h2", "h3", "h4"})  # everybody does


def test_apply_context_makes_a_confirmed_need_lead() -> None:
    w = seed_weights(H)  # uniform over 4
    w2 = apply_context(w, [], ["h1"])  # the note confirms existing h1
    assert abs(sum(w2.values()) - 1) < 1e-9
    assert w2["h1"] > 0.6  # one strong note -> clear leader
    assert w2["h1"] > w2["h2"]


def test_apply_context_inserts_a_new_need_on_top() -> None:
    w = {"h1": 0.5, "h2": 0.5}
    w2 = apply_context(w, ["h3"], [])
    assert "h3" in w2
    assert w2["h3"] > w2["h1"]


def test_apply_context_is_noop_when_nothing() -> None:
    w = {"h1": 0.6, "h2": 0.4}
    assert apply_context(w, [], []) == w


def test_ranked_is_sorted_descending() -> None:
    w = update_weights(seed_weights(H), {"h2"}, "yes")
    r = ranked(H, w)
    assert r[0][0].id == "h2"
    assert all(r[i][1] >= r[i + 1][1] for i in range(len(r) - 1))
