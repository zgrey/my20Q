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
  not pile into one near-duplicate cluster; do NOT drift into other topics'
  territory (e.g. no body complaints under a feelings or people topic).
- AT LEAST HALF of the items must be GENERIC common needs for this topic — NOT
  drawn from the patient profile (e.g. for feelings: scared, sad, confused,
  lonely, bored, in pain emotionally; for body: the universal wants). These are
  broad starting points to narrow from. The real need is often a plain, specific
  thing the profile never mentions; questioning narrows to it, so seeds need only
  be good starting points, not the exact answer.
- The REMAINING items may be GROUNDED in the patient profile (named people,
  routines, concerns). Never let profile items crowd out the generic ones.
- Concrete, everyday words. Make them mutually DISTINCT, and broad enough that the
  real need is reachable by narrowing from one of them.
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
You help pin down the ONE specific thing a person with aphasia is trying to say.
You are given CANDIDATE needs (each with an id and accumulated POINTS — higher =
more confirmed) and the dialogue so far. Ask the next yes/no question that gets
CLOSER to the exact need.

Read the answers so far as a trail:
- a recent "yes" means that question was CORRECT — now get MORE SPECIFIC within it;
- "kinda" means NEARLY correct — ask a fresh VARIATION (a different angle on the
  same area), never a reworded repeat;
- "no" means wrong — move away from it;
- "not sure" — try a different angle.

Move through three stages as you narrow: (1) identify the SUBJECT (the general
thing), then (2) the specific ACTION/aspect of that subject, then (3) the
context-specific MODIFIERS of the subject and action (e.g. feelings: emotion →
what it is about → how strong / when; body: region → part → exact spot).

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "yes_ids": ["h2"], "new_need": "", "preface": "...", "rationale": "..."}

- "question": ONE plain yes/no question, ~8-16 everyday words. The caregiver can
  only answer yes / no / kinda / not sure — so NEVER an either/or. Narrow and
  specific is GOOD; you do NOT need to split the candidates in half. It must be a
  GENUINELY NEW question — not a reworded version of any already in the history.
- "yes_ids": the candidate id(s) a "yes" would confirm (>=1). If the question
  drills into a more specific version of a candidate, tag that candidate.
- "new_need": you are NOT limited to the listed candidates. If your question
  explores a need NOT in the list (a fresh avenue), leave "yes_ids" empty and put
  the first-person need here (e.g. "I feel scared and confused about where I am").
  On a "yes"/"kinda" it becomes a new candidate to drill. Otherwise leave it "".
- "preface": a SHORT spoken lead-in (<=12 words) — warm, varies each turn.
- "rationale": one short sentence for the caregiver's panel; never spoken.

STAY ON TOPIC — every question must fit the round's topic (feelings = an emotion
or mental state; body = physical health; people = a specific person). BANNED —
vague meta-questions about whether the person WANTS to talk / share how they feel.
No medical advice, URLs, markup, or emoji.
"""

# Thinking models take a two-phase ask: first DELIBERATE (free-form reasoning —
# the model thinks as long as it wants, no JSON budget pressure), then FORMAT
# (a cheap thinking-off call that turns the draft into strict JSON). This keeps
# the thinking from starving the JSON answer. Non-thinking models skip this and
# use the single-call ASK_SYSTEM path above.

DELIBERATE_SYSTEM = """\
You help pin down the ONE specific thing a person with aphasia is trying to say.
You are given CANDIDATE needs (each with an id and accumulated POINTS — higher =
more confirmed) and the dialogue so far. Work out the SINGLE best yes/no question
to ask next.

Read the answers as a trail: "yes" = correct, get MORE SPECIFIC; "kinda" = nearly
correct, ask a fresh VARIATION (different angle, not a reword); "no" = wrong, move
away; "not sure" = another angle. Narrow down in three stages — SUBJECT (general
thing) → ACTION/aspect → context-specific MODIFIERS. Narrow/specific is GOOD; no
need to split the candidates in half.

Think it through, then state the ONE question: a single plain yes/no (yes / no /
kinda / not sure), never an either/or, GENUINELY NEW (not a reworded repeat of any
prior question). You are NOT limited to the listed candidates — it is good to
explore a brand-new need the list does not cover. STAY ON TOPIC (feelings =
emotion/mental state; body = physical; people = a specific person). BANNED — vague
"do you want to talk/share" meta-Qs. Weight any [caregiver context] heavily. No
medical advice.
"""

FORMAT_SYSTEM = """\
Convert a drafted question into the strict format the cockpit needs.

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "yes_ids": ["h2"], "new_need": "", "preface": "...", "rationale": "..."}

