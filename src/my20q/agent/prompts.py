"""System prompts and templating for the reasoning-mode LLM.

The retooled engine wraps the LLM in a hypothesis controller (see
``agent/hypotheses.py`` and ``agent/dialogue.py``). The LLM does *language*,
code does *control*, via three focused calls:

- ``seed_messages``      — list candidate needs to test (the belief's prior).
- ``ask_messages``       — the single most-discriminating yes/no question.
- ``synthesize_messages``— phrase the leading need as a first-person utterance.

See docs/design/beta-retool.md §7.
"""

from __future__ import annotations

from my20q.llm.base import LLMMessage

SEED_SYSTEM = """\
You help a caregiver and a person with aphasia communicate. Under ONE topic,
list the distinct things the person might be trying to express RIGHT NOW — a
spread of candidate NEEDS to test. These are hypotheses, NOT questions.

OUTPUT — STRICT JSON, nothing else:
{"hypotheses": ["I'm thirsty and want a drink", "My foot hurts", ...]}

- 6 to 10 items, each ONE short FIRST-PERSON need.
- ALWAYS include the most common everyday needs, phrased naturally, EVEN IF the
  topic seems narrow: being thirsty / wanting a drink, being hungry / wanting
  food, needing the toilet, being in pain, and being too hot or too cold. A
  basic want like thirst is easy to miss under a "body" topic — do not skip it.
- THEN add items specific to the topic, spread across kinds — do not pile into
  one: WANT (an object, to move), FEEL (lonely, scared, frustrated, tired),
  WRONG/body (nausea, dizziness, weakness), PEOPLE (see or contact someone).
- Concrete, everyday words. Make them mutually DISTINCT so a yes/no question
  can tell them apart, and broad enough that the real need is likely among them.
- No medical advice, diagnoses, or dosages. No URLs, markup, or emoji.
"""

ASK_SYSTEM = """\
You help narrow down what a person with aphasia needs. You are given the current
CANDIDATE needs (each with an id). Ask the ONE yes/no question that best SPLITS
them — ideally about half of them would answer "yes".

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "yes_ids": ["h2","h5"], "preface": "...", "rationale": "..."}

- "question": ONE plain yes/no question, ~8-16 everyday words. The caregiver can
  only answer yes, no, kinda, or not sure — so NEVER an either/or or
  multiple-choice question ("Is it A or B?"). Pick one idea and ask it plainly.
  Prefer questions that separate WANTS from FEELINGS from BODY problems from
  PEOPLE, rather than drilling deeper into one need.
- "yes_ids": exactly the candidate ids whose need would answer YES to your
  question. It MUST be a non-empty STRICT subset (some yes AND some no) — that
  split is what makes the question informative.
- "preface": a SHORT spoken lead-in (<=12 words) read to the person right before
  the question — warm, plain, varies each turn, gives gentle context, never just
  restates the question.
- "rationale": one short sentence for the caregiver's panel; never spoken.

Never repeat a question already in the history. Weight any [caregiver context]
heavily. No medical advice, URLs, markup, or emoji.
"""

# Thinking models take a two-phase ask: first DELIBERATE (free-form reasoning —
# the model thinks as long as it wants, no JSON budget pressure), then FORMAT
# (a cheap thinking-off call that turns the draft into strict JSON). This keeps
# the thinking from starving the JSON answer. Non-thinking models skip this and
# use the single-call ASK_SYSTEM path above.

DELIBERATE_SYSTEM = """\
You help a caregiver narrow down what a person with aphasia needs. You are given
the current CANDIDATE needs (each with an id and a confidence) and the dialogue
so far. Work out the SINGLE best yes/no question to ask next.

A good question SPLITS the candidates — ideally about half would answer "yes" —
and moves from the general toward the specific as the candidates narrow
(topic → subject/action → the specific thing → its modifiers). Favour questions
that separate WANTS from FEELINGS from BODY problems from PEOPLE.

Think it through, then state the ONE question you will ask. It must be a single
plain yes/no question the caregiver can answer yes / no / kinda / not sure —
NEVER an either/or or multiple-choice question. Do not repeat a question already
asked. Weight any [caregiver context] heavily. No medical advice.
"""

