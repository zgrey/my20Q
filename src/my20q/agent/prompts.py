"""System prompt and templating for the reasoning-mode LLM.

In the retooled engine the LLM has one job: drive a round. Each turn it
either proposes a yes/no `query` or a `synthesis` — a candidate utterance
in the patient's voice. See docs/design/beta-retool.md §7.
"""

from __future__ import annotations

from my20q.llm.base import LLMMessage

GAME_SYSTEM_PROMPT = """\
You help a caregiver and a person with aphasia communicate. Inside ONE
round, under ONE high-level topic, you ask short questions to work out
what the person needs to say — then you synthesize it as a short
sentence in their voice.

OUTPUT FORMAT — STRICT JSON, nothing else:
{"action": "query" | "synthesis", "content": "...", "rationale": "..."}

- "query" — a single yes/no narrowing question.
- "synthesis" — the candidate utterance: what the person is trying to
  say, as ONE complete sentence in the FIRST PERSON ("I would like…",
  "I feel…"). This is the round's output; the caregiver confirms it
  with the person before it is spoken.
- "content" — the query or utterance shown in the cockpit. Phrase it as
  a clear, complete, natural sentence — roughly 8-16 words. Not a
  clipped fragment ("Your son?"), not a paragraph. Use concrete,
  everyday words; a little extra phrasing helps it land clearly.
- A QUERY MUST BE A SINGLE YES/NO QUESTION. The caregiver can only
  answer yes, no, kinda, or not sure — there are no other buttons.
  NEVER ask an either/or or multiple-choice question ("Is it inside or
  outside?", "Is it A, B, or C?"). Pick ONE option and ask it as a
  plain yes/no ("Is it inside the house?"); a later query can probe
  the other option.
- "rationale" — one short sentence for the caregiver's reasoning panel;
  never spoken to the patient.

STAY ON TOPIC. Every query and the synthesis must stay within the
round's topic. Drift to a closely related facet only briefly, and only
when it genuinely helps narrow the need.

HOW TO READ THE ANSWERS (in history):
- "yes" — affirmed; keep narrowing in this direction.
- "no" — rejected; pivot. Never re-ask something already rejected.
- "kinda" — warm; you are close. Refine around what got the "kinda".
- "not_sure" — no information; try a different facet.
- A "[caregiver context]" entry is free-form steering the caregiver
  typed in mid-round — weight it heavily.

EXPLORE vs EXPLOIT — the core dialogue philosophy:
Each query balances two moves, like a reinforcement-learning policy:
- EXPLOIT what this round has revealed — its history, the caregiver
  context, the seed context, and the patient profile.
- EXPLORE an under-sampled facet every 2-3 queries, even when the
  exploit signal looks coherent, to avoid a false local optimum.
- Never echo a known subject back as a synthesis. If the topic or seed
  context already says WHAT the subject is, narrow what is unclear
  ABOUT it.

PACING:
- Mix queries and an eventual synthesis. A reasonable rhythm is 2-5
  narrowing queries, then synthesize, then adapt if it is rejected.
- After two "kinda" answers in a row, SYNTHESIZE from the neighbourhood.
- When told the query budget is reached, action MUST be "synthesis".

SAFETY:
- Never give medical advice, diagnoses, or dosages.
- Never include URLs, HTML, markdown, or emoji.
- Never repeat content already in the history.
"""


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no queries yet)"
    lines: list[str] = []
    for i, h in enumerate(history, start=1):
        if h["kind"] == "context":
            lines.append(f'{i}. [caregiver context] "{h["text"]}"')
        else:
            lines.append(f'{i}. [{h["kind"]}] "{h["text"]}" -> {h.get("answer")}')
    return "\n".join(lines)


def reason_messages(
    topic_label: str,
    history: list[dict],
    query_index: int,
    max_queries: int,
    *,
    final: bool = False,
    seed_context: str = "",
    profile_context: str = "",
    topic_hint: str = "",
    emotional_state: dict | None = None,
    corrections: list[str] | None = None,
) -> list[LLMMessage]:
    """Build the messages asking the LLM for the next round action.

    `history` is the round's ordered list of `{"kind", "text", "answer"}`
    dicts — `kind` is "query", "synthesis", or "context". `seed_context`
    is optional caregiver free-form text supplied up front;
    `profile_context` is the persistent patient profile; `topic_hint` is
    the topic's reasoning guidance.
    """
    instruction = (
        f"Topic for this round: {topic_label}\n"
        f"Query {query_index} of up to {max_queries}\n\n"
    )
    if topic_hint:
        instruction += (
            "TOPIC-SCOPED REASONING GUIDANCE (applies to this whole round):\n"
            f"{topic_hint.strip()}\n\n"
        )
    if profile_context:
        instruction += (
            "PATIENT PROFILE (persistent, caregiver-managed context — use as "
            "a prior on HOW to ask, never as the answer itself):\n"
            f"  {profile_context}\n\n"
        )
    if emotional_state:
        readings = []
        for pair_id, value in emotional_state.items():
            poles = pair_id.split("_", 1)
            if len(poles) == 2 and isinstance(value, int | float):
                readings.append(f"  - {poles[0]} ↔ {poles[1]}: {float(value):+.2f}")
        if readings:
            instruction += (
                "CAREGIVER'S EMOTIONAL READING of the patient right now "
                "(-1 = the first word, +1 = the second, 0 = neutral):\n"
                + "\n".join(readings)
                + "\nLet this colour the tone and focus of your questions — it "
                "is context, not the answer.\n\n"
            )
    if seed_context:
        instruction += (
            "SEED CONTEXT the caregiver supplied up front:\n"
            f'  "{seed_context}"\n\n'
            "Treat this as the SUBJECT, not the answer. Do not echo it back "
            "as a synthesis — narrow what is still unclear about it.\n\n"
        )
    instruction += f"History so far:\n{_format_history(history)}\n\n"
    if corrections:
        joined = "\n".join(f"  - {c}" for c in corrections)
        instruction += (
            "YOUR PREVIOUS QUERY ATTEMPT WAS REJECTED BY THE AUDITOR:\n"
            f"{joined}\n"
            "Produce a corrected query — ONE plain yes/no question.\n\n"
        )
    if final:
        instruction += (
            'QUERY BUDGET REACHED. action MUST be "synthesis" — your best '
            "synthesis of what the person is trying to say, from all the "
            "evidence above."
        )
    else:
        instruction += 'Propose your next "query" or a "synthesis" as strict JSON.'
    return [
        {"role": "system", "content": GAME_SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]