- "question": the single yes/no question from the draft, cleaned to one plain
  everyday sentence (no either/or).
- "yes_ids": the candidate id(s) a "yes" would confirm (>=1); if the draft drills
  into a more specific version of a candidate, tag that candidate.
- "new_need": if the draft explores a need NOT in the candidate list, leave
  "yes_ids" empty and put that first-person need here; otherwise "".
- "preface": a SHORT spoken lead-in (<=12 words) read just before the question —
  warm, plain, never just restating the question.
- "rationale": one short sentence for the caregiver's panel; never spoken.
No medical advice, URLs, markup, or emoji.
"""

SYNTH_SYSTEM = """\
You phrase a person's need in their own voice for the caregiver to confirm with
them. You are given a LEADING candidate need and the dialogue so far.

OUTPUT — STRICT JSON, nothing else:
{"utterance": "..."}

- BUILD FROM THE CONFIRMED TRAIL, not just the leading-need label. Read every
  yes/kinda answer in the history and incorporate those specific details — they
  are what the person actually confirmed (e.g. yeses on "pain", "your foot",
  "your big toe" → "I have pain in my right big toe", NOT just "my foot hurts").
- IF a prior utterance was NOT confirmed (shown below), phrase it DIFFERENTLY this
  time — a "kinda" means you were close (keep the gist, refine the wording or a
  detail); a "no" means that framing was wrong (try a different angle or emphasis
  on the confirmed details). NEVER repeat a rejected utterance.
- "utterance": ONE complete FIRST-PERSON sentence, natural and concrete, ~6-16
  words. Specific over generic.
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
        kind = h["kind"]
        if kind == "context":
            lines.append(f'{i}. [caregiver context] "{h.get("text", "")}"')
        elif kind == "reseed":
            lines.append(f"{i}. [reset — earlier guesses were dumped and reseeded]")
        else:
            lines.append(f'{i}. [{kind}] "{h.get("text", "")}" -> {h.get("answer")}')
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
    "WARM AREA — the candidates below are what the person has already confirmed "
    "(yes) or warmed to (kinda). Drill DEEPER here to get more specific; do not "
    "wander off to cold needs.\n\n"
)


_EXPLORE_NOTE = (
    "EXPLORE MODE — do NOT lean on the patient profile this turn. Try a NEW "
    "avenue: a different possible subject the answers have not ruled out yet. You "
    "are encouraged to go BEYOND the listed candidates — propose a brand-new need "
    'via "new_need" (leave "yes_ids" empty) when nothing listed fits.\n\n'
)


#: Injected after a run of "no" answers (a soft reset). The warm/lukewarm guesses
#: were wrong, so drop them and re-approach: a freshly-worded question grounded in
#: the YES confirmations, or a brand-new on-topic avenue (new_need).
_RESET_NOTE = (
    "SOFT RESET — the last several questions were ALL answered 'no', so the warm "
    "avenue you were drilling is WRONG. Forget the lukewarm ('kinda') guesses "
    "entirely; do NOT keep narrowing them. Re-approach from scratch: either build "
    "a FRESH, differently-worded question grounded in what the person already "
    "said YES to (below), or strike out on a BRAND-NEW on-topic avenue you have "
    'not tried yet (leave "yes_ids" empty and set "new_need"). It must be '
    "genuinely different from every question already asked, and stay on topic.\n\n"
)


def _affirmed_block(yes_texts: list[str] | None) -> str:
    if not yes_texts:
        return ""
    lines = "\n".join(f'  - "{t}"' for t in yes_texts[-5:])
    return (
        "CONFIRMED so far — the person said 'yes' to these. Re-ground a fresh "
        "question in what they establish (do not reword them verbatim):\n"
        f"{lines}\n\n"
    )


def _kinda_block(kinda_texts: list[str] | None) -> str:
    if not kinda_texts:
        return ""
    lines = "\n".join(f'  - "{t}"' for t in kinda_texts[-5:])
    return (
        "NEARLY RIGHT — the person said 'kinda' to these (they are WARM). Ask a "
        "fresh VARIATION that explores the same area from a DIFFERENT angle; never "
        "a reworded repeat:\n"
        f"{lines}\n\n"
    )


