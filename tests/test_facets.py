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


def test_no_hits_only_the_lowest_scoring_pair() -> None:
    # THE collateral-damage fix: "Do you want to remind Rob about your blood
    # pressure?" → no must disconfirm *blood pressure*, not the confirmed
    # *Rob* (one trial buried its only anchor under 29 such hits).
    board = _seeded()
    board = facets.update(board, {"who": "Rob"}, "yes")
    board = facets.update(board, {"who": "Rob"}, "yes")
    board = facets.update(board, {"who": "Rob", "what": "blood pressure"}, "no")
    assert board["who"]["Rob"] == 2.0  # the anchor is protected
    assert board["what"]["blood pressure"] == -1.0  # the guess takes the hit


def test_no_on_a_single_pair_still_counts_in_full() -> None:
    board = facets.update(_seeded(), {"who": "Zach"}, "yes")
    board = facets.update(board, {"who": "Zach"}, "no")  # direct "Is it Zach?"
    assert board["who"]["Zach"] == 0.0


def test_no_with_tied_low_pairs_hits_all_of_them() -> None:
    board = facets.update(_seeded(), {"who": "Rob", "what": "a drink"}, "no")
    assert board["who"]["Rob"] == -1.0  # both at 0 → both lowest → both hit
    assert board["what"]["a drink"] == -1.0


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


def test_action_values_refile_from_what_to_how() -> None:
    # "help with tasks" reached +6 in WHAT in one trial while HOW never
    # established — actions are how, objects are what.
    assert facets.action_like("help with tasks")
    assert facets.action_like("physically move items around")  # adverb-led
    assert not facets.action_like("a picture")
    assert not facets.action_like("the kitchen")
    assert facets.remap_slot("what", "help with tasks") == "how"
    assert facets.remap_slot("what", "a picture") == "what"
    assert facets.remap_slot("why", "cleaning") == "why"  # only what is re-filed


# ------------------------------------------------------- the direction layer


def test_mirror_map_is_symmetric() -> None:
    for a, b in facets.MIRROR.items():
        assert facets.MIRROR[b] == a
        assert a in facets.DIRECTION_BUCKETS and b in facets.DIRECTION_BUCKETS


def test_classify_direction_them_for_me() -> None:
    who = ["Rob", "Zach"]
    assert facets.classify_direction("Do you want Rob to clean the kitchen?", who) == "them_for_me"
    assert facets.classify_direction("Do you need Zach to bring you a drink?", who) == "them_for_me"
    assert facets.classify_direction("Will Zach help you move it?", who) == "them_for_me"
    assert (
        facets.classify_direction("Do you want someone to have a visit with you?", who)
        == "them_for_me"
    )


def test_classify_direction_me_for_them() -> None:
    who = ["Rob"]
    assert facets.classify_direction("Do you want to bring Rob a drink?", who) == "me_for_them"
    assert facets.classify_direction("Do you want to visit Rob?", who) == "me_for_them"
    assert (
        facets.classify_direction("Do you want to make your space look nice for Rob?", who)
        == "me_for_them"
    )


def test_classify_direction_tell_and_ask() -> None:
    who = ["Rob", "Julie"]
    assert (
        facets.classify_direction("Do you want to tell Rob that you are proud of him?", who)
        == "tell_them"
    )
    assert (
        facets.classify_direction("Do you want Rob to know that you love him?", who)
        == "tell_them"
    )
    assert (
        facets.classify_direction("Are you wanting to ask Julie about something?", who)
        == "ask_them"
    )


def test_classify_direction_leaves_concern_unclassified() -> None:
    # "want Rob to be okay" is care ABOUT him, not a task request — and state
    # questions are not direction. Genuine concerns must never read as tasks.
    who = ["Rob", "Zach"]
    assert facets.classify_direction("Do you want Rob to be okay?", who) is None
    assert facets.classify_direction("Is Zach having trouble with something?", who) is None
    assert facets.classify_direction("Are you worried about Rob?", who) is None


# ------------------------------------------ refinement links (W3-H)


