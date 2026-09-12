# Rescue plan — 2026-09-12

**One document, one branch, one decision list.** `convergence-plan.md` is the
long-form record (now ~2,900 lines of queue and autopsies) and stays that. This
is the short operational plan that supersedes the scattered proposals from
09-11 and 09-12 until the engine is back to a known-good state.

Owner, 2026-09-12: *"We are spinning out into a mess of features… We had
something that worked but now you seem to be picking and choosing fixes at
whim."* Both halves are correct, and the second one is measurable.

---

## 0. The parking spot — branches

This is an organisational problem, not a code problem, and it is smaller than
it looks. Checked by content, not by ancestry (squash-merges rewrite history,
so `git log` lies about this):

| Branch | State | Action |
|---|---|---|
| `main` @ `87f2cd1` | **Last known-good engine.** Everything through PR #6. | baseline — do not touch |
| `cockpit-shell` @ `b63017f` | 12 commits ahead. **Contains `w2-board-refinements` entirely.** | the ONE live branch |
| `w2-board-refinements` | fully contained in `cockpit-shell` | delete after confirming |
| `phase2-cockpit` | merged (PR #1), 0 unique | delete |
| `clarifying-mode` | merged (PR #4, squashed) | delete |
| `cockpit-input-and-restate` | merged (PR #5) | delete |
| `dev-capture` | merged (PR #6) | delete |
| `security/httpx2-and-tts-cap` | merged (PR #3) | delete |
| `docs/post-merge-status` | merged (PR #2) | delete |
| `augmented-reasoning` | June 2nd. Its useful half (decouple reason→format, the serve script, the timeout bump) is already in `main`; the 4 unique commits are the abandoned **hierarchical-zoom** architecture | `git tag archive/augmented-reasoning` then delete — tagging keeps it reachable forever at no cost |

**So: there are not four live branches. There is one** — `cockpit-shell` —
plus seven merged PR branches nobody deleted, and one June relic. The mess is
real but it is litter, not divergence. Clearing it is one command per branch
and loses nothing.

---

## 1. What the data says

Every recorded round, patient and synthetic, by date
(`scripts/round_metrics.py`, counts only — no patient content).

| date | rounds | **accepted** | med q | diag | restarts | checks | jobB |
|---|---|---|---|---|---|---|---|
| 2026-06-11 | 3 | 2 | 52 | 1 | 8 | 0 | 6.37 |
| 2026-06-13 | 4 | 3 | 7 | 0 | 1 | 4 | 7.67 |
| 2026-09-01 | 3 | 1 | 18 | 2 | 4 | 3 | 2.79 |
| **2026-09-09** | 4 | **3** | **6** | 0 | 1 | 1 | **7.90** |
| 2026-09-10 | 2 | **1** | 7 | 0 | 0 | 0 | 6.17 |
| **2026-09-12** | 3 | **0** | 5 | **6** | **13** | 10 | **1.05** |

09-09 is the peak the project has ever reached. 09-12 is the worst day since
June 9th. The owner's read is not an impression.

**What landed between them:**

- **09-10** — W2-T (clarifying mode) + W2-U (check every detail). Live-trialled
  the same day: still fine (1/2 accepted, jobB 6.17).
- **09-11** — W2-Y, W2-X(a), the explore/exploit split, the simplification
  pass. Landed on `w2-board-refinements`. **Never live-trialled.**
- **09-12** — the cockpit shell pass, then W2-Z / W3-K / W3-L / W3-M, all in
  one day, on top of the untrialled 09-11 work.

The first live exposure of 09-11's engine changes was today, mixed with a day
of my own changes. That is the process failure underneath the technical one.

---

## 2. Root cause, ranked by evidence

### R1 — W2-X(a) removed the round-level decay on exploration ★ primary suspect

`scripts/explore_delta.py`, priced on trial 2's actual final board:

```
slot     mass  depth   P(explore) NEW   P(explore) OLD    ratio
why       0.0   0.00           0.6667           0.0003   2217x
how       0.0   0.00           0.6667           0.0003   2217x
where     4.0   2.00           0.2963           0.0003    985x
```

`main`: `decay ** (yeses_this_round + 1)` — a monotonic round counter, so
exploration winds down as the round goes on. By q38 with 19 yeses it is 0.0003:
effectively off, and the round exploits what it knows.

`cockpit-shell`: `decay ** (slot_mass/ready + 1)` — a per-slot ratio that is
**0 for an empty slot no matter how long the round has run**. An empty slot
therefore explores at the opening rate forever.

An exploratory probe *drops the patient profile and reaches for a brand-new
on-topic value*. Trial 2 spent its back half probing `why` — which does not
exist for an injury — at 67%, producing a fresh wrong guess every time (q22,
q31–q34, all rejected by Gate 4), feeding the stall detector, feeding the
restarts. **Nine restarts.**

The per-slot idea is *right* — the 09-11 complaint it fixed was real (the round
looked settled while a slot knew nothing). The error is that it replaced the
round term instead of combining with it.

### R2 — priority 3 sends the controller INTO the unfillable slot

`_pick_focus` priority 3: *every core slot confident → probe an empty modifier
slot*. On trial 2's final board it returns `focus='why' directive='probe'`.

Core confidence was itself inflated: `where: arms = 4.0` was reached by being
**re-confirmed four times** (q9 probe, q12 check, q25 and q36 both templated
clarify confirms). Gate 4 blocks confirmation farming; CHK and the clarify walk
are exempt by construction. `arms` was never narrowed — zero refinement edges
formed under `where` in 41 questions.

R1 and R2 compose: R2 sends the round to a slot that cannot be filled, R1 makes
it explore there indefinitely.

### R3 — W2-Z shipped without its escape hatch (mine)

Two of trial 2's three diagnostics are the drill contract rejecting a
re-assertion correctly and the round dying because there is no fallback
directive. I proposed this scope and argued W2-V "may mask whether W2-Z alone
was enough". Wrong: a new rejection gate needs its handoff in the same change.

### R4 — the `_ruled_out` bug (mine, fixed in `33bc272`)

Destroyed three yes-supported beliefs in trial 2 (`discomfort`, `tingling`,
`dull ache`), which is the `what`-slot churn on the transcript. Already fixed
and locked by tests; listed for completeness because it inflated every other
symptom in that record.

---

## 2a. Bench A/B — run, and it confirms R1

Three revisions, `gemma4:e4b`, seed 7, scenarios `foot-pain` / `rob-kitchen` /
`call-daughter`, `--max-queries 25`. Revision C is `cockpit-shell` with **only**
the R1 revert applied, built in a throwaway worktree.

| metric | A `main` | B `cockpit-shell` | C `shell + R1 revert` |
|---|---|---|---|
| **converged** | 1/3 | **0/3** | **1/3** |
| **queries to converge** | 24 | — | **11** |
| **ready_at_query** | **5** | **14** | 11 |
| queries_after_ready | 19 | 11 | **0** |
| gate rejections | 2.00 | 3.33 | **0.67** |
| re-asks | 6 | 10 | **2** |
| checks | 2.00 | 3.67 | 1.33 |
| clarifications | 1 | 4 | 2 |
| diagnostics | 3.33 | 2.67 | 3.00 |
| latency p50 | 3436 ms | 3231 ms | **2957 ms** |

**B is vetoed.** Convergence 1 → 0, re-asks +67%, gate rejections +67%, and the
draft takes until q14 to become offerable instead of q5. The focus histogram
says where the questions went: `main` spent them on `what` (13 vs 5), B spent
them on `who` (**25** vs 9) — churning candidates in one slot instead of
building a draft, which is the signature R1 predicts and the same shape as
trial 2's `why` thrash.

**C rescues it, and keeps the gains.** Reverting R1 alone restores convergence
and does it in **11 questions against main's 24**, with a third of main's gate
rejections, no wasted questions after the draft was ready, and lower latency.
The changes worth keeping (K1–K5) are net positive once R1 is out of the way —
which is the evidence for keeping them rather than reverting the day wholesale.

**The one honest exception: `ready_at_query` is 11 on C against 5 on `main`.**
C still takes six questions longer to put a first draft on screen; it simply
stops wasting the next nineteen. That residue is not explained by R1 and is the
next thing to attribute — most likely R2 (the W2-Z gate costing early turns) or
the untrialled W2-Y widening making the first slot harder to fill. It is listed
as open, not fixed.

**Weight of evidence.** Three rounds. This is veto-grade — enough to say "do
not ship B" and "C is worth trialling" — and not an effect size. A wider
scenario set is cheap to run and should precede any claim stronger than that.

---

## 3. The rescue — decision list

One line per item. **Nothing here is implemented until the owner marks it.**

| # | Item | Evidence | Proposal |
|---|---|---|---|
| **K1** | `split_either_or` | 9 rescues in one round, 7–17 s each | **KEEP** |
| **K2** | Dev capture + server log + stale-build warning | this autopsy was only possible because of them | **KEEP** |
| **K3** | Cockpit shell (viewport, Quit, splitters, hide/expand, bigger draft, thinking animation) | no engine surface at all | **KEEP** |
| **K4** | Diagnostic carries the board; reveal control cannot vanish | owner-reported | **KEEP** |
| **K5** | `_ruled_out` **as fixed** + kinda surviving a restart | fix verified against live history | **KEEP** |
| **R1** | W2-X(a) explore argument | 2217× on the real board; bench C restores convergence and halves the questions | **REVERT to main's rule.** Not the combined form — see below |
| **R2** | W2-Z drill contract | 2 of 3 diagnostics in trial 2; likely part of C's residual `ready_at_query` | **REVERT**, or land W2-V with it. Not keep as-is. |
| **R3** | Priority 3 / confirmation-inflated confidence | never narrowed `arms` in 41 q | **W2-AA** — needs design, not a quick patch |
| **R4** | W2-Y placeholder widening | untrialled; `_NON_IDENTIFYING` grew from 1 set to 3 | **MEASURE before deciding** — no evidence either way yet |
| **R5** | Clarify/CHK exemption from Gate 4 | `arms` farmed to 4.0 | **MEASURE** — likely wants a cap, not an exemption |

**Recommended minimum to get back to working:** R1 revert + R2 revert, keeping
K1–K5. That is two reversions and returns the engine to `main`'s policy while
retaining every change that has evidence behind it. R3/R4/R5 then get designed
properly, one at a time, each with a bench A/B before it is trialled.

**On R1, a correction to an earlier version of this plan.** It offered a choice
between a straight revert and a combined `decay ** (yeses + slot_depth + 1)`.
Offering both was the same mistake this document exists to stop: the combined
form is a *new, untrialled design*, and proposing it inside a rescue breaks the
one-change-per-trial gate in §4. It remains genuinely attractive — the round
term sets a ceiling the slot term can only lower, so it can never explore more
than `main` does, and it preserves the real 09-11 complaint W2-X(a) was written
for. It is therefore a **separate candidate, benched on its own, after the
engine is working again** — not part of the rescue.

---

## 4. The gate — no live trial without this

Owner's time is the scarcest input in this project and it has been spent as a
test harness twice in one day. Before any further live round:

1. **Bench A/B** — `bench_reasoning.py --compare` between the candidate and
   `main`. It is a good veto and a poor endorser (a "no effect" result is no
   evidence; a "worse" result is evidence). Any candidate that benches worse
   than `main` does not go to a trial.

   **How to read the absolute numbers: don't.** `main` itself benches 1/3
   converged at 24 queries, while the same engine took 3 of 4 rounds live on
   09-09 at a median of 6 questions. The simulated answerer is harsher than a
   real caregiver and the gap is structural. Only the **delta** between two
   revisions carries information, which is why both sides are run at the same
   seed, model and scenario set.
2. **W2-AB replay harness** — feed recorded ANSWERS back through a live
   `Round` and report where the controller diverges. Trial 2's dominant failure
   was reproducible offline from trial 1's record in under a second; it cost a
   live round instead. The one-off scripts written today
   (`replay_pick_focus.py`, `check_split_askability.py`, `autopsy_round.py`,
   `round_metrics.py`, `explore_delta.py`) are that harness in pieces.
3. **One change per trial**, or an A/B that separates them. Today's round mixed
   seven engine changes and a UI pass; nothing in it is cleanly attributable
   without this level of forensics.

**Instrumentation gap found while writing this:** the `exploratory` flag is not
recorded on a history entry, so R1 had to be *priced* rather than *counted*.
Recording it is a two-line change and makes the next trial decisive.

---

## 5. Parked, explicitly

Not abandoned, not in scope until the engine is back to 09-09 form: W2-V,
W2-AA, W2-Y extension (coined superset), W3-I, the CHK→template fold, the
scheduling-rounds topic, Phase 3.
