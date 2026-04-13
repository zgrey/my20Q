from my20q.agent.safety import EMERGENCY_SCREEN, is_emergency_path, sanitize_llm_text
from my20q.taxonomy import Node


def test_emergency_screen_has_actions() -> None:
    assert EMERGENCY_SCREEN["actions"]
    ids = {a["id"] for a in EMERGENCY_SCREEN["actions"]}
    assert "call_caregiver" in ids


def test_is_emergency_path_detects_any_emergency_ancestor(tiny_taxonomy: Node) -> None:
    b = tiny_taxonomy.find("cat_b")
    assert b is not None
    assert is_emergency_path([tiny_taxonomy, b])
    a = tiny_taxonomy.find("cat_a")
    assert a is not None
    assert not is_emergency_path([tiny_taxonomy, a])


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
