"""System prompts and templating for the reasoning-mode LLM.

The engine wraps the LLM in a 5W1H facet controller (see ``agent/facets.py``
and ``agent/dialogue.py``). The LLM does *language*, code does *control*, via
four focused calls:

- ``seed_messages``      — starter contender values for the six facet slots.
- ``deliberate_messages``/``format_question_messages`` — the two-phase ask:
  reason out the next yes/no question for ONE focus slot, then format it into
  strict JSON whose ``slots`` tag only what the question actually says.
- ``synthesize_messages``— weave the per-slot leaders into one natural
  first-person utterance (placeholders for unknown slots, never a slot dump).
- ``expand_messages``    — facet values implied by a caregiver note.
- ``flip_messages``      — the pending question re-rendered in its opposite
  connotation (the caregiver's opposition button).

See docs/design/beta-retool.md §7 and docs/design/reasoning-retro.md §8.
"""

from __future__ import annotations

from my20q.agent import facets
from my20q.llm.base import LLMMessage

SEED_SYSTEM = """\
You help a caregiver and a person with aphasia figure out what the person is
trying to express RIGHT NOW. Under ONE topic, propose starting GUESSES for the
six slots of the need:

- "who":   the OTHER person involved — helper, visitor, someone to contact.
- "what":  the subject or object (a drink, a picture, a phone call, a feeling).
- "when":  the timing, when it matters (now, today, soon, after lunch).
- "where": the place, when it matters (my room, the kitchen, their house).
- "why":   the motivation (thirsty, redecorating, missing them, worried).
- "how":   the action wanted (bring it, move it, call them, sit with me).

OUTPUT — STRICT JSON, nothing else:
{"who": ["..."], "what": ["..."], "when": [], "where": [], "why": ["..."], "how": ["..."]}

- 2 to 5 values per slot; SHORT plain phrases (1-4 everyday words). "when" and
  "where" may be empty lists when timing/place rarely matter for this topic.
- These are broad starting points to narrow from, not exact answers. Cover
  DISTINCT possibilities; stay anchored to this topic.
- AT LEAST HALF the values must be GENERIC common possibilities for this topic
  — NOT drawn from the patient profile. The rest may be GROUNDED in the
  profile (named people, routines, concerns).
- No medical advice, diagnoses, or dosages. No URLs, markup, or emoji.
"""

# Injected into the seed instruction only for topics with
# ``seed_universal_wants`` (body / catch-all). Off-topic for feelings/people.
_UNIVERSAL_WANTS = (
    'ALSO cover the most common everyday physical wants in "what"/"why", EVEN '
    "IF the topic seems narrow: a drink (thirsty), food (hungry), the toilet, "
    "pain somewhere, and being too hot or too cold. A basic want like thirst "
    "is easy to miss — do not skip it."
)

# The ask is two-phase for EVERY model: first DELIBERATE (free-form reasoning —
# the model thinks as long as it wants, no JSON budget pressure), then FORMAT
# (a cheap thinking-off call that turns the draft into strict JSON).

DELIBERATE_SYSTEM = """\
You help pin down the ONE specific thing a person with aphasia is trying to
say. The need is tracked as six slots — who / what / when / where / why / how —
each with scored CONTENDER values (higher points = more confirmed by the
person's answers; negative = answered away). You are given the board, the
dialogue so far, and ONE FOCUS SLOT with a directive. Work out the SINGLE best
yes/no question to serve that directive:

- probe: test the most plausible contender of the focus slot — or a fresh
  on-topic value the board is missing, if the listed ones look wrong.
- split: the top two contenders are tied — ask about ONE of them so the answer
  separates the two (never an either/or question).
- drill: the leader is confirmed but vague — ask a MORE SPECIFIC version of it.

Read the answers as a trail: "yes" = correct, get MORE SPECIFIC; "kinda" =
nearly right, try a fresh VARIATION from a different angle; "no" = wrong, move
away; "not sure" = ask another way. One subject per question, in plain
everyday words the person can answer yes / no / kinda / not sure. The question
must say the value it is testing OUT LOUD (the person hears only the question,
never the board). It must be GENUINELY NEW — not a reword of any prior
question. STAY ON TOPIC. BANNED: vague "do you want to talk/share" meta-Qs.
Weight any [caregiver context] heavily. No medical advice.
"""

