from pathlib import Path

import pytest

from my20q.taxonomy import Node, load_taxonomy


@pytest.fixture
def real_taxonomy() -> Node:
    root = Path(__file__).resolve().parent.parent
    data = root / "src" / "my20q" / "taxonomy" / "data" / "tree.yaml"
    return load_taxonomy(data)


@pytest.fixture
def tiny_taxonomy() -> Node:
    """A small hand-built tree for deterministic state-machine tests."""
    return Node.model_validate(
        {
            "id": "root",
            "label": "root",
            "question": "",
            "children": [
                {
                    "id": "cat_a",
                    "label": "A",
                    "question": "Is this A?",
                    "children": [
                        {"id": "a1", "label": "A1", "question": "Is this A1?"},
                        {"id": "a2", "label": "A2", "question": "Is this A2?"},
                    ],
                },
                {
                    "id": "cat_b",
                    "label": "B",
                    "question": "Is this B?",
                    "emergency": True,
                    "children": [
                        {"id": "b1", "label": "B1", "question": "Is this B1?", "emergency": True}
                    ],
                },
            ],
        }
    )