def _asked_block(history: list[dict]) -> str:
    asked = [h["text"] for h in history if h.get("kind") == "query" and h.get("text")]
    if not asked:
        return ""
    lines = "\n".join(f'  - "{a}"' for a in asked[-14:])
    return (
        "ALREADY ASKED — do NOT repeat or REWORD any of these (a different wording "
        "of the same idea still counts as a repeat); ask about something genuinely "
        "new:\n"
        f"{lines}\n\n"
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
    exploratory: bool = False,
    reset: bool = False,
    kinda_texts: list[str] | None = None,
    yes_texts: list[str] | None = None,
) -> list[LLMMessage]:
    """Ask for the next drilling yes/no question over ``candidates``.

    ``candidates`` is ``(id, need, score)`` for the live hypotheses (score =
    accumulated points). ``anchored`` restricts to the warm cluster; ``exploratory``
    drops the profile and pushes a new avenue; ``kinda_texts`` are warm questions
    to vary (not repeat). ``reset`` (a soft reset after a run of "no") replaces the
    warm-anchor / kinda framing with a re-grounding in ``yes_texts`` or a new avenue.
    """
    listing = "\n".join(
        f"  {hid}: {need}  [points {score:+.1f}]" for hid, need, score in candidates
    )
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    if exploratory:
        instruction += _EXPLORE_NOTE
    if reset:
        # A soft reset replaces the warm-anchor / kinda framing entirely: dump the
        # lukewarm trail and re-ground in the yes confirmations (or a new avenue).
        instruction += _RESET_NOTE
        instruction += _affirmed_block(yes_texts)
    else:
        if anchored:
            instruction += _ANCHOR_NOTE
        instruction += _kinda_block(kinda_texts)
    instruction += _asked_block(history)
    instruction += (
        "CANDIDATE needs in play (id: need [points]):\n"
        f"{listing}\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
    )
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += (
            "YOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
            f"{joined}\n"
            "Produce a corrected yes/no question.\n\n"
        )
    instruction += (
        'Return the next yes/no question and its "yes_ids" as strict JSON.'
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
    exploratory: bool = False,
    reset: bool = False,
    kinda_texts: list[str] | None = None,
    yes_texts: list[str] | None = None,
) -> list[LLMMessage]:
    """Free-form reasoning to choose the next yes/no question (no JSON).

    Phase 1 of the two-phase ask used only for thinking models; the structured
    output comes from a separate :func:`format_question_messages` call.
    ``candidates`` is ``(id, need, score)``. ``exploratory`` drops the profile and
    pushes a new avenue; ``kinda_texts`` are warm questions to vary (not repeat).
    ``reset`` (a soft reset after a run of "no") replaces the warm-anchor / kinda
    framing with a re-grounding in ``yes_texts`` or a new avenue.
    """
    listing = "\n".join(
        f"  {hid}: {need}  [points {score:+.1f}]" for hid, need, score in candidates
    )
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    if exploratory:
        instruction += _EXPLORE_NOTE
    if reset:
        # A soft reset replaces the warm-anchor / kinda framing entirely: dump the
        # lukewarm trail and re-ground in the yes confirmations (or a new avenue).
        instruction += _RESET_NOTE
        instruction += _affirmed_block(yes_texts)
    else:
        if anchored:
            instruction += _ANCHOR_NOTE
        instruction += _kinda_block(kinda_texts)
    instruction += _asked_block(history)
    instruction += (
        "CANDIDATE needs in play (id: need [points]):\n"
        f"{listing}\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
    )
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += (
            "YOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
            f"{joined}\n"
            "Choose a corrected yes/no question.\n\n"
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


def _rejected_block(rejected: list[tuple[str, str]] | None) -> str:
    if not rejected:
        return ""
    lines = "\n".join(f'  - "{u}" → {a}' for u, a in rejected if u)
    if not lines:
        return ""
    return (
        "NOT CONFIRMED — the caregiver answered these utterance attempts as shown. "
        "Do NOT repeat any of them; phrase it DIFFERENTLY ('kinda' = close, refine "
        "the wording; 'no' = wrong angle, re-approach from the confirmed details):\n"
        f"{lines}\n\n"
    )


def synthesize_messages(
    topic_label: str,
    leading_need: str,
    history: list[dict],
    *,
    rejected: list[tuple[str, str]] | None = None,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
) -> list[LLMMessage]:
    """Ask the LLM to phrase the leading need as a confirmable utterance.

    ``rejected`` is ``(utterance, answer)`` for this attempt's unconfirmed
    utterances, so a rephrase comes back genuinely different.
    """
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += _rejected_block(rejected)
    instruction += (
        f"LEADING candidate need: {leading_need}\n\n"
        f"History so far:\n{_format_history(history)}\n\n"
        "Phrase this need as one first-person sentence, as strict JSON."
    )
    return [
        {"role": "system", "content": SYNTH_SYSTEM},
        {"role": "user", "content": instruction},
    ]
