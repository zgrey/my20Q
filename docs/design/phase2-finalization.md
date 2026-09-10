# Phase 2 finalization — closing the beta and merging to `main`

Written 2026-08-11 after a two-month gap. Purpose: state exactly where the
`phase2-cockpit` branch stands, what remains before it becomes `main`, and
what is explicitly deferred.

---

## 1. Where we stand

**Branch.** `phase2-cockpit` is **81 commits ahead of `main`, 0 behind** (the
Starlette/CVE-2026-48710 bump was merged down 2026-08-11). The eventual merge
carries **89 files, +20,473 / −1,903** — `web/` arrives on `main` whole, for
the first time. *(Counts refreshed 2026-09-08; refreshed again 2026-09-10 after the
convergence work and three live trials.)*

**Health — all green, re-confirmed 2026-09-10:**

| Check | Result |
|---|---|
| `pytest` | 290 passed, 2 skipped *(211 → 220 K → 237 O → 252 G → 261 R → 283 M/P → 290 Q/oracle)* |
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

*Resolved 2026-09-09/10.* The bench exists (F2), W2-K unblocked W3-H, and the
stack is now **validated in the field**: three sessions on 09-09 converged
four rounds at 7, 4, 11 and 12 queries with the caregiver accepting each
weave — see `convergence-plan.md` §1e–§1f. W2-L's harm did not recur; verifies
now fire once in 38 questions, which is why its open scoring question was
judged moot rather than answered.

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

**Known doc drift — RESOLVED 2026-09-10 (gate F4).** `docs/ROADMAP.md` Phase 2
is rewritten to the banner era with real numbers; `beta-retool.md` §12 is
reconciled against what actually shipped (four items did not land as written —
three tiles not four, the pictogram tile shelved, rounds caregiver-terminated
rather than synthesis-terminated, and TTS moved from Out to In); and the
true-merge-commit carve-out is recorded in `CLAUDE.md` so the merge is not read
as a process violation.

---

## 2. What remains

Five items. **All merge gates are now closed: F1, F1.5 and F2 cleared; F3 was
dropped on 2026-09-10 after re-measurement; F4 is done. The branch is ready to
merge.**

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
>
> **(c) is now closed — W2-O landed 09-08**, so F2 is unblocked and is the
> next action. The record carries `seed_context`, `seed_ms`,
> `pending_question`, per-query `banner` / `timing` / `rejections`, and
> `board.restarts[].after_query`; `scripts/dump_recording.py` renders all of
> it, including the ready-transition metric ("board ready at q009, 9 asked
> after"). Caveat for the fixtures: the four canonical rounds are *pre*-W2-O
> recordings, so a baseline read straight off them still lacks the
> instrumented metrics — replaying them through the current engine is what
> produces a comparable run. (a), (b) and (d) remain F2's own work.

> **F2 closed 09-08.** The bench exists and reproduces the §1 table. Its first
> results re-ordered what comes next: `gemma4:e4b` converged **0 of 6** rounds,
> W2-P is confirmed *with numbers* (`what=20 / how=12 / why=8`), and a new
> defect **W2-R** turned up.
>
> **W2-R was taken ahead of F3 (owner, 09-09) and landed the same day.** The
> reason it outranks W2-E: running the mechanism down showed the stale draft
> was only the symptom. A slot whose confirmations fragment across singleton
> families can never satisfy `family_confident`, so the board never reaches
> readiness, the LLM weave never runs, and the round is **unwinnable however
> well it questions**. Rescuing a tag onto a board that cannot reach readiness
> buys nothing — the same argument that put W2-K ahead of the bench. Full
> writeup and the star-vs-chain experiment: `convergence-plan.md` W2-R.
>
> This is the first engine change **measured** rather than argued about — a
> full nine-scenario run before and after, diffed with `--compare`. The result
> is honest and partial: round health improved consistently (farming yeses to
> zero, diagnostics halved, informative yeses 11 → 23) while **convergence and
> readiness both stayed at 0/9**. Kept because it is correct, tested and
> regresses nothing; *not* claimed as a convergence win. That distinction is
> only available because F2 exists.
>
> **The wall has moved to readiness.** No round in either run reached the point
> where the draft becomes a woven sentence, so no round could be accepted. Two
> things now cap the measurement independently of the engine — the confirm
> oracle rejected a draft that plainly captured the need, and W2-P's
> enumeration is still unaddressed. Worth settling before F3 is judged.

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
- [x] **F2** — W2-G bench lands and reproduces the §1 metrics on a fixture.
      *(09-08. `--noise`, `--compare`, `--scenarios`, the convergence-metrics
      table computed from `build_round_record`, and 15 tests where there were
      none. Its first run converged 0/6 and immediately confirmed W2-P with
      numbers and turned up a new defect, **W2-R**.)*
- [x] ~~**F3** — W2-E~~ — **DROPPED from the bar (owner, 2026-09-10).**
      Re-measured on the post-fix trials: the defect it targets is down to
      **1 farming yes in 25** (4%). It was justified as "the last cheap engine
      win" when the engine did not converge; it now converges repeatedly, the
      bench cannot reliably endorse a pre-merge change (it scored W2-R null),
      and 79 unmerged commits are the larger risk. Moved post-merge, if at all.
- [x] **F4** — ROADMAP Phase 2 rewritten to the banner era with real
      numbers (289/2); beta-retool §12 reconciled (4 items did not land as
      written, pictogram tile SHELVED); the true-merge-commit carve-out
      recorded in `CLAUDE.md`. **2026-09-10.**

**Not gates, but raised by F1 and queued** (`convergence-plan.md` §3): **W2-L**
verify-turn safety — patient-facing, and 2 of 3 verifies in the trial destroyed
a correct belief; **W2-M** caregiver-note fidelity; ~~**W2-N** restart keeps the
profile prior~~ (landed 09-08); ~~**W2-O** autopsy instrumentation~~ (landed
09-08, as predicted, ahead of the bench it gates); **W2-P** focus/content gate;
**W2-Q** `yes_memory` integrity. Whether any of the remainder should be promoted
to a gate — W2-L has the strongest claim, being the only one with a
patient-facing harm — is an open owner decision.

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