FORMAT_SYSTEM = """\
Convert a drafted question into the strict format the cockpit needs.

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "yes_ids": ["h2","h5"], "preface": "...", "rationale": "..."}

- "question": the single yes/no question from the draft, cleaned to one plain
  everyday sentence (no either/or).
- "yes_ids": exactly the candidate ids whose need would answer YES to it — a
  non-empty STRICT subset (some yes AND some no).
- "preface": a SHORT spoken lead-in (<=12 words) read just before the question —
  warm, plain, never just restating the question.
- "rationale": one short sentence for the caregiver's panel; never spoken.
No medical advice, URLs, markup, or emoji.
"""

SYNTH_SYSTEM = """\
You phrase a person's need in their own voice for the caregiver to confirm with
them. Given the LEADING candidate need and the dialogue so far:

OUTPUT — STRICT JSON, nothing else:
{"utterance": "..."}

- "utterance": ONE complete FIRST-PERSON sentence, natural and concrete, ~6-16
  words ("I would like a glass of water.", "I feel lonely and would like someone
  to sit with me."). Build on the leading need and what the answers confirmed.
- No medical advice, diagnoses, or dosages. No URLs, markup, or emoji.
"""


EXPAND_SYSTEM = """\
A caregiver or medical professional just added a NOTE about what the person with
aphasia needs. Their note is HIGH-TRUST context — far more reliable than any
guess. Use it two ways at once.

OUTPUT — STRICT JSON, nothing else:
{"hypotheses": ["I'm thirsty and want a drink", ...], "boost_ids": ["h1", ...]}

- "hypotheses": any genuinely NEW candidate needs the note implies that are NOT
  already in the current list. Each ONE short FIRST-PERSON need, concrete and
  everyday. Empty list if the note implies nothing new.
- "boost_ids": the ids of EXISTING candidates the note points to / makes more
  likely (e.g. the note "reaching for her water cup" confirms an existing
  "I'm thirsty" candidate). Empty list if none apply.
- At least one of the two should usually be non-empty — the note is meaningful.
- No medical advice, diagnoses, or dosages. No URLs, markup, or emoji.
"""


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(nothing asked yet)"
    lines: list[str] = []
    for i, h in enumerate(history, start=1):
        if h["kind"] == "context":
            lines.append(f'{i}. [caregiver context] "{h["text"]}"')
        else:
            lines.append(f'{i}. [{h["kind"]}] "{h["text"]}" -> {h.get("answer")}')
    return "\n".join(lines)


def _context_block(
    *,
    profile_context: str,
    seed_context: str,
    topic_hint: str,
    emotional_state: dict | None,
) -> str:
    """Shared preamble: topic guidance, profile, emotion, and seed context.

    These inform *how* to ask — never the answer itself.
    """
    out = ""
    if topic_hint:
        out += (
            "TOPIC-SCOPED GUIDANCE (applies to this whole round):\n"
            f"{topic_hint.strip()}\n\n"
        )
    if profile_context:
        out += (
            "PATIENT PROFILE (persistent caregiver context — a prior on HOW to "
            "ask, never the answer):\n"
            f"  {profile_context}\n\n"
        )
    if emotional_state:
        readings = []
        for pair_id, value in emotional_state.items():
            poles = pair_id.split("_", 1)
            if len(poles) == 2 and isinstance(value, int | float):
                readings.append(f"  - {poles[0]} <-> {poles[1]}: {float(value):+.2f}")
        if readings:
            out += (
                "CAREGIVER'S EMOTIONAL READING (-1 = first word, +1 = second, "
                "0 = neutral):\n" + "\n".join(readings) + "\n\n"
            )
    if seed_context:
        out += (
            "SEED CONTEXT the caregiver supplied up front:\n"
            f'  "{seed_context}"\n\n'
        )
    return out


