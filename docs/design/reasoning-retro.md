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
