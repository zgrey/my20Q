"""System prompts and templating for the LLM.

The LLM is used for four tasks:
  1. Rephrase a curated question in simpler aphasia-friendly wording.
  2. Summarize the traversal path as one short caregiver-facing sentence
     (tree-walk fallback mode).
  3. Drive the 20-questions game in reasoning mode — propose the next
     question or guess based on accumulated history.
  4. Summarize a reasoning-mode game outcome for the caregiver.
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


GAME_SYSTEM_PROMPT = """\
You are playing 20 Questions to help someone with aphasia communicate \
what they need or feel. Be gentle. Use short, concrete, everyday words.

OUTPUT FORMAT — STRICT JSON, nothing else:
{"action": "question" | "guess", "content": "...", "rationale": "..."}

- "action" = "question" when you ask a yes/no narrowing question.
- "action" = "guess" when you propose a specific thing they might want
  or feel. A guess should be a concrete noun phrase or state — not
  another question.
- "content" is what the patient sees. Keep it under 8 words. Yes/no
  answerable if it is a question.
- "rationale" is one short sentence for the developer — never shown to
  the patient.

HOW TO READ THE ANSWERS (in history):
- "yes" — affirmed. Keep narrowing in this direction.
- "no" — rejected. Pivot to a different direction. Never re-ask
  something in the rejected list.
- "kinda" — warmer. You are close. Propose something similar, adjacent,
  or a refinement of what got the "kinda".
- "not_sure" — no information gained. Try a different axis entirely.

EXPLORE vs EXPLOIT — the core dialogue philosophy:
Every turn is a balance of two moves, like a reinforcement-learning policy:
- EXPLOIT what this PASS has revealed: the CURRENT GAME's history, plus
  any free-form seed context supplied up front (which may, at the
  caregiver's explicit opt-in, include context carried in from earlier
  passes of this same round), plus any caregiver-configured patient
  profile (persistent, patient-specific priors). Bias toward the region
  these signals point at.
- EXPLORE an under-sampled dimension. Insert at least one question on
  a facet you have not probed yet, to avoid getting stuck in a local
  optimum that looked promising but was not the real target.
- Rule of thumb: after 2-3 turns exploiting one direction, ask an
  exploratory question on a different axis before exploiting further.
- Each pass is independent by default. Do NOT assume the patient's
  need this pass matches an earlier pass's outcome — if earlier
  context is relevant, it will appear explicitly in the seed context
  block below.
- Never echo a known subject back as a guess. If seed context or a
  category identifies WHAT the subject is, your job is to learn what
  is still unclear ABOUT that subject — not to confirm the subject.

GAME PACING:
- Mix questions and guesses. A reasonable rhythm is 2-4 narrowing
  questions, then a guess, then adapt.
- After two "kinda" answers in a row, MAKE A GUESS based on the
  neighborhood — do not keep narrowing.
- If turn count is at the final turn, action MUST be "guess" — your
  best guess from all evidence.

SAFETY RULES:
- Never give medical advice, diagnoses, or dosages.
- Never include URLs, HTML, markdown, or emoji.
- Never repeat content already in the history.
"""


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no turns yet)"
    lines: list[str] = []
    for i, t in enumerate(history, start=1):
        lines.append(
            f"{i}. [{t['action']}] \"{t['content']}\" -> {t['answer']}"
        )
    return "\n".join(lines)


def game_reason_messages(
    category_label: str,
    history: list[dict],
    turn: int,
    max_turns: int,
    *,
    final: bool = False,
    seed_context: str = "",
    category_hint: str = "",
) -> list[LLMMessage]:
    """Ask the LLM for the next 20Q action given the game state.

    `history` is a list of `{"action", "content", "answer"}` dicts in
    turn order. `seed_context` is optional free-form text supplied by the
    user (e.g. from the "Other" category prompt) describing what they
    want to communicate. When present, the LLM should use it to guide
    early guesses rather than asking redundant narrowing questions.
    `category_hint` is optional category-scoped reasoning guidance
    attached to the starting taxonomy node.
    """
    instruction = (
        f"Starting category: {category_label}\n"
        f"Turn {turn} of {max_turns}\n\n"
    )
    if category_hint:
        instruction += (
            "CATEGORY-SCOPED REASONING GUIDANCE (applies to this entire "
            "game):\n"
            f"{category_hint.strip()}\n\n"
        )
    if seed_context:
        instruction += (
            "The user provided this free-form context up front:\n"
            f'  "{seed_context}"\n\n'
            "TREAT THIS CONTEXT AS THE SUBJECT, NOT THE ANSWER. The subject "
            "is already known - do NOT guess the subject back at the user "
            "and do NOT ask whether they want the subject. Your job is to "
            "learn what is still unclear ABOUT the subject so you can help "
            "them communicate the specific thing they need or feel.\n\n"
            "Before each turn, ask yourself: \"I know the subject. What do "
            "I still not understand?\" Then ask a narrowing question about "
            "the unknown dimension - for example the feeling, concern, "
            "request, desired outcome, specific person, time, or object tied "
            "to the subject. A guess is only appropriate once a concrete "
            "specific beyond the subject itself has been narrowed down.\n\n"
            "Anti-examples (do NOT do this):\n"
            '  - context "wants to talk to son about something specific" -> '
            'guess "Talk to your son?" (this just echoes the subject)\n'
            "Good patterns:\n"
            '  - question "Is it about their health?"\n'
            '  - question "Do you want to share good news?"\n'
            '  - question "Are you worried about something they are doing?"\n\n'
        )
    instruction += f"History so far:\n{_format_history(history)}\n\n"
    if final:
        instruction += (
            "THIS IS THE FINAL TURN. action MUST be \"guess\" — your best "
            "guess from all the evidence above."
        )
    else:
        instruction += "Propose your next question or guess as strict JSON."
    return [
        {"role": "system", "content": GAME_SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]


def summarize_game_messages(
    category_label: str,
    final_content: str,
    history: list[dict],
    *,
    seed_context: str = "",
) -> list[LLMMessage]:
    context_line = (
        f'\nFree-form context the user provided up front: "{seed_context}"'
        if seed_context
        else ""
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"A patient played a short 20-questions game starting in "
                f'the category "{category_label}". The agent\'s final '
                f'confirmed guess was: "{final_content}".'
                f"{context_line}\n\n"
                f"Game history:\n{_format_history(history)}\n\n"
                "Write ONE short sentence the caregiver should see, starting "
                'with "They" and describing what the patient is communicating. '
                "No medical advice."
            ),
        },
    ]
