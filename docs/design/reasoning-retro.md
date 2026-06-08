# Reasoning architecture — live-trial retrospective (2026-06-02)

A record of what we built, what live trials on cerberus showed, and the decision
to simplify the working line. Nothing here is lost — Stages 1–3 are preserved on
the `augmented-reasoning` branch.

## What we built (in stages)

1. **Hypothesis belief controller** (`37f99f5`) — a flat belief over candidate
   needs; each turn asks the most *discriminating* yes/no question; the cockpit
   shows the live belief (the "honest tile"). Fixed the "asks what's *wrong*,
   never what you *want*" drift. **~1 LLM call per question.** *Keep.*
2. **Context expansion** (`d4955c0`) — a caregiver note adds/boosts candidate
   needs as high-trust evidence. *Keep.*
3. **Reason→format decouple — "Stage 1"** (`b05bf42`) — split the ask into
   *deliberate* (free reasoning) + *format* (strict JSON), to let thinking
   models (gemma4) participate without the JSON being starved.
   **2 LLM calls per question.**
4. **Augmented hierarchical zoom + trace — "Stage 2/3"** (`7318e4e`) — a toggle:
   on take-root, zoom into finer sub-needs and descend a grammatical ladder
   (need → object → modifier); `effort = 1 + depth` deliberate→critique→refine
   passes; a reasoning trace surfacing the explicit passes *and* a summary of a
   thinking model's opaque reasoning. **Many LLM calls per question.**

## What live trials showed

- **Question generation is too slow.** Augmented mode makes `(1+depth)`
  deliberate+critique passes, plus a format call, plus zoom and a
  thinking-summary call — several round-trips per question. With a thinking
  model (gemma4) each call is already slow, so turns ran tens of seconds to
  minutes and frequently **degraded to fallback at the timeout**. In practice
  this makes the gemma4 models obsolete for live use.
- **"Zoom" did not demonstrably help** — it narrows along the ladder, but the
  latency cost was not justified by any clear quality gain; at times it felt
  worse.
- **TTS unstable** — voice activates only occasionally. Likely a *separate*
  issue (the TTS path itself was unchanged) but aggravated by slow turns, since
  TTS fires per question and questions became rare/slow. **Needs its own
  investigation** (candidates: piper subprocess flakiness, a frontend
  speak/stop race, autoplay gating).

## Cost — LLM calls per question

| Stage | calls / question |
|---|---|
| original single-shot reasoner | ~1 |
| hypothesis belief controller | ~1 |
| + Stage 1 (reason→format) | 2 |
| + augmented (Stage 2/3), depth `d` | ~`(1+d)·2` + zoom + summary |

## Decision

- **Preserve** Stages 1–3 on the **`augmented-reasoning`** branch for a future,
  cheaper revisit.
- **Roll the working line back to the single-call hypothesis belief controller**
  (`dc2e951`): fast, and keeps the honest tile, context expansion, and the
  cockpit work (model selector, conversation thinking indicator), plus the
  remote serve script. Drop the two-phase split and augmented zoom from main.
- **Investigate TTS instability separately** — it is not part of the reasoning
  architecture.

## If/when we revisit augmented reasoning

Make it cheaper *before* it can earn a place in the main line:
- cache the seed; cap or skip critique passes; drop the per-turn thinking
  summary (compute lazily / on demand);
- only zoom when it *measurably* improves convergence;
- prefer a fast model;
- and **prove a quality win against the single-call baseline** before paying any
  latency for it.

## Re-integration log

Features are being lifted back off `augmented-reasoning` one at a time, on
`reasoning-conditional-decouple`, each made cheaper before it lands — per the
conditions above.

### 1. Conditional reason→format decouple (2026-06-05)

The Stage-1 decouple (`b05bf42`) was originally **always-on**, which doubled the
baseline to 2 calls/question — the cost the rollback rejected. Re-integrated it
**gated on model capability** instead:

- The ask is two-phase (**deliberate** → free-form reasoning, no JSON pressure;
  **format** → cheap thinking-off JSON) **only when the active model declares
  the `thinking` capability** (probed once per model via Ollama `/api/show`,
  cached). gemma4 takes this path.
- Every other model (gemma3:12b, the fallback line) keeps the **single fast
  call** — the rolled-back baseline is untouched.
- Mechanism: `chat()` gains a per-call `think` override across the `LLMBackend`
  protocol + Ollama/Anthropic/mock; `OllamaBackend.is_thinking_model()` does the
  probe; `Reasoner.ask()` branches on it. `prompts.ask_messages` (single-call)
  is kept alongside the new `deliberate_messages`/`format_question_messages`.

