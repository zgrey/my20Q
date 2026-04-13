"""YAML loader for the taxonomy tree."""

from __future__ import annotations

from pathlib import Path

import yaml

from my20q.taxonomy.node import Node


def load_taxonomy(path: Path) -> Node:
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Taxonomy at {path} must be a YAML mapping at the root")
    root = Node.model_validate(raw)
    _check_unique_ids(root)
    return root


def _check_unique_ids(root: Node) -> None:
    seen: set[str] = set()
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.id in seen:
            raise ValueError(f"Duplicate taxonomy node id: {node.id!r}")
        seen.add(node.id)
        stack.extend(node.children)
