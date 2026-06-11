"""Tests for the question auditors: yes/no form, meta leakage, repeat gate."""

from __future__ import annotations

from my20q.agent.auditor import audit_query, is_repeat


def test_audit_passes_a_plain_yes_no() -> None:
    assert audit_query("Is this about your daughter?").ok


def test_audit_flags_missing_question_mark() -> None:
    assert not audit_query("Tell me about your day").ok


def test_audit_flags_either_or() -> None:
    result = audit_query("Is the thing inside or outside your house?")
    assert not result.ok
    assert "either/or" in result.reason


def test_audit_flags_open_wh_question() -> None:
    assert not audit_query("What do you need right now?").ok


def test_audit_flags_leaked_reasoning_language() -> None:
    # gemma's deliberation must never leak into the patient-facing question.
    result = audit_query("Does the draft question about the candidate fit?")
    assert not result.ok
    assert "reasoning language" in result.reason
    assert not audit_query("Is this question about the patient feeling cold?").ok


# ----------------------------------------------------------- the repeat gate


def test_repeat_gate_catches_exact_and_normalized_matches() -> None:
    asked = ["Do you want to talk to Zach about something important?"]
    assert is_repeat("Do you want to talk to Zach about something important?", asked)
    assert is_repeat('do you want to talk to zach about something important??', asked)


def test_repeat_gate_catches_high_similarity_rewords() -> None:
    # The gemma3 trial asked these as "new" questions — they are the same idea.
    asked = ["Do you need help with a specific task right now?"]
    assert is_repeat("Do you need help completing a specific task right now?", asked)


def test_repeat_gate_catches_content_identical_rewords() -> None:
    # Same content words, different framing — the gemma4 trial slipped these
    # past the string-similarity check and farmed re-confirmations with them.
    asked = ["Do you want Zach to come over to help with tasks?"]
    assert is_repeat("Will Zach come over to help with tasks?", asked)
    assert is_repeat("Will Zach help with tasks when he comes over?", asked)


def test_repeat_gate_allows_genuinely_new_questions() -> None:
    asked = [
        "Do you want to talk to Zach about something important?",
        "Is it something you can see?",
    ]
    assert is_repeat("Is it a picture?", asked) is None
    assert is_repeat("Do you need them to lift something heavy?", asked) is None


def test_repeat_gate_slot_exemption_for_new_category_drills() -> None:
    # A content-overlap match that asserts a NEW slot category is a drill on
    # the same anchor, not a repeat ("…when he comes over?" asserts `when`).
    asked = ["Do you want Zach to come over to help with tasks?"]
    cats = [frozenset({"who", "how"})]
    q = "Will Zach help with tasks when he comes over?"
    assert is_repeat(q, asked)  # no slot info — blocked (back-compat)
    assert is_repeat(  # same categories — still a reword
        q, asked, candidate_cats=frozenset({"who", "how"}), asked_cats=cats
    )
    assert (  # new category asserted — the drill passes
        is_repeat(q, asked, candidate_cats=frozenset({"who", "when"}), asked_cats=cats)
        is None
    )
    assert is_repeat(  # unknown prior categories (flip-superseded): never exempt
        q, asked, candidate_cats=frozenset({"who", "when"}), asked_cats=[None]
    )


def test_repeat_gate_distinguishes_short_subject_swaps() -> None:
    # Short questions differing in the one content word are NOT repeats.
    assert is_repeat("Is it a person?", ["Is it a picture?"]) is None