FORMAT_SYSTEM = """\
Convert a drafted question into the strict format the cockpit needs.

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "slots": {"what": "a picture"}, "preface": "", "rationale": "..."}

- "question": the single yes/no question from the draft, cleaned to one plain
  everyday sentence (no either/or, no reasoning language).
- "slots": what the question ASSERTS, as 1-2 entries mapping a slot to the
  value the question names. The slots are: "who" = the OTHER person involved;
  "what" = the thing or object — a NOUN (an action like "help with tasks" is
  NEVER "what"); "when" = timing; "where" = place; "why" = the motivation;
  "how" = the ACTION wanted (a verb phrase: "move it", "clean the kitchen").
  Every value MUST be words the question itself says (e.g. "Do you need Zach
  to move it?" → {"who": "Zach", "how": "move it"}). NEVER tag a value the
  question does not mention. Reuse the exact wording of a listed contender
  when the question is about it.
- "refines": OPTIONAL — when an asserted value is a MORE SPECIFIC version of
  a contender already on the board, name that broader contender VERBATIM
  (testing "tingling" under the confirmed "discomfort" →
  {"refines": {"what": "discomfort"}}). The board shows known refinements as
  "parent›value". Omit when the value is not a refinement.
- "preface": OPTIONAL short spoken lead-in, at most 8 words, ENDING with an em
  dash, that flows grammatically into the question when read aloud as one
  sentence (e.g. "Okay, not food then —"). It must not reuse the question's
  words and must not be a question. Use "" when nothing natural fits — empty
  beats awkward.
- "rationale": one short sentence for the caregiver's panel, in PLAIN everyday
  language — never mention slots, boards, contenders, or drafts; never spoken.
No medical advice, URLs, markup, or emoji.
"""

SYNTH_SYSTEM = """\
You phrase a person's need in their own voice for the caregiver to confirm
with them. You are given the need's slots — who / what / when / where / why /
how — with the leading CONFIRMED value of each (some slots are unknown), plus
the dialogue trail.

OUTPUT — STRICT JSON, nothing else:
{"utterance": "..."}

- ONE natural, complete FIRST-PERSON sentence (~6-16 words) that WEAVES the
  confirmed slot values together — a real sentence a person would say, NEVER a
  slot-by-slot list or a categorical dump.
- Use every confirmed slot where it fits naturally; for UNKNOWN slots either
  leave them out or use a plain placeholder word (someone, something, soon,
  here) — never invent a specific detail the person did not confirm.
- BUILD FROM THE TRAIL: things answered "yes" are what the person actually
  confirmed — keep their specific words where natural.
- If prior utterances were NOT confirmed (listed), phrase it DIFFERENTLY this
  time — a "kinda" means close (keep the gist, refine the wording or one
  detail); a "no" means that framing was wrong (change the angle). NEVER
  repeat a rejected utterance.
- No medical advice, diagnoses, or dosages. No URLs, markup, or emoji.
"""

FLIP_SYSTEM = """\
The caregiver pressed the OPPOSITE button: the on-screen yes/no question points
the wrong way, and they want the SAME question asked again with its direction
or connotation REVERSED — not a new question.

OUTPUT — STRICT JSON, nothing else:
{"question": "...", "slots": {"who": "Rob"}}

How to flip:
- When the question involves the person AND someone else, swap WHO DOES the
  thing FOR WHOM: "Do you want to bring Rob a drink?" → "Do you want Rob to
  bring you a drink?". Telling someone ↔ asking/hearing from them.
- Otherwise reverse the question's key detail: "Is it too hot…?" → "Is it too
  cold…?", "…right now?" → "…later?".
- KEEP everything else the same — same people, same subject words, same plain
  everyday tone. ONE yes/no question; no either/or; no new guesses; never a
  bare negation ("Do you NOT want…").
- "slots": 1-2 entries naming what the FLIPPED question asserts ("who" =
  the other person, "what" = the thing, "when"/"where"/"why", "how" = the
  action). Every value MUST be words the flipped question itself says.
No medical advice, URLs, markup, or emoji.
"""

