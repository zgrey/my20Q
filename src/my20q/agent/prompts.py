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
- Stay ANCHORED to this topic. Cover several DISTINCT facets OF THIS TOPIC — do
  not pile into one near-duplicate cluster. When topic guidance is given below,
  spread across the facets it names; do NOT drift into other topics' territory
  (e.g. no body complaints under a feelings or people topic).
- GROUND them in the PATIENT PROFILE when one is given — use the person's actual
  people, routines, and known concerns rather than generic placeholders.
- Concrete, everyday words. Make them mutually DISTINCT so a yes/no question
  can tell them apart, and broad enough that the real need is likely among them.
- No medical advice, diagnoses, or dosages. No URLs, markup, or emoji.
"""

# Injected into the seed instruction only for topics with
# ``seed_universal_wants`` (body / catch-all). Off-topic for feelings/people.
_UNIVERSAL_WANTS = (
    "ALSO include the most common everyday physical needs, phrased naturally, "
    "EVEN IF the topic seems narrow: being thirsty / wanting a drink, being "
    "hungry / wanting food, needing the toilet, being in pain, and being too hot "
    "or too cold. A basic want like thirst is easy to miss — do not skip it."
)

ASK_SYSTEM = """\
You help narrow down what a person with aphasia needs. You are given the current
CANDIDATE needs (each with an id). Ask the ONE yes/no question that best SPLITS
them — ideally about half of them would answer "yes".

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "yes_ids": ["h2","h5"], "preface": "...", "rationale": "..."}

- "question": ONE plain yes/no question, ~8-16 everyday words. The caregiver can
  only answer yes, no, kinda, or not sure — so NEVER an either/or or
  multiple-choice question ("Is it A or B?"). Pick one idea and ask it plainly.
  Separate the candidates along a real dimension — a different feeling, a
  different cause, or a different person — moving from general toward the
  specific as they narrow. Once it is clear WHICH need, drill INTO it.
- "yes_ids": exactly the candidate ids whose need would answer YES to your
  question. It MUST be a non-empty STRICT subset (some yes AND some no) — that
  split is what makes the question informative.
- "preface": a SHORT spoken lead-in (<=12 words) read to the person right before
  the question — warm, plain, varies each turn, gives gentle context, never just
  restates the question.
- "rationale": one short sentence for the caregiver's panel; never spoken.

DRILL IN, DON'T CIRCLE. Each question must open a NEW dimension or narrow toward
the specific need — never re-ask a prior question in different words, and never
re-slice the same group of candidates.
BANNED — vague meta-questions about whether the person WANTS to talk, share,
express, tell someone, or "let people know" how they feel. Everyone answers yes,
so they reveal nothing. Ask the SUBSTANCE instead: which feeling, how strong, and
what or who it is about.
Weight any [caregiver context] heavily. No medical advice, URLs, markup, or emoji.
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
that separate the candidates along a real dimension — a different feeling, a
different cause, or a different person. Once it is clear WHICH need, drill INTO it.

Think it through, then state the ONE question you will ask. It must be a single
plain yes/no question the caregiver can answer yes / no / kinda / not sure —
NEVER an either/or or multiple-choice question.
DRILL IN, DON'T CIRCLE: open a NEW dimension or narrow toward the specific need;
never re-ask a prior question in different words or re-slice the same group.
BANNED — vague meta-questions about whether they WANT to talk / share / express /
tell someone how they feel (everyone says yes). Ask the SUBSTANCE: which feeling,
how strong, what or who it is about.
Weight any [caregiver context] heavily. No medical advice.
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

CLARIFY_SYSTEM = """\
A person with aphasia has nearly settled on ONE need (given below). Before we put
words to it we need to be SURE — confirm it and sharpen it. Ask the ONE yes/no
question that pins down a specific, NEW detail of THIS need: its cause, the person
it involves, the place/time, or how strong it is.

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "preface": "...", "rationale": "..."}

- "question": ONE plain yes/no question, ~8-16 everyday words, about THIS need —
  NEVER an either/or, and NEVER a vague "do you want to talk about it" (that adds
  nothing). It must add a detail not already asked in the history.
- "preface": a SHORT spoken lead-in (<=12 words) — warm, never just a restatement.
- "rationale": one short sentence for the caregiver's panel; never spoken.
No medical advice, URLs, markup, or emoji.
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
    include_universal_wants: bool = True,
) -> list[LLMMessage]:
    """Ask the LLM for the round's candidate-need set (the belief prior).

    ``include_universal_wants`` mirrors the topic's ``seed_universal_wants``: when
    False (feelings, people, ...) the generic physical wants are NOT injected, so
    they don't crowd out topic-appropriate, profile-grounded candidates.
    """
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    if include_universal_wants:
        instruction += _UNIVERSAL_WANTS + "\n\n"
    instruction += (
        "List the candidate needs as strict JSON, anchored to the topic, "
        "concrete and mutually distinct, grounded in the profile when given."
    )
    return [
        {"role": "system", "content": SEED_SYSTEM},
        {"role": "user", "content": instruction},
    ]


#: Injected when the candidate set has been hard-restricted to already-affirmed
#: needs (see hypotheses.anchor_focus) — tells the model these are confirmed and
#: the job is to drill in, not re-open the field.
_ANCHOR_NOTE = (
    "ALREADY CONFIRMED — every candidate below is something the person has "
    "answered YES to. Do NOT introduce anything new: your question must clarify "
    "WHICH of these it is, or a specific aspect of it (its cause, the person "
    "involved, or how strong it is).\n\n"
)


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
    anchored: bool = False,
) -> list[LLMMessage]:
    """Ask for the next discriminating yes/no question over ``candidates``.

    ``candidates`` is ``(id, need, weight)`` for the live hypotheses. ``anchored``
    marks that the set is restricted to already-affirmed needs (drill-in mode).
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
    if anchored:
        instruction += _ANCHOR_NOTE
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
    anchored: bool = False,
) -> list[LLMMessage]:
    """Free-form reasoning to choose the next yes/no question (no JSON).

    Phase 1 of the two-phase ask used only for thinking models; the structured
    output comes from a separate :func:`format_question_messages` call.
    ``candidates`` is ``(id, need, weight)`` for the live hypotheses. ``anchored``
    marks that the set is restricted to already-affirmed needs (drill-in mode).
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
    if anchored:
        instruction += _ANCHOR_NOTE
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


def clarify_messages(
    topic_label: str,
    leading_need: str,
    history: list[dict],
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
    corrections: list[str] | None = None,
) -> list[LLMMessage]:
    """Ask one confirming/sharpening yes/no question about the leading need.

    The deepen phase: a frontrunner has emerged but not enough yeses to synthesize,
    so we gather positive confirmation and refine the detail before wording it.
    """
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += (
        f"The person has nearly settled on this need:\n  \"{leading_need}\"\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
    )
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += (
            "YOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
            f"{joined}\n"
            "Ask a corrected yes/no question about this need.\n\n"
        )
    instruction += (
        "Ask ONE yes/no question that confirms or sharpens a NEW detail of this "
        "need, as strict JSON."
    )
    return [
        {"role": "system", "content": CLARIFY_SYSTEM},
        {"role": "user", "content": instruction},
    ]
