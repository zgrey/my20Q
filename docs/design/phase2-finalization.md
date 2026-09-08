# Phase 2 finalization — closing the beta and merging to `main`

Written 2026-08-11 after a two-month gap. Purpose: state exactly where the
`phase2-cockpit` branch stands, what remains before it becomes `main`, and
what is explicitly deferred.

---

## 1. Where we stand

**Branch.** `phase2-cockpit` is **65 commits ahead of `main`, 0 behind** (the
Starlette/CVE-2026-48710 bump was merged down 2026-08-11). The eventual merge
carries **89 files, +20,473 / −1,903** — `web/` arrives on `main` whole, for
the first time. *(Counts refreshed 2026-09-08; the three added commits are the
08-11/08-12 planning docs.)*

**Health — all green, re-confirmed 2026-09-08:**

| Check | Result |
|---|---|
| `pytest` | 211 passed, 2 skipped |
| `ruff check .` | clean |
| `npm run typecheck` (web) | clean |

**Engine.** The v3 5W1H facet controller is complete. Implemented and live:
W1-A (rephrase→1), W1-B (focus v3: retire/widen/rotate-on-stall), W1-C (the
living proposal banner), W1-E (per-topic priorities, body-aware seeds), W1-F
(the synthesis editor), W2-F (verify-on-lock), W3-H (refinement links).

*Caveat added 09-08:* "live" means shipped and unit-tested, not validated in the
field. §1d found **W3-H has never once engaged in production** (`board.edges`
empty in all 8 recorded rounds, blocked upstream by W2-K) and **W2-F is
actively harmful as shipped** (see W2-L). Nothing in this list reaches
VALIDATED until the bench exists.

**Trial evidence.** The v3 stack works, and the improvement is large. The
06-13 session (the last trial *before the park*; superseded as "most recent" by
08-31/09-01 below) ran five rounds:

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

**Staleness — resolved 2026-09-08.** The engine *was* idle for two months, but
gate F1 has since been sat: two live sessions on **2026-08-31** and
**2026-09-01**, `gemma4:e4b` on local Ollama, 8 rounds and 45 answered
questions. Full autopsy: [`convergence-plan.md` §1d](convergence-plan.md).

| Session | Result |
|---|---|
| 08-31 | `mental_health` **synthesized in 18 q** via a genuine banner ✓, job_b **9.556**, 0 diagnostics, 1 `stalled` restart |
| 09-01 | `physical_health` abandoned at 7 q; `physical_health` abandoned at the **20-q cap** with 3 fail-loop restarts, 2 diagnostic cards, 2 typed caregiver rescues |

F1 did exactly what it was for: it found a regression the June rounds could not
have exposed, because June never ran a laterality-heavy body round. The banner
era is sound — given an honestly-locked board, the caregiver accepts the weave.
The failure is upstream: **the engine cannot retain the specific content of a
"yes."** *toes*, *right leg*, *right thigh*, *"Pain in right calf"* — all
confirmed or typed, none reaching the board, which ended on `pain / right side`.
Root cause is reproducible with no LLM in the loop and is now queued as
**W2-K**, with **W2-L / W2-M / W2-N / W2-O / W2-P / W2-Q** behind it.

**Known doc drift.** `docs/ROADMAP.md` still marks Phase 2 ✅ with a
verification line reading "51 passed" and describes the pre-banner
architecture. It predates the entire facet-controller rebuild and the banner
era. It is the single most misleading document in the repo right now.

---

## 2. What remains

Five items. Only the first four are candidates for gating the merge.

### F1 · A fresh trial round — ✅ **DONE (08-31 / 09-01, closed 09-08)**
Two months idle. One live session confirms nothing rotted (model availability,
TTS, the banner flow, Ollama on `gemma4:e4b`). Any regression found here
re-prioritizes everything below.

**Outcome:** sat, recorded, and autopsied — [`convergence-plan.md`
§1d](convergence-plan.md). It found a regression, so it *did* re-prioritize
everything below: **W2-K is now inserted ahead of F2** (see §3).

### F1.5 · W2-K — value identity — ✅ **DONE (09-08)**
The one defect F1 surfaced that cannot wait for the bench.
`facets.canonical_value` folds every laterality-bearing body part onto
"right side"; `facets.mentions` credits a multi-token value when a *single*
token appears; morphological variants never fold at all. Three consequences
make this a gate rather than a queue item: it caps the board at seed-level
granularity, it has silently disabled **W3-H** since it shipped (`board.edges`
is empty in all 8 recorded rounds), and it writes the wrong value into
`yes_memory` — i.e. into the recorded dataset — every session it survives.
Reproducible with no LLM and unit-testable without a bench. Spec and the full
causal chain: W2-K in `convergence-plan.md`.

**Landed 09-08.** The mechanism turned out to be one step deeper than first
recorded: the fold also destroyed the model's explicit `refines` tag by making
child and parent identical, which is why `board.edges` was empty everywhere.
A replay of the 09-01 sequence now builds `right side › right leg › right
thigh` and weaves the fine value. **W2-L** (verify wording) and **W2-N**
(restart keeps the profile prior) rode along. `220 passed / 2 skipped`, ruff
clean.

