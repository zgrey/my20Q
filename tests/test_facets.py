"""Tests for the 5W1H facet board — the consensus-score belief."""

from __future__ import annotations

from my20q.agent import facets


def _seeded() -> facets.Board:
    return facets.seed_board(
        {
            "who": ["Zach", "Rob"],
            "what": ["a picture", "a drink"],
            "why": ["redecorating"],
            "how": ["move it"],
        }
    )


def test_seed_board_zeroes_and_drops_unknown_categories() -> None:
    board = facets.seed_board({"who": ["Zach"], "bogus": ["x"]})
    assert board["who"] == {"Zach": 0.0}
    assert "bogus" not in board
    assert board["what"] == {}  # every category exists, even when unseeded


def test_update_credits_only_the_asserted_pairs() -> None:
    board = _seeded()
    board = facets.update(board, {"who": "Zach"}, "yes")
    assert board["who"]["Zach"] == 1.0
    assert board["who"]["Rob"] == 0.0  # untouched — no renormalization
    assert board["what"]["a picture"] == 0.0  # other categories untouched


def test_update_answer_weights() -> None:
    board = _seeded()
    assert facets.update(board, {"who": "Zach"}, "kinda")["who"]["Zach"] == 0.5
    assert facets.update(board, {"who": "Zach"}, "no")["who"]["Zach"] == -1.0
    assert facets.update(board, {"who": "Zach"}, "not_sure")["who"]["Zach"] == 0.0


def test_no_on_a_fresh_value_keeps_it_known_rejected() -> None:
    # A "no" to a value not yet on the board mints it NEGATIVE — it stays
    # known-rejected, so a later question cannot resurrect it at zero.
    board = facets.update(_seeded(), {"who": "Aaron"}, "no")
    assert board["who"]["Aaron"] == -1.0
    board = facets.update(board, {"who": "Aaron"}, "no")
    assert board["who"]["Aaron"] == -2.0
    assert ("Aaron", -2.0) not in facets.live(board, "who")  # at the floor — out


def test_update_merges_case_insensitively() -> None:
    board = facets.update(_seeded(), {"who": "zach"}, "yes")
    assert board["who"]["Zach"] == 1.0  # folded onto the existing casing
    assert "zach" not in board["who"]


def test_apply_context_is_a_strong_boost() -> None:
    board = facets.apply_context(_seeded(), {"what": ["a picture", "the frame"]})
    assert board["what"]["a picture"] == facets.CONTEXT_POINTS
    assert board["what"]["the frame"] == facets.CONTEXT_POINTS  # minted + boosted


def test_leader_live_and_floor() -> None:
    board = _seeded()
    board = facets.update(board, {"who": "Zach"}, "yes")
    board = facets.update(board, {"who": "Rob"}, "no")
    board = facets.update(board, {"who": "Rob"}, "no")
    assert facets.leader(board, "who") == ("Zach", 1.0)
    live = facets.live(board, "who")
    assert ("Rob", -2.0) not in live  # eliminated at the floor
    assert facets.leader(board, "when") is None  # empty category


def test_confident_needs_points_and_margin() -> None:
    board = _seeded()
    assert not facets.confident(board, "who", ready_points=2.0, margin=1.0)
    for _ in range(2):
        board = facets.update(board, {"who": "Zach"}, "yes")
    assert facets.confident(board, "who", ready_points=2.0, margin=1.0)
    # A rival within the margin breaks confidence.
    board = facets.update(board, {"who": "Rob"}, "yes")
    board = facets.update(board, {"who": "Rob"}, "yes")
    assert not facets.confident(board, "who", ready_points=2.0, margin=1.0)


def test_tied_top_flags_matching_positive_scores() -> None:
    board = _seeded()
    for _ in range(2):
        board = facets.update(board, {"who": "Zach"}, "yes")
        board = facets.update(board, {"who": "Rob"}, "yes")
    pair = facets.tied_top(board, "who", margin=1.0)
    assert pair is not None
    values = {pair[0][0], pair[1][0]}
    assert values == {"Zach", "Rob"}
    # Zero-scored seeds are not a meaningful tie.
    assert facets.tied_top(board, "what", margin=1.0) is None


def test_snapshot_restore_roundtrip() -> None:
    board = facets.update(_seeded(), {"who": "Zach", "how": "move it"}, "yes")
    restored = facets.restore(facets.snapshot(board))
    assert restored["who"]["Zach"] == 1.0
    assert restored["how"]["move it"] == 1.0
    assert restored["what"]["a drink"] == 0.0


def test_merge_values_caps_and_preserves_scores() -> None:
    board = facets.update(_seeded(), {"who": "Zach"}, "yes")
    merged = facets.merge_values(
        board, {"who": ["Zach", "Julie", "Sam", "Pat", "Lee", "Max", "Ann"]}
    )
    assert merged["who"]["Zach"] == 1.0  # existing score never reset
    assert len(merged["who"]) <= facets.MAX_PER_CATEGORY


# ---------------------------------------------------- question-text anchoring


def test_mentions_requires_the_value_in_the_text() -> None:
    q = "Is what you need from Zach related to physically assisting you?"
    assert facets.mentions(q, "Zach")
    assert facets.mentions(q, "physical assistance")  # stemmed overlap
    # THE Aaron bug: this question says nothing about an Aaron visit.
    assert not facets.mentions(q, "Aaron coming to visit")


def test_mentions_stopword_only_value_falls_back_to_substring() -> None:
    # "something" is all stopwords — substring matching is the fallback.
    assert facets.mentions("Is it something you can see?", "something")
    assert not facets.mentions("Do you want a drink?", "something")


def test_canonical_value_folds_variants() -> None:
    board = _seeded()
    assert facets.canonical_value(board, "what", "the picture") == "a picture"
    assert facets.canonical_value(board, "what", "A Drink") == "a drink"
    assert facets.canonical_value(board, "what", "a warm blanket") == "a warm blanket"


def test_facet_view_shape() -> None:
    board = facets.update(_seeded(), {"who": "Zach"}, "yes")
    view = facets.facet_view(board, "who")
    assert [v["category"] for v in view] == list(facets.CATEGORIES)
    who = view[0]
    assert who["focus"] is True
    assert who["contenders"][0] == {"value": "Zach", "score": 1.0}
    when = next(v for v in view if v["category"] == "when")
    assert when["contenders"] == [] and when["focus"] is False
