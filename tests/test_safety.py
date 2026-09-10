from my20q.agent.safety import (
    EMERGENCY_SCREEN,
    for_speech,
    sanitize_llm_text,
    sanitize_utterance,
)


def test_emergency_screen_has_actions() -> None:
    assert EMERGENCY_SCREEN["actions"]
    ids = {a["id"] for a in EMERGENCY_SCREEN["actions"]}
    assert "call_caregiver" in ids


def test_sanitize_drops_urls() -> None:
    assert sanitize_llm_text("See https://example.com for more") == ""


def test_sanitize_drops_medical_advice() -> None:
    assert sanitize_llm_text("Take 50 mg of acetaminophen") == ""
    assert sanitize_llm_text("You should prescribe rest") == ""


def test_sanitize_drops_html() -> None:
    assert sanitize_llm_text("<b>hi</b>") == ""


def test_sanitize_strips_quotes_and_extra_lines() -> None:
    out = sanitize_llm_text('"They need help going to the bathroom."\nextra line')
    assert out == "They need help going to the bathroom."


def test_sanitize_truncates_long_strings() -> None:
    out = sanitize_llm_text("word " * 200)
    assert out.endswith("…")
    assert len(out) <= 241


def test_sanitize_utterance_keeps_a_full_sentence() -> None:
    out = sanitize_utterance('"I would like to call my daughter this afternoon."')
    assert out == "I would like to call my daughter this afternoon."


def test_sanitize_utterance_collapses_whitespace() -> None:
    assert sanitize_utterance("I feel\n  tired   today") == "I feel tired today"


def test_sanitize_utterance_drops_medical_advice() -> None:
    assert sanitize_utterance("You should increase the dosage") == ""


def test_for_speech_strips_asterisks_and_markdown() -> None:
    # The reported bug: TTS voiced the literal "*". Formatting chars are
    # dropped and the surrounding whitespace collapsed.
    assert for_speech("Are you *really* hungry?") == "Are you really hungry?"
    assert for_speech("**bold** _em_ `code` #tag") == "bold em code tag"


def test_for_speech_keeps_spoken_punctuation() -> None:
    assert (
        for_speech("Okay, not food then — are you thirsty?")
        == "Okay, not food then — are you thirsty?"
    )
