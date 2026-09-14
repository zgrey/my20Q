# State of the engine — START HERE

**Purpose: get whoever picks this up (me, next session) from cold to an
informed recommendation in about five minutes, against whatever data exists by
then — not against a snapshot that goes stale.**

Last updated 2026-09-14. Branch `cockpit-shell` @ `909fac3`.

---

## 1. First five minutes — regenerate the picture, don't trust this file

New dev trials are being run in separate sessions, so there will be recordings
here that this document has never seen. **Run these before reading further**;
they read whatever is on disk now.

```bash
# The trajectory, every recorded round, patient + synthetic. Counts only —
# no patient content, safe to paste anywhere.
python scripts/round_metrics.py patient_data dev_recordings

# Any round in detail (the newest dev capture, say)
python scripts/autopsy_round.py dev_recordings/synthetic_demo/<session>.jsonl

# What the controller WANTED on a recorded board — replays _pick_focus
python scripts/replay_pick_focus.py <record.jsonl>

# Server logs from each dev trial (new 09-12; they survive the Quit button)
ls dev_recordings/logs/
```

The three numbers that matter in `round_metrics.py`: **`acc`** (rounds the
caregiver accepted — the only real success measure), **`jobB`**, and now
**`expl%`** (share of questions that dropped the profile to guess fresh,
instrumented 09-14 — earlier dates read 0% for want of the flag, not for want
of exploring).

**Reference points.** 09-09 is the project peak: 3/4 accepted, median 6
questions, jobB 7.90. 09-12 is the worst day since June: 0/3 accepted, 13
restarts, jobB 1.05.

---

## 2. Where the engine actually stands

`cockpit-shell` is the only live branch; `main` is the last known-good engine.
The branch differs from `main` by every KEEP item in
`rescue-plan-2026-09-12.md` §3, **minus W2-X(a)** which was reverted on 09-13.

Benched over all nine scenarios (`rescue-plan` §2c), branch against `main`:

```
reached_ready   2 -> 4      ready_at_query  14 -> 7.25
converged       3 -> 4      diagnostics   2.22 -> 1.44
gate_rejections 4.33 -> 7.33            latency  +536 ms
```

**`reached_ready` doubling is the result to hold on to**: an acceptable draft
reaches the caregiver in 4 of 9 rounds instead of 2, at question 7 instead of
14. It costs more gate rejections and half a second per question.

**Not yet validated live.** Every number above is a simulator. The branch has
never completed a good live round — 09-12's trials ran on the pre-revert code
*and* on a bug fixed mid-round. **The first thing new trial data can tell us is
whether `reached_ready` doubling shows up with a real person.**

---

## 3. Recommendations, ranked — what I would do next

Each says what it rests on, because two of yesterday's calls rested on less
than I claimed.

### R-1 · Fix `why` so it can be recorded (W3-N) — highest value
**Evidence: strong, from the record.** Four of six `why` questions in the 09-12
trial tagged no `why` at all: a reason is phrased as a clause ("because of
using them too much") and slot anchoring wants a noun the board holds. So the
slot never fills, is permanently the "empty modifier" the controller reaches
for, and produces the `focus=what, focus_requested=why` entries that also broke
`_ruled_out`. Owner's framing (09-13): an injury **does** have a why — it is
merely less important, which `facet_priority` already expresses correctly.
Fixing the recording is a different and more tractable problem than anything
about priority.

### R-2 · Give W2-Z its handoff (W2-V)
**Evidence: mixed, and interesting.** The drill contract prevents belief
corruption — removing it took convergence 1/3 → 0/3 and gate rejections
0.67 → 5.00 on the narrow set. But it has no fallback directive, so a correct
rejection can kill a round (2 of 3 diagnostics on 09-12), and the narrow A/B
showed `ready_at_query` 6 without it against 11 with it — **it costs early
turns every time it fires**, not only when it fails. The handoff is: on N
failed drill attempts, fall through to `probe` with tried values excluded.

### R-3 · Build the replay harness (W2-AB)
**Evidence: process, not engine.** Trial 2's dominant failure was reproducible
offline from trial 1's record in under a second and instead cost a live round.
The one-off scripts in `scripts/` are this in pieces. **Limit worth stating:** a
replay cannot test a change that alters which QUESTION is asked, because the
recorded answer belongs to the old question. It tests the controller — focus,
directive, scoring, gates — which is exactly where both 09-12 regressions were.

### R-4 · Re-examine the confirmation-inflated confidence (W2-AA)
**Evidence: one round, needs more.** `where: arms` hit 4.0 by being
re-confirmed four times (one probe, one check, **two identical clarify
confirms**) and was never narrowed in 41 questions. Gate 4 blocks confirmation
farming; CHK and the clarify walk are exempt by construction. The board knows
"confident enough" and has no notion of "specific enough". New trials should
say whether this recurs before anything is built.

### R-5 · The combined explore decay
**Evidence: none yet — deliberately.** `decay ** (yeses + slot_depth + 1)`: the
round term sets a ceiling the slot term can only lower, so it can never explore
more than `main` does, while preserving the real complaint W2-X(a) was written
for (a round can look settled while one slot knows nothing). Kept out of the
rescue on purpose. Needs its own nine-scenario A/B.

### Parked, not forgotten
W2-Y's coined-superset extension; folding CHK into the templated confirm walk
(templated is 16–37× cheaper — 0.47 s against 6.9–17.6 s); W3-I; scheduling
rounds; Phase 3.

---

## 4. Rules that cost real time to learn — do not re-derive them

- **Bench on the full nine scenarios, never a subset.** A 3-scenario run on
  09-12 gave a confident *wrong* veto; the 9-scenario run reversed it. Nine
  scenarios do not fit one background-task window — two batches, merged with
  `scripts/merge_bench_json.py`.
- **Judge a bench on the aggregate metrics, not the convergence count.** Three
  revisions all landed 3–4 of 9 while converging on *different scenarios each
  time*. ±1 convergence at n=9 is noise.
- **Never say "the bench confirms X" when the bench merely failed to
  contradict X.** See `rescue-plan` §2c for the worked example — mine.
- **One change per comparison.** The 09-12 trial mixed seven engine changes and
  a UI pass; nothing in it was attributable without a day of forensics.
- **Build candidates in a `git worktree`**, never on the owner's branch:
  `git worktree add -d <tmp> <ref>`, patch, bench, `git worktree remove`.
- **The privacy invariant is not negotiable.** Dev capture writes only for a
  synthetic persona and refuses if a real profile is loaded; the server log is
  gated on the same flag. `round_metrics.py` prints counts only, so its output
  is safe to share even though it reads the patient dataset.

---

## 5. Pointers

| | |
|---|---|
| The single operational plan | `docs/design/rescue-plan-2026-09-12.md` |
| Queue + every trial autopsy | `docs/design/convergence-plan.md` (§1m, §1n are 09-12; §1j is the ledger) |
| The cockpit as built | `web/README.md` |
| Analysis tools | `scripts/round_metrics.py`, `autopsy_round.py`, `replay_pick_focus.py`, `explore_delta.py`, `check_split_askability.py`, `merge_bench_json.py` |
| Run a dev trial | `bash scripts/serve_cerberus.sh synthetic` — and **`npm run build --prefix web` first**, the bundle is git-ignored |
