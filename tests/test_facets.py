"""Tests for the 5W1H facet board — the consensus-score belief."""

from __future__ import annotations

import itertools

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


def test_an_aggravation_question_credits_why_not_how() -> None:
    # A live round spent 25 of 57 questions on what made the pain worse, filed
    # every answer under `how` ("an action wanted"), and then double-checked
    # them as wants — "do you want to lift things?" — which correctly returned
    # no every time. They were answering `why` all along.
    worse = "Does lifting things make the pain worse?"
    assert facets.asks_about_aggravation(worse)
    assert facets.remap_slot("how", "lifting things", worse) == "why"
    # …and the same value, asked as a request, is still a wanted action.
    want = "Do you want help lifting things?"
    assert not facets.asks_about_aggravation(want)
    assert facets.remap_slot("how", "lifting things", want) == "how"


def test_aggravation_frames_seen_live_are_all_caught() -> None:
    for q in (
        "Does repeating movements make the pain worse?",
        "Does the pain get worse if you have to move your arm back and forth?",
        "Is the pain worse when you have to support weight with your arm?",
        "Is the pain you are feeling more bothersome when you lift things?",
    ):
        assert facets.asks_about_aggravation(q), q
    for q in (
        "Is it your wrist?",
        "Do you want to call them?",
        "Is the pain sharp?",
    ):
        assert not facets.asks_about_aggravation(q), q


def test_gerund_led_picks_the_right_sentence_frame() -> None:
    assert facets.gerund_led("lifting things")
    assert facets.gerund_led("repeating movements")
    assert facets.gerund_led("dropping things")
    # "bring" ends in -ing and is not a gerund — the trap this guards.
    assert not facets.gerund_led("bring it")
    assert not facets.gerund_led("call them")
    assert not facets.gerund_led("tell them something")
    assert not facets.gerund_led("")


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


# --------------------------------------------- W2-K · value identity (09-01)
# Regressions from the 2026-09-01 trial: every fine body part collapsed onto
# "right side", which capped the board at seed granularity, silently disabled
# refinement links, and wrote the wrong value into the recorded dataset.
# See docs/design/convergence-plan.md §1d C1.


def test_shared_modifier_does_not_collapse_distinct_heads() -> None:
    board = {"where": {"right side": 7.0}}
    for sibling in ("right leg", "right thigh", "right arm", "right calf"):
        assert facets.canonical_value(board, "where", sibling) == sibling


def test_a_refinement_is_never_folded_onto_its_parent() -> None:
    # "a phone call" ⊃ "a call": parent and child, not two spellings of one
    # value — folding them collapses the structure W3-H drills through.
    board = {"what": {"a call": 3.0}}
    assert facets.canonical_value(board, "what", "a phone call") == "a phone call"


def test_morphological_variants_still_fold() -> None:
    # 08-31 ended with confusion +2.0 beside confused −1.0, worried +1.0
    # beside worry −0.5 — one concept, four contenders.
    board = {"what": {"confusion": 2.0, "worry": 1.0}}
    assert facets.canonical_value(board, "what", "confused") == "confusion"
    assert facets.canonical_value(board, "what", "worried") == "worry"


def test_rewordings_still_fold() -> None:
    board = {"what": {"a picture": 2.0}}
    assert facets.canonical_value(board, "what", "the picture") == "a picture"


def test_multi_token_value_needs_its_head_not_just_any_token() -> None:
    # q024 "Is the pain you are feeling happening RIGHT now?" credited
    # where=right side off the word "right".
    assert not facets.mentions(
        "Is the pain you are feeling happening right now?", "right side"
    )
    assert not facets.mentions(
        "Is the feeling you are having located in your right thigh?", "right side"
    )
    assert facets.mentions("Is the place you want the right side?", "right side")
    # 08-31 q007 "Are you feeling lonely?" put −1.0 on why=feeling overwhelmed.
    assert not facets.mentions("Are you feeling lonely?", "feeling overwhelmed")


def test_single_token_values_still_match_loosely() -> None:
    assert facets.mentions("Are you feeling any tingling in your feet?", "tingling")
    assert facets.mentions("Is it hurting?", "hurt")


def test_vacuous_placeholders_never_become_contenders() -> None:
    # `what: feeling` reached +3.0 in the 09-01 round, within 1.0 of leading
    # the slot against the real answer.
    board = facets.update(facets.empty_board(), {"what": "feeling"}, "yes")
    assert board["what"] == {}
    # ...but a value that merely CONTAINS the word is fine.
    board = facets.update(facets.empty_board(), {"what": "feeling cold"}, "yes")
    assert board["what"] == {"feeling cold": 1.0}


