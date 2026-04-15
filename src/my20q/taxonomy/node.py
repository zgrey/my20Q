"""Taxonomy node model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Node(BaseModel):
    """One node in the communication taxonomy.

    Leaves represent a concrete thing a patient may want to express
    ("I am in pain", "I want to call my daughter"). Internal nodes are
    high-level categories used to narrow the dialogue.
    """

    id: str
    label: str
    question: str = Field(
        description="Yes/no question whose 'yes' answer implies this node.",
    )
    image: str | None = None
    description: str | None = Field(
        default=None,
        description="Short context for the LLM; never shown to the patient.",
    )
    reasoning_hint: str | None = Field(
        default=None,
        description=(
            "Category-scoped guidance threaded into the reasoning-mode "
            "prompt when this node is the starting category. Never shown "
            "to the patient."
        ),
    )
    emergency: bool = False
    children: list[Node] = Field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def walk_leaves(self) -> list[Node]:
        if self.is_leaf:
            return [self]
        out: list[Node] = []
        for child in self.children:
            out.extend(child.walk_leaves())
        return out

    def find(self, node_id: str) -> Node | None:
        if self.id == node_id:
            return self
        for child in self.children:
            hit = child.find(node_id)
            if hit is not None:
                return hit
        return None


Node.model_rebuild()