def seed_messages(
    topic_label: str,
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
) -> list[LLMMessage]:
    """Ask the LLM for the round's candidate-need set (the belief prior)."""
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += (
        "List the candidate needs as strict JSON. Spread them across "
        "want / feel / body / people, concrete and mutually distinct."
    )
    return [
        {"role": "system", "content": SEED_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def ask_messages(
    topic_label: str,
    candidates: list[tuple[str, str, float]],
    history: list[dict],
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
    corrections: list[str] | None = None,
) -> list[LLMMessage]:
    """Ask for the next discriminating yes/no question over ``candidates``.

    ``candidates`` is ``(id, need, weight)`` for the live hypotheses.
    """
    listing = "\n".join(
        f"  {hid}: {need}  [confidence {weight:.2f}]" for hid, need, weight in candidates
    )
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += (
        "CANDIDATE needs still in play (id: need [confidence]):\n"
        f"{listing}\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
    )
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += (
            "YOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
            f"{joined}\n"
            "Produce a corrected question that splits the candidates.\n\n"
        )
    instruction += (
        'Return the single best yes/no question and its "yes_ids" as strict JSON.'
    )
    return [
        {"role": "system", "content": ASK_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def deliberate_messages(
    topic_label: str,
    candidates: list[tuple[str, str, float]],
    history: list[dict],
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
    corrections: list[str] | None = None,
) -> list[LLMMessage]:
    """Free-form reasoning to choose the next yes/no question (no JSON).

    Phase 1 of the two-phase ask used only for thinking models; the structured
    output comes from a separate :func:`format_question_messages` call.
    ``candidates`` is ``(id, need, weight)`` for the live hypotheses.
    """
    listing = "\n".join(
        f"  {hid}: {need}  [confidence {weight:.2f}]" for hid, need, weight in candidates
    )
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += (
        "CANDIDATE needs still in play (id: need [confidence]):\n"
        f"{listing}\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
    )
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += (
            "YOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
            f"{joined}\n"
            "Choose a corrected question that splits the candidates.\n\n"
        )
    instruction += (
        "Reason it through, then give the single best yes/no question to ask next."
    )
    return [
        {"role": "system", "content": DELIBERATE_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def format_question_messages(
    candidates: list[tuple[str, str, float]], draft: str
) -> list[LLMMessage]:
    """Turn a free-form deliberation/draft into the strict question JSON."""
    listing = "\n".join(f"  {hid}: {need}" for hid, need, _ in candidates)
    instruction = (
        f"Candidate needs (id: need):\n{listing}\n\n"
        f"Drafted reasoning / question:\n{draft}\n\n"
        "Return the cockpit JSON for the final yes/no question."
    )
    return [
        {"role": "system", "content": FORMAT_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def expand_messages(
    topic_label: str,
    context: str,
    existing: list[tuple[str, str]],
    history: list[dict],
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
) -> list[LLMMessage]:
    """Ask for new + boosted candidate needs implied by a caregiver note.

    ``existing`` is ``(id, need)`` for the current candidates, so the model can
    name which ones the note confirms (``boost_ids``).
    """
    listing = "\n".join(f"  {hid}: {need}" for hid, need in existing) or "  (none yet)"
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += (
        f'CAREGIVER\'S NEW NOTE:\n  "{context}"\n\n'
        f"Current candidate needs (id: need):\n{listing}\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
        "Return new needs and/or the ids the note confirms, as strict JSON."
    )
    return [
        {"role": "system", "content": EXPAND_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def synthesize_messages(
    topic_label: str,
    leading_need: str,
    history: list[dict],
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
) -> list[LLMMessage]:
    """Ask the LLM to phrase the leading need as a confirmable utterance."""
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += (
        f"LEADING candidate need: {leading_need}\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
        "Phrase this need as one first-person sentence, as strict JSON."
    )
    return [
        {"role": "system", "content": SYNTH_SYSTEM},
        {"role": "user", "content": instruction},
    ]