def test_derive_edges_explicit_and_lexical() -> None:
    board = facets.seed_board({"what": ["discomfort", "a task"]})
    board = facets.update(board, {"what": "tingling"}, "yes")
    board = facets.update(board, {"what": "a specific cleanup task"}, "yes")
    edges = facets.derive_edges(board, [("what", "tingling", "discomfort")])
    assert edges["what"]["tingling"] == "discomfort"  # explicit (synonyms)
    # Lexical subset fallback: "a specific cleanup task" ⊃ "a task".
    assert edges["what"]["a specific cleanup task"] == "a task"


def test_derive_edges_never_resurrects_or_cycles() -> None:
    board = facets.seed_board({"what": ["discomfort"]})
    edges = facets.derive_edges(board, [("what", "discomfort", "ghost")])
    assert edges["what"] == {}  # the parent must already exist
    board = facets.update(board, {"what": "tingling"}, "yes")
    edges = facets.derive_edges(
        board,
        [("what", "tingling", "discomfort"), ("what", "discomfort", "tingling")],
    )
    assert edges["what"] == {"tingling": "discomfort"}  # the cycle is refused


def test_family_mass_is_nonnegative_protection() -> None:
    # The owner's "functional protection": a child's no never erodes the
    # family — the parent stays locked while the round weaves through failed
    # refinements (no propagated points; pure read-time shielding).
    board = facets.seed_board({"what": ["discomfort"]})
    for _ in range(3):
        board = facets.update(board, {"what": "discomfort"}, "yes")
    board = facets.update(board, {"what": "burning"}, "no")
    board = facets.update(board, {"what": "stabbing"}, "no")
    edges = facets.derive_edges(
        board,
        [("what", "burning", "discomfort"), ("what", "stabbing", "discomfort")],
    )
    assert facets.families(board, "what", edges)[0] == ("discomfort", 3.0)


def test_frontier_descends_and_retreats() -> None:
    board = facets.seed_board({"what": ["discomfort"]})
    board = facets.update(board, {"what": "discomfort"}, "yes")
    board = facets.update(board, {"what": "discomfort"}, "yes")
    board = facets.update(board, {"what": "tingling"}, "yes")
    edges = facets.derive_edges(board, [("what", "tingling", "discomfort")])
    top = facets.frontier(board, "what", edges)
    assert top is not None and top[0] == "tingling"  # one confirmed yes weaves
    # The fine value loses support → the frontier RETREATS to the parent.
    board = facets.update(board, {"what": "tingling"}, "no")
    top = facets.frontier(board, "what", edges)
    assert top is not None and top[0] == "discomfort"


def test_family_confident_collapses_fragmentation() -> None:
    # The thigh round's shape: five rivals for ONE sensation kept the slot
    # permanently unconfident (22× drill-hammering); as one family it is
    # decisively established.
    board = facets.seed_board({"what": ["discomfort", "the chair"]})
    for value, answer in [
        ("discomfort", "yes"), ("discomfort", "yes"), ("tingling", "yes"),
        ("pins and needles", "kinda"), ("buzzing", "kinda"),
        ("the chair", "yes"), ("the chair", "yes"),
    ]:
        board = facets.update(board, {"what": value}, answer)
    edges = facets.derive_edges(
        board,
        [("what", "tingling", "discomfort"),
         ("what", "pins and needles", "discomfort"),
         ("what", "buzzing", "discomfort")],
    )
    assert not facets.confident(board, "what", ready_points=2.0, margin=1.0)
    assert facets.family_confident(
        board, "what", edges, ready_points=2.0, margin=1.0
    )


def test_facet_view_shape() -> None:
    board = facets.update(_seeded(), {"who": "Zach"}, "yes")
    view = facets.facet_view(board, "who")
    assert [v["category"] for v in view] == list(facets.CATEGORIES)
    who = view[0]
    assert who["focus"] is True
    assert who["contenders"][0] == {"value": "Zach", "score": 1.0, "parent": None}
    when = next(v for v in view if v["category"] == "when")
    assert when["contenders"] == [] and when["focus"] is False