def test_drill_down_chain_forms_and_the_frontier_reaches_the_fine_value() -> None:
    """The end-to-end 09-01 failure: the whole point of W2-K.

    The model tagged the fine value AND named its parent; the fold made child
    and parent identical, so reasoner._anchored_refines dropped the edge and
    `board.edges` came back empty in all 8 recorded rounds.
    """
    board = facets.apply_context(facets.empty_board(), {"where": ["right side"]})
    tags: list[tuple[str, str, str]] = []
    for child, parent in (("right leg", "right side"), ("right thigh", "right leg")):
        credited = facets.canonical_value(board, "where", child)
        assert credited == child, "the fine value must survive as its own contender"
        canonical_parent = facets.canonical_value(board, "where", parent)
        assert canonical_parent != credited, "parent and child must stay distinct"
        board = facets.update(board, {"where": credited}, "yes")
        tags.append(("where", credited, canonical_parent))

    edges = facets.derive_edges(board, tags)
    assert edges["where"] == {
        "right leg": "right side",
        "right thigh": "right leg",
    }
    assert facets.frontier(board, "where", edges) == ("right thigh", 1.0)


# ------------------------------------- W2-R: drill-inferred refinement edges


def test_drill_inference_stops_a_slot_fragmenting() -> None:
    # The bench's `cold` round: five yeses each narrowing the last, none
    # tagged and none lexically nested. Without an inferred link they become
    # five singleton families of mass 1.0, so family_confident can NEVER fire
    # and the draft freezes on whichever value was scored first.
    seq = ["an object", "keeps you warm", "fabric", "wrap yourself in", "blanket"]
    board = facets.seed_board({"what": ["an object"]})
    for v in seq:
        board = facets.update(board, {"what": v}, "yes")

    bare = facets.derive_edges(board, [])
    assert facets.families(board, "what", bare) == [(v, 1.0) for v in seq]
    assert facets.frontier(board, "what", bare) == ("an object", 1.0)
    assert not facets.family_confident(
        board, "what", bare, ready_points=2.0, margin=1.0
    )

    # Each drill parents to the frontier the caregiver was looking at, which
    # builds the ladder the round actually walked.
    drills = [("what", c, p) for p, c in itertools.pairwise(seq)]
    edges = facets.derive_edges(board, [], drills)
    assert facets.families(board, "what", edges) == [("an object", 5.0)]
    assert facets.frontier(board, "what", edges) == ("blanket", 1.0)
    assert facets.family_confident(
        board, "what", edges, ready_points=2.0, margin=1.0
    )


def test_drill_inference_to_the_leader_would_build_a_star() -> None:
    # Why the parent is the FRONTIER and not the leader: parenting every drill
    # to the leader accumulates mass correctly but leaves an arbitrary
    # depth-1 child as the frontier, so the draft still shows the wrong value.
    seq = ["an object", "keeps you warm", "fabric", "wrap yourself in", "blanket"]
    board = facets.seed_board({"what": ["an object"]})
    for v in seq:
        board = facets.update(board, {"what": v}, "yes")
    star = facets.derive_edges(board, [], [("what", c, "an object") for c in seq[1:]])
    assert facets.families(board, "what", star) == [("an object", 5.0)]
    assert facets.frontier(board, "what", star) != ("blanket", 1.0)


def test_explicit_tags_and_lexical_nesting_outrank_inference() -> None:
    # Inference is the LAST resort — it must never overwrite what the model
    # said or what the words plainly show.
    board = facets.seed_board({"what": ["discomfort", "a task"]})
    board = facets.update(board, {"what": "tingling"}, "yes")
    board = facets.update(board, {"what": "a specific cleanup task"}, "yes")
    edges = facets.derive_edges(
        board,
        [("what", "tingling", "discomfort")],          # explicit
        [                                               # inference, contradicting
            ("what", "tingling", "a task"),
            ("what", "a specific cleanup task", "discomfort"),
        ],
    )
    assert edges["what"]["tingling"] == "discomfort"            # explicit won
    assert edges["what"]["a specific cleanup task"] == "a task"  # lexical won


def test_inferred_edges_obey_the_same_legality_rules() -> None:
    board = facets.seed_board({"what": ["discomfort"]})
    board = facets.update(board, {"what": "tingling"}, "yes")
    # A parent that is not on the board cannot be invented.
    assert facets.derive_edges(board, [], [("what", "tingling", "ghost")])["what"] == {}
    # And a cycle is refused.
    edges = facets.derive_edges(
        board,
        [],
        [("what", "tingling", "discomfort"), ("what", "discomfort", "tingling")],
    )
    assert edges["what"] == {"tingling": "discomfort"}