VERIFY_SYSTEM = """\
The engine needs to DOUBLE-CHECK one detail it believes is settled — a
single answer locked it in, and answers can be noisy. Re-ask that ONE detail
plainly so the person can confirm or correct it.

OUTPUT — STRICT JSON, nothing else:
{"question": "..."}

- ONE plain yes/no question that asks the detail directly and SAYS the value
  out loud (e.g. who = "Rob" → "Is it Rob you want to talk to?").
- It SHOULD restate what was asked before — this is a deliberate
  double-check, not a new question. Short, everyday words; no either/or; no
  reasoning language.
- NEVER describe the KIND of detail you are checking. Words like "the
  subject", "the thing", "the place", "the timing", "the reason", "the
  action", "the value" or "you want to discuss" are the engine's own
  vocabulary — the person hears this question read aloud and those words mean
  nothing to them. Name the value in ordinary speech instead.
No medical advice, URLs, markup, or emoji.
"""

#: What each slot means — GUIDANCE for the model, never words to reuse in the
#: question. Handing these to the model as a label got them read aloud to the
#: patient verbatim ("Is the subject you want to discuss tingling?", "Is the
#: place you want is the right side?"), so each now ships with a natural
#: example of what the question should sound like instead. See §1d C3.
_SLOT_PHRASE = {
    "who": "a person",
    "what": "a thing or subject",
    "when": "a time",
    "where": "a place",
    "why": "a reason",
    "how": "an action wanted",
}

#: One natural phrasing per slot, so the model has a shape to copy.
_SLOT_EXAMPLE = {
    "who": 'Is it Rob you want to talk to?',
    "what": 'Do you mean the dishes?',
    "when": 'Do you mean this afternoon?',
    "where": 'Is it in the kitchen?',
    "why": 'Is it because you are cold?',
    "how": 'Do you want him to move it?',
}


def verify_messages(
    category: str,
    value: str,
    *,
    corrections: list[str] | None = None,
) -> list[LLMMessage]:
    """Ask for a double-check question for one locked (category, value) pair.

    The verify turn's one-shot call — tiny on purpose (no board, no history):
    it re-asks a single settled detail, so the only inputs are the pair, the
    kind of detail it is, and an example of how that sounds in plain speech.
    """
    kind = _SLOT_PHRASE.get(category, category)
    example = _SLOT_EXAMPLE.get(category)
    instruction = f'DETAIL TO DOUBLE-CHECK — this is {kind}:\n  "{value}"\n\n'
    if example:
        instruction += (
            f"Ask about it the way this example asks about its own value:\n"
            f"  {example}\n"
            f'Say "{value}" out loud in the question. Do NOT say '
            f'"{kind}" or any other description of the KIND of detail.\n\n'
        )
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += f"YOUR PREVIOUS ATTEMPT WAS REJECTED:\n{joined}\n\n"
    instruction += "Return the double-check question as strict JSON."
    return [
        {"role": "system", "content": VERIFY_SYSTEM},
        {"role": "user", "content": instruction},
    ]


EXPAND_SYSTEM = """\
A caregiver or medical professional just added a NOTE about what the person
with aphasia needs. Their note is HIGH-TRUST — far more reliable than any
guess. Extract what it implies for the six slots of the need.

OUTPUT — STRICT JSON, nothing else:
{"slots": {"who": ["Zach"], "how": ["move it", "lift it"]}}

- Keys are among who/what/when/where/why/how; each maps to the value(s) the
  note states — short plain phrases (1-4 words) TAKEN FROM THE NOTE'S OWN
  WORDS.
- NEVER emit a value the note does not say, even when a similar value is
  already on the board. A note reading "right side paralysis" does NOT support
  "pain"; a note reading "Pain in right calf" says "right calf", not "right
  side". Substituting a board value for the caregiver's own wording is the
  worst thing you can do here — it is trusted far above any guess, and it
  overwrites what they actually reported.
- Prefer the note's PRECISE wording over a broader one. "right calf" beats
  "leg"; the specific is the whole reason the note was written.
- {"slots": {}} only if the note truly states nothing for any slot. Saying
  nothing is much better than saying something the note does not.
- No medical advice, diagnoses, or dosages. No URLs, markup, or emoji.
"""