This satisfies "make it cheaper first" (no baseline regression) and "prefer a
fast model" (the slow path is opt-in by model choice). Verified live: gemma3:12b
→ 1 call; gemma4:e4b → two-phase, valid discriminating question (~33s for the
two calls). Tests in `tests/test_reasoner_decouple.py`.

**Not yet re-integrated** (still only on `augmented-reasoning`): the augmented
hierarchical zoom + critique passes (`7318e4e`), the reasoning-trace UI, and the
thinking-summary call. Each must still prove a quality win before it lands.

### 2. Seed + ask/synthesis-policy overhaul (2026-06-07)

A second live trial (recorded as `patient_data/.../ed3da...jsonl`) was "circular,
less on the issue, unwilling to go deep on emotion." Reading the transcript: the
seeds were good (the fix below worked), but the **question/synthesis policy**
squandered them. Fixes, in order of impact:

- **Topic-aware seeding** (`f7d30f5`): the seed prompt hard-mandated the universal
  physical wants under *every* topic, so "My feelings" seeded 6/10 physical needs.
  Now `Topic.seed_universal_wants` (False for feelings/people); the mandate moved
  out of the static prompt into `seed_messages`; cross-topic drift forbidden.
- **Hard-elimination of rejected syntheses** (B): a rejected synthesis only
  soft-down-weighted the need, so a later generic "yes" revived it and the round
  re-proposed the *same* utterance ~7× until the budget killed it. Now
  `_replay_belief` drops a rejected need entirely (can't lead or be targeted).
- **Anti-redundancy** (A): `Reasoner.ask` re-prompts when a question's `yes_ids`
  re-slice a recent one (Jaccard ≥ 0.8) — stops re-asking the same axis.
- **Depth / no meta-questions** (C): banned "do you want to share how you feel?"
  (everyone says yes → zero signal); the feelings hint drives name → cause →
  intensity.
- **Anchoring on "yes" content** (hardcoded, owner's hypothesis):
  `hypotheses.anchor_focus` restricts the candidate set to already-affirmed needs
  once any are confirmed, so questions *structurally* drill into the confirmed
  cluster instead of drifting. Not prompt steering — the dropped needs are not
  candidates.
- **No question budget** (owner's call): the "20" was a name, not a method.
  `max_queries` default is now **0 = unlimited**; it is a pure safety ceiling that
  *stops* a round, never forces a synthesis. Synthesis is readiness-driven
  (`should_synthesize`) only. A round may ask many questions and never synthesize
  — the questioning is the point.

Verified on a clean simulated round against the real profile: general → specific
feeling → specific person → utterance in **6 queries** (vs the 20-query abandoned
loop), with the anchor visibly drilling within the affirmed cluster. The real
trial with noisy caregiver input is the true test.

### 3. Anchoring trap, no-progress terminator, fair thinking-model eval (2026-06-07)

A max-effort audit across all topics exposed three things:

- **The anchoring trap.** Anchoring + a *soft* "no" let a confirmed cluster the
  patient kept rejecting trap a round forever (physical_health: 9 queries, 196s,
  no result). Fix: a definitive "no" to a **single-need** question now ELIMINATES
  it (`_replay_belief`), like a rejected synthesis; the anchor auto-releases when
  the cluster empties. Multi-need "no" stays soft.
- **No-progress terminator** (replaces the removed budget): a round ends —
  gracefully, no forced utterance — when, over `STALL_QUERIES` (6), the leader
  gains no confidence AND the live set does not shrink. Catches an oscillating
  stall, not just a stuck leader. This is the principled "ask only while making
  progress" rule the budget removal needed.
- **Thinking models were judged unfairly.** The earlier "gemma4 is bad" rested on
  a token cap: the deliberate phase's `num_predict` capped thinking+content
  together, so a verbose thinker was truncated mid-thought and emitted nothing,
  and we discarded the thinking. Not a quality finding — a crippling. Fixes:
  `_chat_json` forces `think=False` for structured calls (seed/format/synth);
  the deliberate phase is **uncapped** (`num_predict=-1`, latency bounded by the
  timeout); `OllamaBackend.chat` salvages `message.thinking` when content is
  empty. Now thinking models run to completion.

**Fair re-audit result.** gemma3:12b: **4/4 topics converged in 3–5 queries,
12–16s each**, trap gone, profile-grounded utterances (singing, Rob's heart, the
martini). gemma4:26b with thinking fully uncapped: converged well, comparable
utterance quality, but **~3–8× slower** with **no demonstrable questioning
advantage**. Conclusion (now fair): thinking models *work*, but do not beat the
gemma3:12b workhorse here — default stays gemma3:12b, thinking models available
and no longer crippled, so the choice is informed. A cockpit thinking-trace (from
`augmented-reasoning`) would be the right tool to evaluate them further.
