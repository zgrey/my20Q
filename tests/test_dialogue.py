from my20q.agent.dialogue import Answer, DialogueSession
from my20q.taxonomy import Node


def test_yes_path_reaches_confirmed_leaf(tiny_taxonomy: Node) -> None:
    session = DialogueSession(tiny_taxonomy, llm=None)
    r = session.select_category("cat_a")
    assert r.kind == "question"
    assert r.node.id == "a1"  # first child probed

    r = session.answer(Answer.YES)
    assert r.kind == "confirm_leaf"
    assert r.node.id == "a1"

    r = session.answer(Answer.YES)
    assert r.kind == "answer"
    assert r.node.id == "a1"
    assert r.summary  # fallback summary is non-empty


def test_no_on_probe_advances_to_next_sibling(tiny_taxonomy: Node) -> None:
    session = DialogueSession(tiny_taxonomy, llm=None)
    r = session.select_category("cat_a")
    assert r.node.id == "a1"
    r = session.answer(Answer.NO)
    assert r.kind == "question"
    assert r.node.id == "a2"


def test_leaf_rejection_backs_up_and_tries_sibling(tiny_taxonomy: Node) -> None:
    session = DialogueSession(tiny_taxonomy, llm=None)
    session.select_category("cat_a")       # probe a1
    session.answer(Answer.YES)              # descend into a1 (confirm_leaf)
    r = session.answer(Answer.NO)           # reject a1 leaf — probe next sibling
    assert r.kind == "question"
    assert r.node.id == "a2"
    r = session.answer(Answer.YES)          # descend into a2 → confirm
    assert r.kind == "confirm_leaf"
    assert r.node.id == "a2"


def test_not_sure_defers_and_retries(tiny_taxonomy: Node) -> None:
    session = DialogueSession(tiny_taxonomy, llm=None)
    session.select_category("cat_a")       # probe a1
    r = session.answer(Answer.NOT_SURE)     # defer a1 → probe a2
    assert r.node.id == "a2"
    r = session.answer(Answer.NO)           # reject a2 → retry deferred a1
    assert r.kind == "question"
    assert r.node.id == "a1"


def test_emergency_category_short_circuits(tiny_taxonomy: Node) -> None:
    session = DialogueSession(tiny_taxonomy, llm=None)
    r = session.select_category("cat_b")
    assert r.kind == "emergency"
    assert r.node.id == "cat_b"


def test_dead_end_when_all_children_rejected(tiny_taxonomy: Node) -> None:
    session = DialogueSession(tiny_taxonomy, llm=None)
    session.select_category("cat_a")
    session.answer(Answer.NO)  # reject a1
    r = session.answer(Answer.NO)  # reject a2 → exhausted cat_a subtree
    # Root queue was cleared by select_category, so we hit dead_end.
    assert r.kind == "dead_end"


def test_select_category_rejects_non_top_level(tiny_taxonomy: Node) -> None:
    import pytest

    session = DialogueSession(tiny_taxonomy, llm=None)
    with pytest.raises(ValueError):
        session.select_category("a1")  # not a top-level child
    with pytest.raises(ValueError):
        session.select_category("does_not_exist")


def test_max_turns_forces_dead_end(tiny_taxonomy: Node) -> None:
    session = DialogueSession(tiny_taxonomy, llm=None, max_turns=1)
    session.select_category("cat_a")      # probe a1 (turn still 0)
    r = session.answer(Answer.NO)         # turn=1, meets max → dead_end
    assert r.kind == "dead_end"
