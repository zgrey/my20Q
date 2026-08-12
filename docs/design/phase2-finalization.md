# Phase 2 finalization — closing the beta and merging to `main`

Written 2026-08-11 after a two-month gap. Purpose: state exactly where the
`phase2-cockpit` branch stands, what remains before it becomes `main`, and
what is explicitly deferred.

---

## 1. Where we stand

**Branch.** `phase2-cockpit` is **62 commits ahead of `main`, 0 behind** (the
Starlette/CVE-2026-48710 bump was merged down 2026-08-11). The eventual merge
carries **87 files, +20,203 / −1,903** — `web/` arrives on `main` whole, for
the first time.

**Health — all green as of 2026-08-11:**

| Check | Result |
|---|---|
| `pytest` | 211 passed, 2 skipped |
| `ruff check .` | clean |
| `npm run typecheck` (web) | clean |

**Engine.** The v3 5W1H facet controller is complete. Implemented and live:
W1-A (rephrase→1), W1-B (focus v3: retire/widen/rotate-on-stall), W1-C (the
living proposal banner), W1-E (per-topic priorities, body-aware seeds), W1-F
(the synthesis editor), W2-F (verify-on-lock), W3-H (refinement links).

**Trial evidence.** The v3 stack works, and the improvement is large. The
06-13 session (the last substantive trial data) ran five rounds:

| Topic | Outcome | Queries |
|---|---|---|
| my_people | synthesized | 7 |
| my_people | synthesized | 4 |
| my_people | abandoned | 0 |
| mental_health | synthesized | 17 |
| mental_health | abandoned | 7 |

Against the 06-11 dishes round — **95 queries, 8 restarts, 7 synthesis
attempts** — this is the intended order-of-magnitude change. Zero restarts,
zero diagnostics, zero farming-yeses across the 06-13 rounds.

**Staleness.** The most recent recording (2026-08-07) is a **0-query
abandoned round** — a startup smoke test, no signal. Real trial data ends
2026-06-13. The engine has not been exercised in ~2 months.

**Known doc drift.** `docs/ROADMAP.md` still marks Phase 2 ✅ with a
verification line reading "51 passed" and describes the pre-banner
architecture. It predates the entire facet-controller rebuild and the banner
era. It is the single most misleading document in the repo right now.

---

## 2. What remains

Five items. Only the first four are candidates for gating the merge.

### F1 · A fresh trial round — *cheapest, do first*
Two months idle. One live session against the synthetic persona confirms
nothing rotted (model availability, TTS, the banner flow, Ollama on
`gemma4:e4b`). Any regression found here re-prioritizes everything below.
**Cost:** one sitting. **Risk if skipped:** merging an engine nobody has run
since June.

### F2 · W2-G — the noise bench *(PROPOSED, scripts-only)*
The validation instrument, and the most valuable remaining item. Every claim
about W1-B/C/E/F and W3-H currently rests on **single live trials**; the §1
autopsy was hand-made. W2-G automates it: ε-noise on the simulated answerer,
fixture targets replayed from recordings, and per-run metrics (queries to
converge, restarts, informative-yes ratio, focus histogram, wasted-query
estimate) with a `--compare` mode across engine revisions.
**Touches scripts only — zero engine risk.** It is also the precondition for
honestly moving anything from IMPLEMENTED to VALIDATED.

### F3 · W2-E — candidates + tag rescue + pronoun folding *(PROPOSED)*
The cheapest remaining engine win, and now on its **third sighting**: q93's
dishes yes, the right-leg yes in the body round, and the who-fragmentation
(Rob +25.5 alongside "him" +10). Decisive answers are still being thrown away
because the formatter tags already-established pairs. Best landed *after* F2
so the delta is measured rather than asserted.

### F4 · Documentation reconciliation
- Rewrite `ROADMAP.md` Phase 2 to the banner-era architecture and real
  verification numbers (211/2, not 51/1).
- Verify `beta-retool.md` §12 scope checklist against what actually shipped —
  the pictogram tile is **shelved**, not delivered as specced.
- `CLAUDE.md` is already banner-era and needs no change.

### F5 · Post-merge hygiene *(not a merge gate)*
The `httpx2` deprecation surfaced by Starlette 1.x: `starlette.testclient`
warns that using `httpx` is deprecated. Tests pass today. `httpx>=0.27` is a
**core runtime dependency**, so migrating is a real decision (add `httpx2`
as a test dep, or move wholesale), not a one-line bump.

---

## 3. Recommended merge bar

**F1 → F2 → F3 → F4 → merge.** Rationale: F1 is nearly free and derisks
everything; F2 is zero-risk to the engine and converts the whole convergence
queue from anecdote to measurement; F3 is the last cheap engine win and wants
F2 to exist first; F4 prevents `main` from landing with a roadmap that
describes a system that no longer exists.

**Merge mechanics.** `CLAUDE.md` specifies squash-merge PRs into `main`. A
62-commit branch carrying the entire cockpit is the one case where squashing
destroys real history — the convergence work is a documented sequence of
owner-iterated proposals, and the autopsies reference commits. **Recommend a
true merge commit here**, as an explicit, noted exception. Owner's call.

---

## 4. Explicitly deferred — not Phase 2, not merge gates

From the convergence queue: **W1-D** (focus directives — demoted; the ✗-flow
absorbed it), **W3-I** (mass-scaled confidence — gated on F2 existing),
**W4-J** (fatigue-aware stopping).

From `beta-retool.md` §15: emotion-weighted conditional sampling, recording
compaction, the on-topic LLM auditor, the emergency false-alarm metric,
metrics-over-time, and the **pictogram tile / image-generator decision**
(which reopens a locked decision and needs its own privacy review).

**Scheduling rounds** — the owner-requested scheduling-logistics / event-
planning mode. See [`scheduling-rounds.md`](scheduling-rounds.md). Post-merge,
and it wants F2 (the bench) to exist first so a `when`-centric topic can be
measured.