### F2 · W2-G — the noise bench *(PROPOSED, scripts-only)*
The validation instrument, and the most valuable remaining item. Every claim
about W1-B/C/E/F and W3-H currently rests on **single live trials**; the §1
autopsy was hand-made. W2-G automates it: ε-noise on the simulated answerer,
fixture targets replayed from recordings, and per-run metrics (queries to
converge, restarts, informative-yes ratio, focus histogram, wasted-query
estimate) with a `--compare` mode across engine revisions.
**Touches scripts only — zero engine risk.** It is also the precondition for
honestly moving anything from IMPLEMENTED to VALIDATED.

> **Amended 09-08 by the F1 result.** (a) The simulator defaults to local
> **`gemma4:e4b`** — `ANTHROPIC_API_KEY` is not set on this machine, and a local
> default keeps the bench reproducible offline (owner decision). (b) Add the
> **09-01 right-thigh round** as a canonical fixture beside picture / kitchen /
> dishes — it is the regression this refresh exists to prevent. (c) **W2-O
> (instrumentation) is a precondition**: the record carries no `seed_context`,
> no banner state, no per-call latency, no restart *position* and no gate
> rejection reasons, so the bench cannot report the §1 metrics honestly on
> recorded rounds until it does. (d) The bench has **zero test coverage**.

### F3 · W2-E — candidates + tag rescue + pronoun folding *(PROPOSED)*
The cheapest remaining engine win, and now on its **fifth sighting**: q93's
dishes yes, the right-leg yes in the body round, the who-fragmentation (Rob
+25.5 alongside "him" +10), and — from §1d — the *toes* yes and the *right
thigh* yes, both credited entirely to established tags. Decisive answers are
still being thrown away because the formatter tags already-established pairs.
Best landed *after* F2 so the delta is measured rather than asserted, and
**necessarily after F1.5**: rescuing a tag onto a board that has already
collapsed "right thigh" into "right side" buys nothing. Pronoun folding is a
special case of W2-K's folding rule and should be reconciled with it there.

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

## 3. The merge bar — DECIDED (owner, 2026-08-11)

**The bar was F1 → F2 → F3 → F4 → merge.** Rationale: F1 is nearly free and
derisks everything; F2 is zero-risk to the engine and converts the whole
convergence queue from anecdote to measurement; F3 is the last cheap engine
win and wants F2 to exist first; F4 prevents `main` from landing with a
roadmap that describes a system that no longer exists.

**Amended 2026-09-08 (owner) to F1 → F1.5 → F2 → F3 → F4 → merge.** F1 came
back with a defect that the "measure before you change the engine" rule cannot
sensibly hold: W2-K is deterministic, unit-testable without a bench, silently
disables a shipped feature (W3-H), and corrupts the recorded dataset while it
survives. Building the bench on a board whose value identity has collapsed
would measure the wrong baseline. This is a **narrow, documented exception**,
not a repeal — W2-M/W2-N/W2-P and everything in Wave 3 stay behind F2.

Concretely, nothing merges until:

- [x] **F1** — one fresh trial session on the current stack, recorded.
      *(08-31 / 09-01; autopsy in `convergence-plan.md` §1d.)*
- [x] **F1.5** — W2-K lands, with unit tests, after owner iteration.
      *(09-08. Drill-down restored and verified by replay; W2-L wording and
      W2-N rode along. 220 passed / 2 skipped, ruff clean.)*
- [ ] **F2** — W2-G bench lands and reproduces the §1 metrics on a fixture
      (needs W2-O first).
- [ ] **F3** — W2-E lands, with a measured delta on the bench.
- [ ] **F4** — ROADMAP Phase 2 rewritten; §12 checklist reconciled.

**Not gates, but raised by F1 and queued** (`convergence-plan.md` §3): **W2-L**
verify-turn safety — patient-facing, and 2 of 3 verifies in the trial destroyed
a correct belief; **W2-M** caregiver-note fidelity; **W2-N** restart keeps the
profile prior; **W2-O** autopsy instrumentation (an F2 precondition, so it will
in practice land before the bench); **W2-P** focus/content gate; **W2-Q**
`yes_memory` integrity. Whether any of these should be promoted to a gate — W2-L
has the strongest claim, being the only one with a patient-facing harm — is an
open owner decision.

**Merge mechanics — DECIDED: a true merge commit**, as an explicit,
documented exception to the squash-merge rule in `CLAUDE.md`. A 62-commit
branch carrying the entire cockpit is the one case where squashing destroys
real history: the convergence work is a documented sequence of owner-iterated
proposals, and the autopsies in `convergence-plan.md` reference individual
commits. Squashing would break those references.

> Note for F4: `CLAUDE.md`'s Git Workflow section says "squash-merge PRs into
> `main`" without exception. Record this carve-out there so the next session
> does not read the merge commit as a process violation.

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
