from my20q.taxonomy import Node, load_taxonomy


def test_real_taxonomy_loads(real_taxonomy: Node) -> None:
    assert real_taxonomy.id == "root"
    assert len(real_taxonomy.children) == 5
    top_ids = {c.id for c in real_taxonomy.children}
    assert top_ids == {"mental_health", "physical_health", "emergency", "general", "my_people"}


def test_emergency_flag_propagates_only_where_set(real_taxonomy: Node) -> None:
    emergency = real_taxonomy.find("emergency")
    mh = real_taxonomy.find("mental_health")
    assert emergency is not None and emergency.emergency is True
    assert mh is not None and mh.emergency is False
    # Children of emergency are also flagged explicitly.
    for child in emergency.children:
        assert child.emergency is True


def test_walk_leaves_excludes_internal_nodes(real_taxonomy: Node) -> None:
    leaves = real_taxonomy.walk_leaves()
    assert all(leaf.is_leaf for leaf in leaves)
    leaf_ids = {leaf.id for leaf in leaves}
    # Sanity: expected leaves are present.
    assert "ph_pain" in leaf_ids
    assert "em_chest" in leaf_ids
    assert "mental_health" not in leaf_ids  # internal node


def test_duplicate_ids_rejected(tmp_path) -> None:
    import pytest

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
id: root
label: r
question: ""
children:
  - id: dup
    label: one
    question: q
  - id: dup
    label: two
    question: q
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_taxonomy(bad)
