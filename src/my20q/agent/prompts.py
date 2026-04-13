"""System prompts and templating for the LLM.

The LLM is intentionally used only for narrow, sanitized tasks:
  1. Rephrase a curated question in simpler aphasia-friendly wording.
  2. Summarize the traversal path as one short caregiver-facing sentence.

The LLM never drives the dialogue tree — it only decorates it.
"""

from __future__ import annotations

from my20q.llm.base import LLMMessage
from my20q.taxonomy.node import Node

SYSTEM_PROMPT = """\
You help phrase very short, gentle questions and summaries for elderly \
patients with aphasia and their caregivers.

HARD RULES:
- Reply with ONE short sentence only. Never multiple sentences.
- Use concrete, everyday words. No medical jargon.
- Never give medical advice, diagnoses, dosages, or treatment suggestions.
- Never include URLs, HTML, markdown, or emoji.
- If asked to rephrase a question, keep it yes/no answerable.
"""


def rephrase_question_messages(question: str) -> list[LLMMessage]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Rephrase this yes/no question in simpler words for someone "
                "with aphasia. Keep it under six words if possible.\n\n"
                "Preserve the original meaning exactly. If the question asks "
                'about a desire or need ("need", "want", "would you like"), '
                'do NOT change it to describe an ongoing action ("are you '
                'taking", "are you doing").\n\n'
                f"Question: {question}"
            ),
        },
    ]


def summarize_path_messages(path: list[Node]) -> list[LLMMessage]:
    labels = " > ".join(n.label for n in path if n.id != "root")
    leaf = path[-1]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "A patient navigated through this assistive menu path:\n"
                f"  {labels}\n\n"
                f"Final selection: {leaf.label}.\n\n"
                "Write ONE short sentence the caregiver should see, starting with "
                '"They" and describing what the patient is communicating. '
                "No medical advice."
            ),
        },
    ]
