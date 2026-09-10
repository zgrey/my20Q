import pytest

from my20q.topics import find_topic, load_topics


def test_topics_load_with_emergency_first() -> None:
    topics = load_topics()
    ids = [t.id for t in topics]
    assert ids[0] == "emergency"
    assert set(ids) == {
        "emergency",
        "mental_health",
        "physical_health",
        "my_people",
        "general",
    }


def test_emergency_topic_flagged() -> None:
    topics = load_topics()
    emergency = find_topic(topics, "emergency")
    physical = find_topic(topics, "physical_health")
    assert emergency is not None and emergency.emergency is True
    assert physical is not None and physical.emergency is False


def test_core_facets_and_hint_present() -> None:
    topics = load_topics()
    people = find_topic(topics, "my_people")
    assert people is not None
    assert people.core_facets == ["who", "how"]
    assert people.reasoning_hint  # the my_people bucket guidance is present
    general = find_topic(topics, "general")
    assert general is not None
    assert general.core_facets == ["what", "how"]


def test_unknown_core_facets_rejected(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- id: t\n  label: T\n  core_facets: [what, sideways]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sideways"):
        load_topics(bad)


def test_duplicate_ids_rejected(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- id: dup\n  label: one\n- id: dup\n  label: two\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_topics(bad)