def _format_history(history: list[dict]) -> str:
    """The dialogue as the model sees it — honoring context restarts.

    A RESTART dumps the noise: everything before the latest restart marker is
    hidden EXCEPT caregiver-context entries and queries answered "yes" (the
    high-trust signal the restart deliberately keeps). Entries after the
    marker show in full.
    """
    if not history:
        return "(nothing asked yet)"
    last_restart = -1
    for i, h in enumerate(history):
        if h.get("kind") == "restart":
            last_restart = i
    lines: list[str] = []
    for i, h in enumerate(history):
        kind = h.get("kind")
        dumped = i < last_restart
        if kind == "context":
            lines.append(f'- [caregiver context] "{h.get("text", "")}"')
        elif kind == "edit":
            lines.append(
                f'- [caregiver edit] "{h.get("text", "")}" — struck from the '
                "evolving proposal"
            )
        elif kind == "restart":
            lines.append(
                "- [restart — earlier wrong guesses were dumped; the confirmed "
                "answers above were kept]"
            )
        elif dumped:
            if kind == "query" and h.get("answer") == "yes":
                lines.append(f'- [query] "{h.get("text", "")}" -> yes')
            # dumped no/kinda/not_sure and old syntheses are withheld on purpose
        elif kind in ("query", "synthesis"):
            lines.append(f'- [{kind}] "{h.get("text", "")}" -> {h.get("answer")}')
    return "\n".join(lines) if lines else "(nothing asked yet)"


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


def _board_block(
    board: facets.Board, *, scores: bool = True, edges: facets.Edges | None = None
) -> str:
    """The live board, one line per category: ``what: a picture +2.0 | a gift 0.0``.

    With `edges`, refinements render as ``parent›value`` so the model sees
    the dive structure and can tag (and extend) it.
    """
    lines: list[str] = []
    for cat in facets.CATEGORIES:
        ranked = facets.live(board, cat)[: facets.MAX_LISTED]
        cat_edges = (edges or {}).get(cat, {})

        def name(v: str, _edges: dict = cat_edges) -> str:
            parent = _edges.get(v)
            return f"{parent}›{v}" if parent else v

        if ranked:
            if scores:
                vals = " | ".join(f"{name(v)} [{s:+.1f}]" for v, s in ranked)
            else:
                vals = " | ".join(name(v) for v, _ in ranked)
        else:
            vals = "(no contenders yet)"
        lines.append(f"  {cat}: {vals}")
    return "\n".join(lines)


def seed_messages(
    topic_label: str,
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
    include_universal_wants: bool = True,
) -> list[LLMMessage]:
    """Ask the LLM for starter contender values across the six facet slots.

    ``include_universal_wants`` mirrors the topic's ``seed_universal_wants``:
    when False (feelings, people, ...) the generic physical wants are NOT
    injected, so they don't crowd out topic-appropriate candidates.
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
        "Propose the starting slot values as strict JSON, anchored to the "
        "topic, concrete and mutually distinct, profile-grounded only where "
        "the profile was given."
    )
    return [
        {"role": "system", "content": SEED_SYSTEM},
        {"role": "user", "content": instruction},
    ]


_DIRECTIVE_NOTE = {
    "probe": (
        "DIRECTIVE — PROBE the focus slot: test its most plausible contender, "
        "or a fresh on-topic value the board is missing if the listed ones "
        "look wrong. When nothing in the slot is confirmed yet, prefer testing "
        "a BROAD kind of value before a specific instance."
    ),
    "split": (
        "DIRECTIVE — SPLIT the tie: the two contenders below have matching "
        "scores. Ask about ONE of them (a plain yes/no, not an either/or) so "
        "the answer separates them."
    ),
    "drill": (
        "DIRECTIVE — DRILL the leader: it is confirmed but still vague. Ask a "
        "MORE SPECIFIC version of it (a concrete instance, detail, or "
        "narrower form). Known refinements show as parent›value on the board "
        "— go DEEPER than the finest confirmed one."
    ),
    "pin": (
        "DIRECTIVE — PIN DOWN the focus slot: the last proposed message was "
        "CLOSE but not confirmed, and this slot is its weakest detail. Ask a "
        "specific question that nails the slot's exact value (a concrete "
        "instance — NOT a reword of the message, NOT a re-ask of details the "
        "person already confirmed)."
    ),
}

_EXPLORE_NOTE = (
    "EXPLORE — do NOT lean on the patient profile this turn, and prefer a "
    "BRAND-NEW plausible value for the focus slot over the listed ones.\n\n"
)

#: Injected when one contender has soaked up a run of "no" guesses — the
#: enumeration trap (remind→dinner→memory→appointment→medicine→…).
_EXHAUSTED_NOTE = (
    'EXHAUSTED AVENUE — "{value}" has had several guesses in a row, all '
    'answered "no". Do NOT build this question around "{value}" again; test a '
    "genuinely different contender or kind of {cat}.\n\n"
)

#: Injected when the caregiver struck values from the proposal (✗-edits).
_VETOED_NOTE = (
    "RULED OUT — the caregiver explicitly struck these from the proposal; "
    "never ask about them again:\n{lines}\n\n"
)

#: Injected when the who-leader is a known caregiver and no direction is
#: established. Caregivers offer care as tasks — test that direction FIRST,
#: but never assume it (concern ABOUT a caregiver is also real).
_CAREGIVER_NOTE = (
    'NOTE — "{who}" is the patient\'s caregiver. A need involving a caregiver '
    "is MOST OFTEN asking them to do a care task FOR the patient, so test "
    "that direction first (does the patient need {who} to do or help with "
    "something?). Do NOT assume it — genuine concern ABOUT {who} is also "
    "possible; one clear answer settles the direction.\n\n"
)


def _asked_block(asked: list[str]) -> str:
    if not asked:
        return ""
    lines = "\n".join(f'  - "{a}"' for a in asked[-14:])
    return (
        "ALREADY ASKED — do NOT repeat or REWORD any of these (a different "
        "wording of the same idea still counts as a repeat); ask about "
        "something genuinely new:\n"
        f"{lines}\n\n"
    )


def deliberate_messages(
    topic_label: str,
    board: facets.Board,
    history: list[dict],
    *,
    focus: str,
    directive: str,
    split_pair: tuple[str, str] | None = None,
    edges: facets.Edges | None = None,
    asked: list[str] | None = None,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
    corrections: list[str] | None = None,
    exploratory: bool = False,
    banned: tuple[str, str] | None = None,
    vetoed: set[tuple[str, str]] | None = None,
    caregiver_hint: str = "",
) -> list[LLMMessage]:
    """Free-form reasoning to choose the next yes/no question (no JSON).

    Phase 1 of the two-phase ask. ``focus`` is the slot the controller chose
    to advance; ``directive`` is one of probe/split/drill/pin; ``split_pair``
    carries the two tied values for a split. ``exploratory`` drops the profile
    and pushes a fresh value. ``banned`` is an (category, value) the futility
    guard has cut off this turn; ``caregiver_hint`` names a who-leader who is
    a known caregiver (test the care-task direction first).
    """
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context="" if exploratory else profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    if exploratory:
        instruction += _EXPLORE_NOTE
    if caregiver_hint:
        instruction += _CAREGIVER_NOTE.format(who=caregiver_hint)
    if banned is not None:
        instruction += _EXHAUSTED_NOTE.format(value=banned[1], cat=banned[0])
    if vetoed:
        lines = "\n".join(f'  - {cat}: "{val}"' for cat, val in sorted(vetoed))
        instruction += _VETOED_NOTE.format(lines=lines)
    instruction += (
        f"THE BOARD (slot: contenders [points]; parent›value = a refinement):\n"
        f"{_board_block(board, edges=edges)}\n\n"
        f"FOCUS SLOT: {focus}\n"
        f"{_DIRECTIVE_NOTE.get(directive, _DIRECTIVE_NOTE['probe'])}\n"
    )
    if split_pair is not None:
        instruction += f'Tied contenders to separate: "{split_pair[0]}" vs "{split_pair[1]}"\n'
    instruction += "\n"
    instruction += _asked_block(asked or [])
    instruction += f"Dialogue so far:\n{_format_history(history)}\n\n"
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += (
            "YOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
            f"{joined}\n"
            "Choose a corrected, genuinely different yes/no question.\n\n"
        )
    instruction += (
        "Reason it through, then state the single best yes/no question to ask next."
    )
    return [
        {"role": "system", "content": DELIBERATE_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def format_question_messages(
    board: facets.Board, focus: str, draft: str
) -> list[LLMMessage]:
    """Turn a free-form deliberation/draft into the strict question JSON."""
    instruction = (
        f"Live contenders per slot:\n{_board_block(board, scores=False)}\n\n"
        f"Focus slot: {focus}\n\n"
        f"Drafted reasoning / question:\n{draft}\n\n"
        "Return the cockpit JSON for the final yes/no question."
    )
    return [
        {"role": "system", "content": FORMAT_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def flip_messages(
    question: str,
    *,
    direction_note: str = "",
    corrections: list[str] | None = None,
) -> list[LLMMessage]:
    """Ask for the pending question re-rendered in its opposite connotation.

    The opposition button's one-shot call — deliberately tiny (no board, no
    history) so the flip feels like a trigger click, not a reasoning turn.
    ``direction_note`` carries the bucket mirror when the original question
    classified into a direction (the unambiguous flip target).
    """
    instruction = f'QUESTION ON SCREEN:\n  "{question}"\n\n'
    if direction_note:
        instruction += direction_note + "\n\n"
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += f"YOUR PREVIOUS ATTEMPT WAS REJECTED:\n{joined}\n\n"
    instruction += "Return the flipped question as strict JSON."
    return [
        {"role": "system", "content": FLIP_SYSTEM},
        {"role": "user", "content": instruction},
    ]


def expand_messages(
    topic_label: str,
    context: str,
    board: facets.Board,
    history: list[dict],
    *,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
) -> list[LLMMessage]:
    """Ask for the facet values a caregiver note implies or confirms."""
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += (
        f'CAREGIVER\'S NEW NOTE:\n  "{context}"\n\n'
        f"Current board (slot: contenders):\n{_board_block(board, scores=False)}\n\n"
        f"Dialogue so far:\n{_format_history(history)}\n\n"
        "Return the slot values the note implies or confirms, as strict JSON."
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
        "NOT CONFIRMED — the caregiver answered these utterance attempts as "
        "shown. Do NOT repeat any of them; phrase it DIFFERENTLY ('kinda' = "
        "close, refine the wording; 'no' = wrong angle, re-approach from the "
        "confirmed details):\n"
        f"{lines}\n\n"
    )


def synthesize_messages(
    topic_label: str,
    leaders: dict[str, str],
    history: list[dict],
    *,
    rejected: list[tuple[str, str]] | None = None,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
) -> list[LLMMessage]:
    """Ask the LLM to weave the slot leaders into one confirmable utterance.

    ``leaders`` maps each facet category to its confirmed leading value;
    categories absent from the map are UNKNOWN and must be placeholdered or
    omitted, never invented.
    """
    slot_lines = []
    for cat in facets.CATEGORIES:
        value = leaders.get(cat)
        slot_lines.append(f"  {cat}: {value if value else '(unknown)'}")
    instruction = f"Topic for this round: {topic_label}\n\n"
    instruction += _context_block(
        profile_context=profile_context,
        seed_context=seed_context,
        topic_hint=topic_hint,
        emotional_state=emotional_state,
    )
    instruction += _rejected_block(rejected)
    instruction += (
        "CONFIRMED SLOT LEADERS:\n" + "\n".join(slot_lines) + "\n\n"
        f"Dialogue so far:\n{_format_history(history)}\n\n"
        "Weave the known slots into one natural first-person sentence, as "
        "strict JSON."
    )
    return [
        {"role": "system", "content": SYNTH_SYSTEM},
        {"role": "user", "content": instruction},
    ]
