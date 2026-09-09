# Convergence Plan (v3) — closing the gap between 95 questions and ~20

Owner-directed plan, 2026-06-11. Successor to the v2 plan (retro §8 tail);
grounded in the [20Q literature audit](20q-research-audit.html) and the
**June 11, 9:25 AM dishes round** — the first live round on the v2 engine.

> **Protocol.** Proposals are iterated **one at a time with the owner before
> any implementation**. Each carries a `Status:` line that moves
> `PROPOSED → AGREED (date, amendments) → IMPLEMENTED (commit) → VALIDATED
> (trial/bench)`. Nothing below is built until its status says AGREED.
> Exception: W1-A codifies a decision the owner already made and announced.

---

## 1. Evidence — autopsy of the 06-11 dishes round

`my_people`, gemma4:e4b, target ≈ *"remind Rob to do the dishes"*. Outcome:
**synthesized** — *"I need to tell Rob that we really need to make a reminder
about doing the dishes around our home later."* Cost: **95 queries**,
9 proposed utterances across 7 synthesis attempts, **8 restarts**
(5 fail-loop, 2 stalled, 1 synthesis-exhausted), 1 diagnostic card,
5 ⇄ flips. Answer mix: 43 yes / 32 no / 18 kinda / 2 not-sure.

What *worked* (the v2 machinery held):

- Direction layer labeled questions correctly (`tell_them` dominated — the
  target was in fact a tell/remind); the ⇄ button was used five times and
  every flip rendered, re-anchored, and re-classified cleanly.
- The informative-yes flag correctly marked **11 farming yeses** as
  zero-information — they earned points but never advanced the gates.
- Slot anchoring held: no phantom subjects; the final board is sane
  (who: Rob +25.5; how: "tell them something" +11).
- It **converged**, where the 06-10 kitchen round (52 q) never proposed at all.

What burned the 95 queries — six named pathologies:

**A1 · The stale weave (coarse→fine failure — audit G4).** The what-slot
leader was *"something related to keeping our house tidy"* (+6) from q14
onward. Every more specific value confirmed later — "a task" (+3), "new
supplies for cleaning" (+2), "something we need to clean up" (+2), "a
specific cleanup task" (+1.5), and finally **"dishes" (+1)** — entered as a
flat *rival* that had to out-score the coarse incumbent and never could. So
all seven proposals wove the same vague phrase; the last **five proposals
carried byte-identical slot sets**, and four of them were re-rejected
("kinda") for exactly the vagueness the board already knew about. The final
"yes" happened only because the *model's prose* said "doing the dishes"
while the recorded slots were still the stale coarse set.

**A2 · The focus metronome (audit F5/F2 + a hole the audit missed).**
`my_people` core = who+how. After q31, focus was **never `what` again — for
64 straight queries** — while `what` was precisely where the unknown lived.
Mechanism: `what` is non-core (drill ranks core only), it had a positive
leader (probe skips it), and its top-two were not tied (split skips it) —
**a non-core slot with any positive leader is unreachable**. Same hole the
owner observed for `where`/kitchen: `where` got exactly one focused question
(q7) all round. Meanwhile `who` — decided at +10, ultimately **+25.5** —
kept receiving drills (~14 in the tail, e.g. q83 "Is this reminder about
something he usually handles?") because the rotation guard
(`MAX_CATEGORY_RUN=2`) forces an alternation between exactly two core slots:
`how, how, who, how, how, who, …` verbatim in the trace. **Confident slots
never retire; non-core slots can never be drilled.**

**A3 · Caregiver steering has no channel (new — F7).** Mid-round note:
*"The 'when' does not seem important. We need to focus on specifically
'what' she is requesting."* The engine (a) did **not** change focus — the
next focused slots were why, how, how — and (b) `expand_slots` extracted
`{'when': ['later']}` from the note, **crediting +2 to the very slot the
caregiver said to ignore** ('later' ended the round at +3 mostly on the
strength of a note that meant the opposite). This is the second consecutive
trial in which the caregiver had to type "focus on WHAT" and the first where
we can see the note actively backfire. Notes are treated as evidence about
*values*; there is no path for them to carry *directives*.

**A4 · Decisive answers wasted by tag choice (audit G1's cheap half).**
q93 *"Are you talking to Rob about cleaning up dishes?"* → **yes** — the
round-winning fact — was tagged `{who: Rob, how: cleaning up}` (both long
established), so the yes was flagged informative=False and **"dishes" was
never credited**. The answer had to be re-earned at q95. The formatter tags
what it considers salient; nothing prefers *unestablished* mentioned values.
Related fragmentation: `who` carried "Rob" +25.5 **and "him" +10** as
separate contenders (pronouns don't fold); `when` split across
later/today/after-dinner/by-the-end-of-the-day (+3/+1/+1/+1).

**A5 · Late-round gate choking (audit F4/F1/F2).** Five fail-loop restarts
and one diagnostic, all in the dense tail: with ~90 questions in the asked
list and most pairs established, the intersection of "not a repeat
(Dice ≥ 0.8)" and "not zero-information (Gate 4)" became so small the ask
loop kept exhausting retries. Each fail-loop restart **dumps kinda/no
influence — which repeatedly erased the +0.5-strength fine-grained signal
(A1's challengers) while the coarse yes-built incumbent survived**, further
entrenching the stale weave. The gates that protect a short round strangle a
long one; thresholds are absolute while the round grows (F2).

**A6 · Synthesis re-proposals with nothing new (audit F1).** Attempts 4–7
re-proposed without the weave changing (see A1). A count of new yeses
(`new_yes_for_resynthesis=3`) permits re-proposal even when every new yes
landed on already-woven pairs — the gate measures *answers collected*, not
*utterance changed*.

Waste accounting (conservative): ~14 who-re-drills + 11 uninformative-yes
questions + ~6 when-farming + 4 stale re-proposals with their rephrase/pin
cycles + fail-loop churn ≈ **35–45 of 95 queries were policy waste**, before
counting second-order effects (every wasted query also grows the asked-list
that feeds A5). A Wave-1-only engine plausibly runs this round in ≤ 40; the
literature bound (Fig 2 of the audit) for ~5 live contenders × 2-3 open
slots is high single digits.

---

## 1b. Evidence — the second trial (06-11 PM, four rounds)

Fresh targets, four models/rounds, the full W1-A/B/C + W2-F stack live.

| Round | Model | Target | Outcome |
|---|---|---|---|
| my_people | gemma4:e4b | "miss Aaron, plan dinner soon" | **✓ 32 q, 0 restarts** (was 95/8 on dishes) |
| physical_health | gemma4:e4b | "tingling pain, right leg/thigh" | ✓ 42 q, 0 restarts — but `what` focused 22×, `where` 5× |
| mental_health | gemma3:12b | "confused about what's on TV" | ✗ 11 q → 3 diagnostics (model unusable) |
| mental_health | gemma4:26b | same | ✗ abandoned at 7 q (latency unusable) |

**Validated:** focus v3 (the metronome is gone — Aaron's spread was
what=12 who=9 why=5 how=3 where=2 when=1; zero restarts in both e4b
rounds); verify-on-lock (3 fires, all on kinda/context-locked pairs);
the banner + ✗ flow (used naturally: 3 bans, 3 mutes across rounds);
flips (3, all re-anchored). gemma4:e4b is the clear front-runner;
gemma3:12b cannot drive the pipeline; gemma4:26b is latency-priced out.

**B1 · `where` starved structurally in the body round (owner's complaint
— confirmed, three interlocking causes).** (a) `where` is not core for
physical_health and held junk place-seeds (chair / bathroom / room /
nearby — ROOM places, not BODY regions), so the one location channel was
unanswerable; (b) the decisive location answers were tagged elsewhere —
q33 *"…when you try to move your right leg?"* → YES was tagged
`{what: tingling}` only, so **"right leg" was never credited anywhere**
(the dishes-q93 pattern again — W2-E); (c) when "upper thigh" finally
scored (+1 at q39), q42's no clawed it back to 0.0 as the lowest tagged
pair. The final utterance says "upper thigh" only because the LLM weave
read the history; the board never knew.

**B2 · `what` fragmentation caused the 22× hammering (W3-H, second
sighting).** pain / discomfort / pins-and-needles / tingling / buzzing —
five contenders for ONE sensation, splitting credit (+5.5/+2.5/+2/+1/+1),
keeping `what` permanently least-confident, so the drill policy correctly
kept choosing it. The dishes round showed the coarse face of this gap
(stale leader); this round shows the fine face (fragmented refinements).
Refinement links fix both.

**B3 · ✗-notes carry REPLACEMENT semantics the parser drops (new).** All
three value-bans were actually replacements: *"plans seem to be dinner"*,
*"Replace a visit with dinner"*, *"she seems to be indicating the right
leg is the body part"*. The parser banned X each time but never minted Y —
"dinner" never became a contender (the Aaron round's what-slot ended led
by junk at +1.0), "right leg" never entered `where`. The weave rescued
both finals via history, but the board flew blind.

**B4 · Feelings rounds open with emotion-name bingo.** Both mental_health
rounds enumerated angry/worried/sad/scared/lonely serially; "confused"
was not in the seeds and enumeration never reaches it. Seed/hint gap +
the EIG gap (W2-E/G1).

---

## 1c. Evidence — the Avalanche round (06-11 evening, full new stack)

`my_people`, gemma4:e4b, target *"give Zach Colorado Avalanche tickets for
next week"*: ✗ abandoned at 62 q (10 restarts, 5 diagnostics) — and the
cause was **the free-text ✗-channel itself**:

- **B5 · ✗-notes destroyed a near-correct draft.** *"remove worrying"*
  banned the right value but the leftover-minting heuristic minted
  `why='remove'` (+2) and verify-on-lock then asked the patient *"Is it
  remove you want to use?"*. Later, the augmentation note *"The news to
  share is that Paula wants to give Zach Avalanche tickets"* matched the
  CORRECT who-anchor → **banned Zach (+10, floored)** and minted
  `who='news share paula give'`. The near-correct draft collapsed; the
  post-ban fail-loops (the model kept reaching for the vetoed Zach) burned
  the rest of the round. Free text cannot distinguish ban / augment /
  replace intent — the guessing must go.
- **B6 · who-enumeration opening** — 25+ serial name guesses
  (Rob/Julie/mom/friend/sister/doctor/neighbor…) before Zach surfaced; the
  people-flavored twin of B4's emotion bingo (W2-E/G1 territory).

→ **W1-F (owner-designed, below): the synthesis editor.** Selectable draft
segments + candidate dropdown + typed replacement + Restate; the free-text
✗-note UI is retired, and the residual free-text path is hardened
(negation-cue gating for bans; marker-gated replacement minting;
caregiver-chosen values are exempt from verify).

### W1-F · The synthesis editor — Status: IMPLEMENTED (06-11, owner-designed)

The ✗-edit's purpose is *minor tweaks to a mostly-correct structure*; the
implementation makes that the only thing it can do:

1. **Selectable segments.** The draft's woven values are clickable; a click
   opens the editor strip: the slot's **top board candidates** as one-tap
   chips, a **free-text replacement** (a word or a grouped phrase; Enter
   applies — fixed: it can no longer leak to caregiver context), and
   **✕ remove this detail** (mutes the slot). The click identifies the
   (category, value) exactly — zero parsing.
2. **Refine-or-replace** (`Round.replace`, `POST …/replace`): a replacement
   that lexically EXTENDS the old value ("tickets" → "Avalanche tickets")
   keeps it as the parent — the refinement edge derives automatically, the
   frontier deepens, nothing is banned; a genuine swap strikes the old
   value and the new one stands in at caregiver strength (+2, minted
   verbatim — no canonical folding onto its own parent). Empty = mute.
3. **⟳ Restate** (`POST …/restate`): re-say the same draft differently
   (the rephrase machinery with a kinda-note — same content, new words);
   touches only the draft cache.
4. **Hardening from B5:** free-text bans require a negation/removal cue;
   replacement minting is marker-gated ("seems to be / replace…with /
   should be") — *"remove worrying"* now bans cleanly with no junk mint,
   and an augmentation note becomes guiding context, never a ban.
   Caregiver-chosen values (mints/replacements) count as verified — the
   engine never double-checks the caregiver's own words aloud.

**Acceptance (met in tests):** the three Avalanche failure notes replay
correctly; "a drink" → "a hot drink" refines (parent kept, frontier
deepens, no verify); swaps strike-and-stand-in; remove mutes; restate
changes only the text. 212 tests pass.

---

## 1d. Evidence — the 08-31 / 09-01 trial (gate F1)

The first trial after the two-month park, and the **first two sessions of the
banner era** to be recorded. It closes gate F1 of
[`phase2-finalization.md`](phase2-finalization.md) — and it did what F1 was for:
it found a regression the June rounds could not have exposed, because June never
ran a laterality-heavy body round.

`gemma4:e4b` on local Ollama throughout (real profile ⇒ the cloud path is
refused fail-closed). 8 rounds, 45 answered questions, **1 accepted proposal**.

| Session | Round | Topic | Outcome | Queries | job_b |
|---|---|---|---|---|---|
| 08-31 | r1 | my_people | abandoned | 0 | 0.0 |
| 08-31 | r2 | physical_health | abandoned | 0 | 0.0 |
| 08-31 | r3 | mental_health | **synthesized** | 18 | **9.556** |
| 08-31 | r4 | mental_health | abandoned | 0 | 0.0 |
| 09-01 | r1 | my_people | abandoned | 0 | 0.0 |
| 09-01 | r2 | physical_health | abandoned | 7 | −0.571 |
| 09-01 | r3 | physical_health | abandoned | 0 | 0.0 |
| 09-01 | r4 | physical_health | abandoned | **20** (the cap) | −0.600 |

**What worked — the banner era is sound.** 08-31 r3 locked both core facets
(`what: confusion +2.0`, `why: about my memory +2.0`) with margin, went ready,
and the caregiver accepted the LLM weave via a genuine ✓ (`Round.accept`):

> *"I feel this confusion all the time, and it's mostly about my memory,
> especially worrying about forgetting things that happened in my past."*

Zero diagnostics, one `stalled` restart, and `who` correctly went all-negative
and was excluded from the weave — the round genuinely was not about a person.
**Zero literal repeats in either session** (the hard repeat gate holds; the
"same question 7 times" era is over). Seeding is healthy in every round,
including the four zero-query ones.

**What failed — the engine cannot retain the specific content of a "yes."**
Across 09-01, Paula confirmed *toes*, *right leg*, *right thigh*, and *it is
hurting*; the caregiver typed *"Paula wants do talk about her right side
paralysis"* and, later, *"Pain in right calf"*. After 27 questions, 3 fail-loop
restarts, 2 diagnostic cards, 2 typed rescues and ~19 minutes, the board's best
answer was `pain / right side` — **less specific than the seven words the
caregiver typed himself**. The 09-01 session is a total loss for the patient.

**C1 · Value identity is the binding constraint (new — the headline).**
Reproducible with no LLM in the loop:

```
canonical_value({'where': {'right side': 7.0}}, 'where', 'right leg')      -> 'right side'
canonical_value(..., 'right thigh')                                        -> 'right side'
canonical_value(..., 'right arm')                                          -> 'right side'
mentions("Is the pain you are feeling happening right now?", "right side")  -> True
canonical_value({'what': {'confusion': 2.0}}, 'what', 'confused')          -> 'confused'  (no fold)
```

Three faults in the same fifty lines of `facets.py`:

- *Coarse absorption.* `canonical_value` folds at raw token overlap ≥ 0.5, so
  any two-token value sharing one token collapses. Every laterality-bearing body
  part *becomes* "right side", which ended at **+7.0** while the actual answer
  never existed as a value. This also silently disables **W3-H**: `board.edges`
  is **absent from all 8 rounds** — the child is never minted, so no coarse→fine
  edge can ever form. The refinement machinery has never once engaged in
  production.
- *False credit.* `mentions` is `any()` over the value's content tokens, so a
  multi-token value is "said" when **one** token appears. q024 *"Is the pain you
  are feeling happening **right now**?"* credited `where: right side` off the
  word "right"; 08-31 q007 *"Are you feeling lonely?"* → **no** scored −1.0 on
  `why: feeling overwhelmed` off the shared stem *feeling*. This is a hole in
  the very gate built to stop score drift (the Aaron rule): it blocks unrelated
  subjects but credits anything sharing one stem.
- *Variant fragmentation.* `canonical_value` compares raw token sets while
  `mentions` uses the prefix-tolerant `_tokens_match`, so single-token
  morphological variants never fold: 08-31 ended with `confusion +2.0` beside
  `confused −1.0`, and `worried +1.0` beside `worry −0.5`.

The corruption reaches the **recorded dataset**, not just the live round:
`yes_memory_2026-09-01.jsonl` logs *"Is the feeling you are having located in
your right thigh?"* with `needs: ["feeling", "right side"]`. Job-A/Job-B
training data is being written with the wrong value. → **W2-K**.

**C2 · A4/B1 again — decisive answers wasted by tag choice, fifth sighting.**
q005 *"Is the tingling you are feeling in your **toes**?"* → **yes**, tagged
`{what: tingling}` — "toes" never minted anywhere. q015 *"…located in your right
**thigh**?"* → **yes**, tagged `{where: right side}`. q018 *"Is the sensation
you are having in your right thigh because it is **hurting**?"* → **yes**,
tagged `{where: right side}` only and formally flagged `informative: false` —
the round's clearest confirmation scored nothing but an already-locked location.
Rejections vanish the same way: q013 ruled out *pressure* and q019 *burning*,
neither ever minted, so the engine cannot know it has ruled that axis out and
re-probes it. This is exactly **W2-E**, but note the ordering consequence: W2-E
rescues the *tag*, C1 fixes the *identity* the tag lands on. Rescuing onto a
board that has already collapsed "right thigh" into "right side" buys nothing.

**C3 · Verify-on-lock is unsafe (new).** `prompts._SLOT_PHRASE` feeds "the thing
or subject" / "the place" into the verify instruction and gemma4 folds that
vocabulary straight into patient-facing text:

- *"Is the subject you want to discuss tingling?"* (09-01 r2 q006)
- *"Is the place you want is the right side?"* (09-01 r4 q009 — ungrammatical)
- *"The subject is pain? Is that correct?"* (09-01 r4 q008)

`auditor._META_PHRASES` contains "the board" and "candidate" but not "the
subject" / "the place", so nothing caught them — and these were spoken via TTS
to a patient with documented comprehension confusion. Worse, the verify is
gate-exempt by construction and applies a full −1.0: **2 of the 3 verifies in
this trial destroyed a correct belief.** q006 took a **no** on `what: tingling`
one question after a **yes** on tingling-in-toes, halving it and killing the
round. q009 fired on `where: right side`, whose only support was a caregiver
note — which W1-F specified is exempt from verify. → **W2-L**.

**C4 · Caregiver notes are mistranslated (new, and the most damaging single
event available).** `expand_slots` is instructed to repeat already-on-board
values verbatim so they are credited; that rule now destroys new information:

- *"…her right side **paralysis**"* → `{where: right side, what: **pain**}`.
  The note does not say pain. "pain" was fabricated from an existing contender
  at `CONTEXT_POINTS = 2.0` — enough to lock the slot instantly — which then
  triggered the verify that got **no**. The engine invented a fact, believed it
  hard, asked the patient about it, and was told it was wrong.
- *"Pain in right **calf**"* → `{what: pain, where: right side}`. *calf* is in
  the note's own words and would have anchored cleanly, but the model preferred
  the coarse contender already at +5. The word *calf* appears in no question, no
  slot, and no board in the entire session.

Both notes came after long no-streaks — the caregiver was visibly rescuing a
failing round, twice, in plain text, and the engine's next questions were about
doctors, posture, burning and timing. → **W2-M**.

**C5 · Restart deletes the caregiver's name prior (new).** `_restart` reseeds
with `profile_context = ""`. The profile says *"Rob is the number one
priority"* and lists the family in ask order; after one restart `who` went from
`Rob, Zach, Aaron, Julie, Ashley` to `my husband / my caregiver / my daughter /
a doctor` — generic relations, which are exactly the low-information questions
the name list exists to prevent, and they drew four consecutive no's. Restart
also drops all `kinda` mass, erasing the fine-grained signal (the A5 effect,
still live). What a restart should dump is the *no/kinda score history*, not the
*identity prior*. → **W2-N**.

**C6 · The autopsy instrumentation cannot support the bench (new — blocks F2).**
This §1d had to be reconstructed partly by reading engine source, because the
record does not contain: `seed_context` (the round-opening guiding-context box
is **never** persisted — and `dump_recording.py`'s `job_b["seed_context"]` probe
can therefore never fire); banner text or `ready` transitions (so the 09-01
banner state below is *inference*, not record); per-call latency or token counts
(turn cost of **~25–65 s/question** had to be derived from `yes_memory`
timestamps); the position of restarts (markers are stripped from `queries`); the
gate rejections behind "could not produce a usable question"; and any pending
unanswered question (08-31 r4 sat 7 min 14 s with zero answers and we cannot see
what was on screen). W2-G is supposed to automate the §1 metrics — it cannot
report honestly on data that does not record them. → **W2-O**, an F2
precondition.

*Inferred banner state (reconstruction, not record).* At abandon, 09-01 r4's
core facets `[what, where]` were both confident — `what: pain 4.0` (over
`feeling 3.0`, a margin of exactly 1.0) and `where: right side 7.0` — so the
banner was **ready and glowing**, weaving `pain / right side / caregiver`. The
caregiver had an accept button in front of him and declined it, because the
location was still "right side" after he had twice typed something finer.

**C7 · Smaller, recorded here so they are not lost.**

- *Focus vs. content divergence.* `focus` is what the controller asked for;
  `slots` is what the model delivered, and they disagree on roughly **9 of 45**
  turns (q003 `focus=where` asserting only `{what}`; q017/q018 `focus=what`
  asserting no `what` at all). `_pick_focus` then re-selects the same starving
  slot, which is what produces r4's `what=11 / 20` histogram.
- *Semantic enumeration the repeat gate does not catch.* Six confusion-frame
  questions in 08-31 r3; ten sensation-quality probes across 09-01
  (discomfort → tingling → numbness → cold → dull ache → pressure → itch →
  heaviness → burning), against topic hints that say never to enumerate. The
  `FUTILE_STREAK` guard is **disarmed by C1/C2**: it needs 4 consecutive no's
  sharing one asserted pair, and the scattered tags never share one.
- *Vacuous contenders.* `what: feeling +3.0` is a contentless placeholder that
  came within 1.0 of leading the slot.
- *Best-effort accepts.* Two questions in 08-31 r3 were asked with the retry
  budget exhausted and unusable tags; both got "no".
- *`yes_memory` is written and never read.* 09-01 r2 confirmed *tingling in
  toes*; r4, same topic, minutes later, re-asked about tingling and got **no**.
  Its `round_id` (`"r4"`, an ordinal) cannot be joined to the recording's uuid,
  it carries no `session_id`, its `needs` inherit C1/C2's lossy tagging, and
  `dated_path` keys on **local** date while `at` is **UTC** — hence a file named
  `yes_memory_2026-08-31.jsonl` containing only `2026-09-01T02:…` timestamps.
- *Banner affordances went unused.* No `edit`, `ban`, `mute`, `mint` or
  `flipped_from` entries exist in either session. Of ✓ / ✗ / ⟳ / ⇄, **only ✓ was
  ever used, once.** Worth asking the caregiver whether they were undiscovered
  or unwanted before building more of them.
- *Profile "never ask" rules are prose-only.* The profile bans questions
  insinuating Paula can speak; 08-31 q008 asked *"Are you feeling frustrated
  because it is hard to get your thoughts out right now?"* and drew the round's
  only `not_sure`. `audit_query` already exists as the code-level backstop for
  what prompts fail to enforce; hard bans belong there.

**Bottom line.** The failure is *upstream of the banner*. Given a board that
locks honestly, the banner produces a good utterance and the caregiver accepts
it (08-31 r3). Given a board whose value identity has collapsed, no amount of
banner or synthesis work can help — which is why **W2-K is ordered ahead of the
bench**, as an explicit exception to the "measure first" rule: it is
deterministic, unit-testable without a bench, and it is corrupting the recorded
dataset every session it survives.

---

## 1e. Evidence — the 09-09 trial: the stack converges with a real person

Two sessions, `gemma4:e4b`, on the post-W2-K/L/N/O/G/R stack. **Three rounds
synthesized and accepted**, at 7, 4 and 11 queries.

| | 08-31 / 09-01 | 09-09 |
|---|---|---|
| accepted | 1 of 8 rounds | **3** |
| queries to accept | 18 | **7 / 4 / 11** |
| `reached_ready` | never | **3 of 3** |
| diagnostics | 2 | **0** |
| restarts | 4 | **1** |
| queries after ready | — | 2 / 0 / 1 |

The drafts are genuine LLM weaves, not the code template — *"I need Zach to
come over soon so he can help me with a task and share my feelings"*, and an
11-query `mental_health` round producing *"I worry right now that I am going to
feel like a burden to others, especially when I think about the future."* Two
`emergency` rounds fired the short-circuit.

**W2-R is visibly working in production**: `DRILLING 'Zach'` on q3/q4, with
`who: Zach +4.0` accumulating instead of fragmenting into singletons.

**The bench got W2-R wrong, and the owner called it.** W2-R measured as a null
result (0/9 converged, 0/9 reached-ready) and was kept only because it was
correct and non-regressing. With a real person answering it is part of a stack
that converges three times in four attempts. The standing instruction —
*"tests with an oracle are less useful than the real thing"* — is now
evidence-backed: **`reached_ready` is the honest bench metric, but a live
session is the honest verdict.**

### The latency finding

24 instrumented questions: mean **10.2s**, median 7.1s, and **deliberate is 86%
of every turn**. The distribution is the story:

- clean first attempt: **6.1s** (n=14)
- needed a gate re-ask: **16.0s** (n=10)
- **re-asks cost 99s of the 245s total — 40% of the caregiver's waiting**

Rejections by cause across the W2-O-era records: 4 repeat-gate, 4
zero-information, 3 either/or, 2 no-anchored-slots.

### W2-S · Prompt-side re-ask reduction — Status: TRIED AND REJECTED (09-09)

**The idea.** Gate 4 rejects a question asserting only established pairs, but
`established` was never passed into `deliberate_messages` at all — the board
shows raw points and leaves the model to infer the threshold. It was being
penalised for a rule it could not see, at ~10s a turn. Adding an ALREADY
SETTLED block, plus restating the three gates as a checklist immediately before
the ask, looked like free money.

**Measured, three ways, same seed and command (9 scenarios each):**

| variant | queries | rejections/question | diagnostics | rounds ending early |
|---|---|---|---|---|
| baseline | 119 | **0.538** | **10** | **2/9** |
| settled + checklist | 97 | 0.433 | 20 | 4/9 |
| settled block only | 103 | **0.553** | 17 | 4/9 |

**Rejected, and the reason is worth keeping.** Constraining the model *more*
made it fail *harder*: both variants doubled the diagnostics and doubled the
rounds that died early. Told to avoid asked ground AND settled ground, it gets
stuck repeating itself and hits the retry wall sooner — and **a diagnostic card
is a worse experience for the caregiver than a slow question.**

Two process notes, both mistakes made here:

1. *The per-round rejection count is a trap.* The combined variant's headline
   −34% was largely an artifact of **shorter rounds** — fewer questions asked
   means fewer chances to be rejected. Only the per-QUESTION rate is honest.
2. *Two changes at once cannot be attributed.* Split, the result inverted the
   hypothesis: the settled block alone is **worse than baseline**, and the
   checklist was doing the useful work and masking it. Change one thing.

**So the latency lever is not prompt-side.** The re-ask rate did not yield to
more instruction. What remains: cap or restructure the deliberate phase (86% of
the turn), a faster model, or reduce how often the controller asks for
something the model cannot deliver — which is W2-P territory.

---

## 2. How the queue is ordered

Three sorting keys, in order:

1. **Queries saved per unit risk** — measured against this autopsy, not
   hypothetically. A2/A6 fixes are pure waste-removal with small surface
   area; they go first.
2. **Information before structure** — make every question and every answer
   count (Wave 2) before changing the belief structure (Wave 3): structural
   changes are easier to judge on top of a non-wasteful baseline, and the
   bench (W2-G) must exist before the big rebuild lands.
3. **Owner-visible pain first** — A3 (the ignored "focus on what" note) is
   small, twice-observed, and corrodes trust in the tool; it outranks
   abstractly-better items.

Dependencies: W3-H (refinement links) supersedes parts of W1-B's vagueness
ranking and W1-C's weave-change test — both are written to degrade
gracefully into it. W2-G (bench) gates *validation* of everything after it.

**Amended 09-08 by §1d.** One exception to key 2 and to "the bench must exist
first": **W2-K (value identity) lands before W2-G.** It is deterministic and
unit-testable with no bench, it has silently disabled W3-H since W3-H shipped,
and it writes wrong values into the recorded dataset — so measuring first would
only establish a corrupt baseline. The rest of the new §1d queue (W2-L…W2-Q)
keeps the normal ordering, except **W2-O**, which W2-G structurally requires.
New dependency chain: **W2-K → W2-O → W2-G → W2-E**.

---

## 3. The queue

### W1-A · Rephrase budget default → 1 — Status: AGREED (owner, 06-11) → IMPLEMENTED

Owner: multiple rephrasings of a rejected utterance are useless; one
"minor perturbation" is the most that helps. The kinda path already caps at
one (v2); this drops the **default** `rephrase_limit` 3 → 1 so "no" gets the
same budget and the shipped default matches the validated setting.
Evidence here agrees: across 9 utterances, no second-or-later rephrase of an
attempt was ever the one confirmed. Touches: `config.py` default, README /
tool-summary tables. Risk: none (env-overridable).

### W1-B · Focus policy v3 — retire, widen, rotate-on-stall — Status: IMPLEMENTED

> **AGREED 06-11 with owner amendments; implemented same day.**
> (1) Owner approved retirement, with trials to tune. **Amended in
> implementation (disclosed):** the agreed *margin* rule (≥ 2× split-margin)
> retires the 06-11 round's vague what-leader (+6 vs +4, margin exactly 2.0)
> at the moment it most needs drilling — margins grow with round length
> (audit F2). Implemented as a **dominance ratio** instead: retire when
> leader ≥ `retire_ready_x`(3.0) × ready AND runner ≤ half the leader.
> Replay: who (+25.5/+10) retires by ~q30 ✓, what (+6/+4) stays live ✓.
> Env: `MY20Q_RETIRE_X`.
> (2) Owner: "where" ranks behind "what" in caregiving — but NOT hard-coded
> (a placing-something round must still drill where). Implemented as
> per-topic `facet_priority` in topics.yaml (data, editable) applied
> *within* a confidence band: drill ranks core block → unestablished before
> established (coverage first — evidence dominates) → facet_priority →
> weakest leader. my_people: `[who, how, what, why, when, where]`.
> (3) Ladder extension ends on the FIRST miss (no/not-sure) past the default
> run — slightly stricter than the drafted "two misses".
> 6 new tests; 182 pass.

**Problem (A2).** Confident slots keep soaking focus; non-core slots with a
positive leader are unreachable; the rotation guard manufactures a
two-slot metronome.

**Proposal — three coordinated rules in `_pick_focus`:**

1. **Retirement.** A slot whose leader is *decisively* confident — leader ≥
   `facet_ready_points` AND margin over runner-up ≥ 2 ×
   `facet_split_margin` (tunable multiplier, `MY20Q_RETIRE_MARGIN_X`,
   default 2.0) — is **retired from probe/drill/pin**. It remains
   split-eligible (a genuine re-tie reopens it) and fully creditable;
   restarts rebuild the board, which can naturally un-retire. In this round
   `who` retires around q15 and `how` by ~q45 — the metronome's two
   stations close.
2. **Drillable = any live slot with signal.** The drill/pin candidate pool
   becomes: non-retired slots with a positive leader, **core or not**,
   ranked by *least confident first* (smallest margin-to-ready), core slots
   winning ties. `what` (+6 but vague) and `where` (+2, exactly the
   kitchen case) become drillable the moment they have signal. Probe
   priority (coverage of empty core slots) is unchanged.
3. **Rotate on stall, not on count.** `MAX_CATEGORY_RUN` stays as the
   *default* rotation, but a drill/pin may extend its run while it is
   *working*: if the last same-slot question was answered yes or kinda
   (the leader moved), a 3rd+ consecutive turn on that slot is allowed.
   Two misses in a row → forced rotation as today. (Restores the
   body→leg→foot→toe ladder that `MAX_CATEGORY_RUN=2` currently cuts,
   without re-enabling 20-question why-hammering — hammering is a *stall*,
   and stalls rotate.)

**Touches:** `dialogue._pick_focus` (+ helpers), `config.ReasoningTuning`
(one knob), tests. No prompt changes; no board-math changes.

**Risks.** Early mis-retirement of a wrongly-confident slot → mitigated by
the 2× margin, split-eligibility, and restarts. Widened drill pool could
chase modifier slots too early → mitigated by "core wins ties" and
probe-first priority. Interaction with W3-H: rule 2's "least confident
first" later refines into "shallowest frontier first" — forward-compatible.

**Acceptance.** Unit: focus-sequence replays (retired slot never focused;
what drillable in the A2 scenario; ladder of 3 allowed while rising). Trial:
no focused question on a slot whose leader is ≥ 2×-confident; `what`/`where`
receive drills in a people round.

### W1-C · The living proposal banner — Status: IMPLEMENTED (06-11)

> Final owner amendment folded in: ✓ raises an **explicit confirmation
> modal** — front and center over a dimmed backdrop, the final utterance
> displayed and spoken, a single "New round" action. Implementation notes:
> early drafts are code-templated ("I need/want … for/from …" with the
> direction buckets collapsing the connector); the LLM weave takes over at
> board-readiness, regenerated only on weave change; ✗-notes are parsed
> deterministically (slot word + dismissal stem ⇒ mute; a note mentioning a
> woven value ⇒ ban, floored on the board and hard-gated out of future
> questions; anything else falls through to guiding context); accepts are
> recorded as a confirmed `synthesis` history entry so the dataset keeps
> one shape; the four synthesis count-knobs (`MY20Q_MIN_YES` …) are retired.
> CLI gained `p`-to-accept; the bench plays caregiver (confirms the draft
> with the simulator whenever it turns ready). 193 tests pass.

**Superseded twice in iteration — final design is the owner's.** Instead of
gating engine-initiated proposals (the original draft) or a bare
propose-button, the cockpit carries a **continuously updated draft
utterance as a top banner**, and the caregiver decides when it is spoken
and when it is done. The synthesis *threshold* demotes from a behavioral
gate to a display gate — wrongness there costs pixels, not turns (SPRT
note: the human becomes the stopping policy; the engine's job is keeping
the posterior legible).

**The banner:**

- **Populates early with structured ambiguity.** As soon as anything
  converges, the draft renders with ambiguous alternates for undecided
  parts and a trailing ellipsis: *"I need/want something for/from Rob…"*.
  The "for/from" alternate is the direction layer's uncertainty rendered as
  text; alternates collapse as buckets/slots establish. Before any signal:
  *"Pending synthesis…"* with a glowing vibrance. Early drafts are
  CODE-templated from the board (deterministic, free, per-segment mapping
  trivial); once core slots establish, the LLM weave takes over —
  regenerated **only when the weave changes** (the original W1-C comparison
  survives as the refresh trigger and the LLM-cost cap).
- **Per-segment emphasis:** *locked* (slot confident) · *working* (positive,
  thin margin — glow) · *placeholder/alternates* (faint + ellipsis).
  Mapping via the stem-tolerant `mentions` matcher; theory mapping: margin
  bands ≈ per-slot posterior odds; propose-ready vibrance at board-ready
  (≈ conjunction ≥ 0.5 — the point where the proposal itself is the
  highest-information question available).
- **Three controls on the box:**
  - **Speak** — TTS the current draft to/for the patient (struck text is
    never spoken).
  - **✓ accept** — definitively conclude: the round ends `synthesized`
    with the current draft as the final utterance.
  - **✗ reject-a-portion** — opens a text field NEXT TO the proposal. The
    note is interpreted against the draft (small anchored LLM call with a
    deterministic fallback) into **value bans** ("not supplies" — the
    banned value renders strike-through and is excluded from leaders,
    weave, and questioning) and/or **slot mutes** ("when doesn't matter" —
    the slot's segment renders dimmed + struck, excluded from questioning
    and from speech). Edits are recorded as history entries → replayable,
    undo-able, and emphasizable (un-strike on undo).
- **Channel separation (owner):** the X-field edits the proposal; the
  existing context field is *guiding context* only. Value bans therefore
  never come from parsing ordinary notes (kills W1-D's misparse risk).
- **Engine consequences:** auto-proposals are REMOVED — no engine-initiated
  synthesis events; the kinda-loop dies structurally. A ✗-rejection still
  pins focus (the pin reads edit entries) and counts toward the restart
  trigger. `min_yes`/`new_yes` count-gates retire. The CLI harness gains a
  `p`/`✓`-equivalent flow.

**Touches:** dialogue (banner state, edit entries, accept/reject/ban/mute,
weave-change detection, pin source), reasoner (X-note interpreter), api
(banner in RoundState + accept/reject/speak endpoints), web (banner
component + controls + strike/dim rendering), recorder (edit entries,
accepted proposals), CLI, tests — the largest Wave-1 item; implemented
after W2-F. **Acceptance:** dishes-round replay shows a draft from the
first established slot; no engine-initiated proposals; an X-note bans a
value that then disappears from questioning and speech.

### W1-D · Caregiver directive channel — Status: PROPOSED · **DEMOTED (06-11 trial)**

> The banner's ✗-flow absorbed most of D's reason to exist: across four
> trial rounds the owner steered entirely through ✗-bans/mutes and plain
> context — no "focus on X" note was typed. D stays queued (the directive-
> leak guard is still wanted) but drops behind W1-E and W3-H.

**Problem (A3).** "Focus on X / ignore Y" notes neither steer focus nor are
protected from being mis-read as evidence (the `when:'later'` +2 credit from
a note saying when doesn't matter).

**Scope shrunk by the W1-C banner design (owner, 06-11): bans and mutes now
arrive through the proposal box's ✗-flow — a dedicated, explicit channel —
so the free-text context field never needs to carry them.** What remains
for D is the FOCUS half of A3:

1. **Deterministic focus-directive parse** (code, no LLM) on
   `add_context`: a slot mention (who/what/when/where/why/how + obvious
   synonyms) with a strict verb set — *focus on / zero in on / ask about /
   pin down* → `forced_focus = (slot, ttl=3 focused turns)`; honored at the
   head of `_pick_focus` while ttl lasts.
2. **Directive notes do not become evidence.** A note that parses as pure
   directive skips `expand_slots` (the A3 mis-credit: "the 'when' does not
   seem important" must never credit `when: later` +2). A mixed note still
   extracts values from its non-directive remainder. "Ignore/doesn't
   matter" phrasings in the context field map to the SAME mute mechanism
   the ✗-flow uses (one implementation, two entry points).

**Touches:** `dialogue` (parse + forced_focus + `_pick_focus` head), tests.
**Risks:** false-positive directive detection — strict verb set + slot word
must co-occur. **Acceptance:** replaying A3's note yields forced what-focus
for 3 turns and no `when` credit; "she pointed at the kitchen" behaves as
today.

### W1-E · Per-topic facet priorities, body-aware seeds, replacement edits — Status: IMPLEMENTED (06-11, owner approved as drafted)

**Problem (B1/B3/B4).** The body round starved `where` (junk room-place
seeds; not core; unreachable until late); ✗-notes that *replace* a value
("plans seem to be dinner") only ban, never mint; feelings seeds lack the
non-emotion states (confusion) so rounds open with emotion-name bingo.

**Proposal — three coordinated data/parser changes:**

1. **Per-topic priorities** (the owner's ask — all in `topics.yaml`, no
   engine logic). Draft, reasoned per topic:
   - `my_people` — KEEP `core [who, how]`, `priority [who, how, what, why,
     when, where]` (validated: Aaron round, 32 q).
   - `physical_health` — **core → `[what, where]`** (a body report is
     sensation + location before anything; the remedy follows). `priority
     [what, where, how, why, when, who]`. For non-localized wants (thirst)
     `where` never goes positive: the caregiver ✗-mutes it (one tap — the
     owner already does this naturally) or accepts pre-ready. Probe order =
     core order, so `where` is probed the moment `what` has any signal —
     the structural fix B-priorities alone cannot deliver.
   - `mental_health` — KEEP `core [what, why]`; `priority [what, why, who,
     how, when, where]` (feelings are often about a person → who before
     how; place/time last).
   - `general` — KEEP `core [what, how]`; `priority [what, how, where,
     who, why, when]`.
2. **Body-aware `where` seeds + hint.** physical_health's topic hint
   redefines `where` as the BODY REGION (head / back / arms / legs /
   stomach / chest …), not a room — B1(a)'s junk seeds (chair, bathroom,
   nearby) made the slot unanswerable. mental_health's hint gains the
   non-emotion states (confused, overwhelmed, bored) so seeds stop being
   pure emotion-name bingo (B4).
3. **Replacement semantics in ✗-notes.** Patterns like "X seems to be Y",
   "replace X with Y", "not X, [it's] Y" ban X **and mint Y** in the same
   category at context strength (+2) — recorded in the same edit entry
   (`ban` + `mint`). Fallback: after any ban, leftover content tokens of
   the note (minus the banned value's and stopwords) mint into the banned
   category. All three trial bans were replacements; the board never
   learned "dinner" or "right leg".

**Touches:** `topics.yaml` (+ hints), `dialogue.edit`/`_parse` (+ mint in
replay), tests. **Risks:** body-core `[what, where]` delays readiness for
non-localized wants (mitigated by mute + accept-anytime); replacement
minting could mint junk from a chatty note (mitigated: mint only from the
ban path, value capped at a few tokens). **Acceptance:** replaying B1's
round, `where` is probed within the first ~6 queries and "right leg"
enters the board at the ✗-edit; "plans seem to be dinner" yields
`what: dinner +2` struck-for-struck.

### W2-E · Candidate selection + tag rescue + pronoun folding — Status: PROPOSED

**Problem (A4, audit G1-cheap).** The single formatted question wastes
turns; model tag choices can squander decisive answers (q93's dishes);
pronouns fragment the who-slot.

**Proposal.**

1. **3-candidate format.** The FORMAT pass returns up to three candidate
   questions (same JSON, `candidates: [...]`), one call. Code picks: prefer
   candidates asserting ≥ 1 unestablished pair; among those, the candidate
   whose asserted pair's current score is closest to the slot's live median
   score (the cheap p≈½ proxy from the audit); run the four gates on the
   winner only; fall back to the next candidate on gate rejection (saving
   re-prompt round-trips — today's corrections loop becomes the fallback,
   not the first resort).
2. **Tag rescue.** After `_anchored_slots`, if every kept pair is
   established but the question text *mentions* an unestablished board value
   (or a novel content noun in the focus slot), swap the weakest established
   tag for that pair. q93 then credits `what: dishes` and its yes is
   informative.
3. **Pronoun folding.** A who-value in the pronoun set folds onto the
   positive who-leader when exactly one exists ("him" → Rob; ambiguous
   boards don't fold).

**Touches:** `prompts.FORMAT_SYSTEM` (+candidates), `reasoner.ask`
(selection + rescue), `facets.canonical_value` (pronouns), tests.
**Risks:** candidate JSON adds tokens (cap 3, short); median proxy is crude
(monotone toward even splits — good enough until true EIG); pronoun folding
wrong in multi-person rounds (guarded: only with a unique positive leader).
**Acceptance:** bench shows fewer gate re-prompts per query; a q93-style
replay credits the unestablished mention; "him/he" no longer appear as
who-contenders alongside a named leader.

> **Fifth sighting (§1d C2, 09-08)** — *"…tingling in your **toes**?"* → yes,
> tagged `{what: tingling}`; *"…located in your right **thigh**?"* → yes,
> tagged `{where: right side}`; *"…because it is **hurting**?"* → yes, tagged
> `{where: right side}` and flagged `informative: false`. Rejections vanish the
> same way (*pressure*, *burning* never minted, so the engine re-probes the
> axis it already ruled out).
> **Ordering (owner, 09-08): W2-E lands after W2-K.** Rescuing a tag onto a
> board that has already collapsed "right thigh" into "right side" buys
> nothing — the identity layer has to be honest before the rescue is worth
> measuring. Pronoun folding (part 3) is a special case of W2-K's folding rule
> and should be implemented there or reconciled with it.

### W2-F · Verify turns + slot-aware repeat exemption — Status: AGREED (owner, 06-11) → IMPLEMENTED

> Owner: "Definitely verify with re-asks — very important functionality I
> had overlooked." Hook redefined during iteration to be independent of the
> W1-C banner (which removes engine-initiated proposals): verify fires **on
> lock**, not on weave.

1. **Verify-on-lock.** When a slot's leader first turns *confident* on the
   strength of a **single yes** (e.g. one yes + a caregiver-context boost),
   the next question turn double-checks that pair: a reasoner one-shot
   (like flip — no deliberate phase), prefaced "Just to double-check —",
   gate-exempt by construction, `verify: true` in history. Budget ≤ 2 per
   round; a pair is never verified twice; pairs confirmed by ≥ 2 yeses
   never need it. **Scoring is the normal rule** — a verify-yes is +1
   genuine confirmation, a verify-no is −1 on the asserted pair (no special
   halving; noise cuts both ways and the asymmetric-no rule already
   protects bystanders).
2. **Repeat-gate slot exemption.** A candidate matched ONLY by the
   content-overlap (Dice) channel passes when it asserts a slot category
   absent from the matched prior's asserted slots ("Will Zach come over
   **today**?" after "Will Zach come over?" asserts `when` — legitimate
   drill, not a repeat). Normalized/ratio channels untouched; flip-
   superseded questions are never exempt (their slot sets are unknown).

**Touches:** `dialogue` (verify-due check in the question path), `reasoner`
(`verify` one-shot), `auditor.is_repeat` (slot-aware exemption), tests.
**Acceptance:** a single-yes lock triggers exactly one double-check; the
"come over today" case passes the gate; verbatim/reword repeats stay
blocked.

### W2-G · Noise bench — Status: IMPLEMENTED (09-08) · gate F2

**Problem (audit G5).** Every claim above is currently judged by single live
trials. The autopsy in §1 was hand-made; it should be a script output.

**Proposal.** Extend `scripts/bench_reasoning.py`: (a) ε-noise wrapper on
the simulated answerer (flip yes↔no with ε ∈ {0, 0.05, 0.1, 0.2}; kinda
unaffected); (b) fixture targets = the picture round, the kitchen round, the
dishes round (from their recordings); (c) report per run: queries to
converge, proposals, restarts, informative-yes ratio, focused-slot
histogram, wasted-query estimate (the §1 metrics, automated); (d) a
`--compare` mode diffing two engine revisions. Strong simulator only
(gemma4-class or Anthropic on synthetic personas, per the privacy
invariant).

**Touches:** scripts only. **Risks:** none to the engine. **Acceptance:**
one command reproduces §1's table for any recording or simulated run; W1
fixes show a measured Δ on the dishes fixture.

> **Amended 09-08 (owner):** the simulator defaults to local **`gemma4:e4b`**
> — `ANTHROPIC_API_KEY` is not set on this machine and a local default keeps
> the bench reproducible offline. Add the **09-01 right-thigh round** as a
> canonical fixture beside picture / kitchen / dishes: it is the regression
> this refresh exists to prevent. **W2-O is a precondition** — the bench
> cannot report restarts, gate rejections, latency or banner state on
> recordings that do not carry them. The bench also has **zero test
> coverage** today; add some.
>
> **W2-O landed 09-08, so this is unblocked.** The bench can now read, per
> round: `seed_context`, `seed_ms`, `pending_question`, and per query
> `banner` (hence the ready-transition and questions-asked-after-ready
> metrics), `timing` (per-phase ms, LLM calls, gate attempts) and
> `rejections`; restart *positions* come from `board.restarts[].after_query`.
> Note this is only true of rounds recorded from here on — the four canonical
> fixtures are all pre-W2-O recordings, so a `--compare` baseline drawn from
> them still cannot report the instrumented metrics. Replaying a fixture
> through the current engine can.

**Landed 09-08.** `scripts/bench_reasoning.py` gained `--noise`, `--compare`,
`--scenarios`, `--seed`, a `convergence metrics` table, and 15 tests (it had
none). Three decisions, all owner-approved before building:

1. **The right-thigh fixture is a synthetic analogue**, `leg-laterality`, not
   the real round. That round was a real-patient session and its clinical
   detail must never be committed; what regressed was the *mechanism* (a
   laterality-bearing part folding onto its coarse parent), so any need with
   the shape *side → limb → part of limb* exercises it identically.
2. **`--compare` diffs two `--json` files**, not two git revisions — it works
   on an uncommitted tree, needs no worktree dance, and `bench_*.json` is
   already gitignored.
3. **The caregiver's accept is never noised.** The confirm call is the run's
   ground-truth oracle, so "converged" keeps meaning *converged to the right
   need despite noise* and a delta stays attributable to the engine.

**Metrics are computed from `build_round_record`, not from the live `Round`** —
the same artifact `dump_recording.py` reads. That is deliberate: every number
the bench prints is then provably obtainable from a real recorded session, and
it is what makes W2-O the precondition rather than a nice-to-have.

**Two defects in the instrument itself, found by running it:**

- *The accept gate was unreachable for a whole class of needs.* The driver only
  offered the draft when `banner.ready` lit. `physical_health` gates readiness
  on **what + where**, and a non-localized need (thirst) has no body location —
  the topic YAML says so outright and expects the caregiver to ✗-mute `where`
  or accept pre-ready. Those rounds could not converge *by construction*. The
  driver now offers the draft whenever it changes; `ready` is still recorded
  per query and reported, it is just no longer the gate.
- *The simulator was answering with the zero-information answer.* `_SIM_SYSTEM`
  mapped "unrelated to your need" → `not_sure`, which scores **0**. A thirsty
  persona asked about tingling therefore gave the board nothing, and one round
  took ten such answers in a row before any signal reached it. A persona handed
  its need outright should answer `no` — it knows. `not_sure` now means
  genuinely cannot tell, and is documented as rare. *(This changes what the
  bench measures. It is one prompt string and trivially revertible.)*

**First results — the bench's output, not an argument.** `gemma4:e4b` converged
**0 of 6** rounds across `thirsty`, `leg-laterality`, `cold` and `lonely` at
caps of 12–20 queries. Two findings fall straight out, both pre-existing and
both for the queue rather than for F2 (which is scripts-only by design):

- **W2-P, confirmed and quantified.** The focus histograms are lopsided —
  `what=20 / how=12 / why=8` on one run, `what=12 / where=4` on another — and
  the transcripts show the enumeration the repeat gate cannot catch: nine
  consecutive delivery-mechanism questions ("carry it to you", "hand it to
  you", "set it down"), every one answered `not_sure` because the scenario's
  need does not specify a delivery mechanism at all.
- **A new one — the draft does not follow the leader.** In the `cold` round the
  `what` slot climbed *an object → keeps you warm → fabric → wrap yourself in →
  blanket*, but the banner text never moved off "an object": the values sat
  tied at +1.0 with no refinement edge between them (they are not lexically
  nested, and the model tagged no `refines`), so `frontier` kept returning the
  incumbent. The draft was offered to the caregiver **once in 20 queries**.
  Adjacent to W2-E/W3-H; wants its own queue entry.

Caveat on reading one run: `--compare` of two *identical* configurations back to
back still moved `gate_rejections` 5 → 0 and mean latency by 1.2 s. Single-
scenario runs are noisy — a real comparison wants the full scenario set.

### W2-K · Value identity — anchoring and folding — Status: IMPLEMENTED (09-08)

> **Owner-approved and landed 09-08** ("address why drill-down isn't
> functioning"). **The mechanism was one step deeper than §1d C1 first
> recorded**, and it is worth stating exactly, because it exonerates the model:
>
> 1. The reasoner asked a correct drilling question and tagged it correctly —
>    `{where: "right thigh"}` **with** `refines: {where: "right leg"}`. The
>    model was doing its job.
> 2. `canonical_value` folded the child onto the coarse incumbent, so
>    `slots["where"]` became `"right side"`.
> 3. The named parent was *also* `"right side"` — so `_anchored_refines`
>    (`reasoner.py:693`, "parent must differ from child") **silently dropped
>    the edge**.
> 4. The point landed on the coarse value, the child never existed, and
>    `derive_edges` had nothing to link.
>
> So the fold did not merely lose the fine value — it destroyed the explicit
> refinement tag that would have rescued it. The lexical fallback could not
> help either: `value_extends("right thigh", "right side")` is False (they are
> siblings, not a subset pair), so the explicit tag was the only path and the
> fold closed it.
>
> **Shipped:** `head_token` (values are head-final; two values disagreeing on
> the head are different things however many modifiers they share);
> `mentions` requires the head plus ≥ 50% token overlap for multi-token values
> (single-token behaviour unchanged); `canonical_value` never folds across a
> `value_extends` refinement pair, requires head agreement, and matches with
> the prefix-tolerant `_tokens_match` so `confused`→`confusion` folds; `_stem`
> restores a trailing `i`→`y` so `worried`→`worry`; `is_vacuous` blocks
> contentless placeholder contenders (`what: feeling` reached +3.0).
> **Verified by replay:** the 09-01 sequence now builds
> `right side › right leg › right thigh` and the frontier weaves
> **"right thigh"** — the actual answer — instead of `right side +7.0`.
> 220 tests pass, ruff clean.
>
> **Not done here, deliberately:** W2-E's candidate selection and tag rescue
> (still F3) — this fixes the identity the tag lands on, not the tag choice.

**Problem (§1d C1 — the binding constraint, and the only one reproducible with
no LLM in the loop).** `facets.canonical_value` folds at raw token overlap
≥ 0.5, so "right leg" / "right thigh" / "right arm" / "right calf" all *become*
"right side"; `facets.mentions` is `any()` over the value's content tokens, so
"…happening **right** now?" credits `where: right side` and "Are you feeling
**lonely**?" penalises `why: feeling overwhelmed`; and because
`canonical_value` compares raw token sets while `mentions` uses the
prefix-tolerant `_tokens_match`, single-token variants never fold at all
(`confusion` beside `confused`, `worried` beside `worry`). Consequences: the
board cannot get finer than its seeds, `board.edges` is empty in **every**
recorded round so **W3-H has never engaged in production**, `FUTILE_STREAK` is
disarmed because scattered tags never share a pair, and the wrong value is
written into `yes_memory` — i.e. into the Job-A/Job-B dataset.

**Proposal sketch (to be iterated with the owner before implementation).**

1. **Anchoring.** Require **all** content tokens of a multi-token value to
   appear in the question; keep `any()` only for single-token values. Exclude
   vacuous tokens (*feeling*, *thing*, *about*, *something*) from anchoring
   entirely.
2. **Folding.** Use the same prefix-tolerant `_tokens_match` the anchor uses,
   so *confused*/*confusion* and *worry*/*worried* fold; and require **head-token
   agreement** for multi-token values, so a shared modifier ("right", "my",
   "the") can never collapse distinct heads.
3. **Vacuous contenders.** Blacklist contentless nouns on mint — `what: feeling`
   reached +3.0 and came within 1.0 of leading the slot.
4. **W3-H interaction.** Once "right thigh" survives as its own value, the
   `derive_edges` lexical-subset fallback should parent it under the coarse
   value and the frontier should weave the fine one. Verify by replay, not by
   assertion.

**Touches:** `facets` (`mentions`, `canonical_value`, `_mint`), tests.
**Risks:** stricter anchoring drops some legitimate credit (mitigation: the
`_derive_slots` fallback already covers the empty-slots case, and W2-E's tag
rescue is the designed complement); head-token detection is crude for
prepositional values ("about my memory"). **Acceptance:** `canonical_value`
keeps "right thigh" distinct from "right side" and folds "confused" onto
"confusion"; `mentions("…happening right now?", "right side")` is False; a
replay of 09-01 r4 carries a fine `where` value and a **non-empty**
`board.edges`.

**Ordering — decided (owner, 09-08):** W2-K lands **before** W2-G, an explicit
exception to "no engine change before the bench". It is deterministic and
unit-testable without a bench, it silently disables an already-shipped feature,
and it corrupts the recorded dataset every session it survives.

### W2-L · Verify-turn safety — Status: PARTLY IMPLEMENTED (09-08)

> **Landed 09-08 — the wording half only.** `prompts.verify_messages` no
> longer hands the model its slot gloss as a label to reuse: each slot now
> ships an indefinite description plus a natural example of what the question
> should sound like, and `VERIFY_SYSTEM` explicitly forbids naming the *kind*
> of detail. `auditor._META_PHRASES` gains a deliberately narrow backstop list
> ("the subject", "you want to discuss", "the place you want" …) — narrow on
> purpose, because re-prompt pressure is what drives fail-loop restarts, so
> blocking the bare word "the place" would trade one failure mode for another.
>
> **Correction to §1d C3 (recorded honestly):** the claim that verify "fired
> on a value W1-F declared exempt" conflates two paths. W1-F exempts values
> the caregiver picks in the *synthesis editor*; values extracted from a
> context *note* are deliberately verified (`dialogue.py:101-104`) precisely
> because the extraction can be wrong. And in this trial it **worked** —
> q008's double-check on the fabricated `what: pain` correctly came back
> **no**. That verify caught a real error; only its phrasing was bad.
>
> **Still open:** whether a verify *disagreement* should erase the pair
> (−1.0, today) or open a split. q006 took a noisy "no" on `what: tingling`
> one question after a genuine yes and halved it, killing the round. This is
> an **owner-decided** behaviour (W2-F: "Scoring is the normal rule — no
> special halving"), so it is not being changed unilaterally. It wants the
> bench before it is re-decided.

**Problem.** `prompts._SLOT_PHRASE` ("the thing or subject", "the place") leaks
into patient-facing text — *"Is the subject you want to discuss tingling?"*,
*"Is the place you want is the right side?"*, *"The subject is pain? Is that
correct?"* — spoken via TTS to a patient with comprehension confusion.
`auditor._META_PHRASES` does not contain those stems. The verify is gate-exempt
and applies a full −1.0, so **2 of 3 verifies in the 08-31/09-01 trial
destroyed a correct belief**, and one fired on a caregiver-supplied value that
W1-F declared exempt.

**Proposal.** (a) Add the slot-phrase stems to `_META_PHRASES` and add a
minimal grammaticality check ("is … is"); (b) rewrite `VERIFY_SYSTEM` to pass
the *value* without the slot gloss; (c) a verify disagreement should **open a
split, not erase the pair** — one noisy answer must not undo a genuine
confirmation; (d) enforce the W1-F exemption: never verify a pair whose only
support is a caregiver context boost. **Touches:** `prompts`, `auditor`,
`dialogue` (verify-due check), tests.

### W2-M · Caregiver-note fidelity — Status: PROPOSED (09-08, §1d C4)

**Problem.** The "repeat already-on-board values VERBATIM so they are credited"
rule in `expand_slots` turns notes into hallucinations: *"right side
**paralysis**"* → `what: pain` (fabricated, credited +2.0, instantly locking the
slot, then contradicted by the engine's own verify); *"Pain in right **calf**"*
→ `where: right side` (the specific location deleted). At
`CONTEXT_POINTS = 2.0`, a mistranslated note is the single most damaging event
available to the system — and both notes were the caregiver *rescuing* a
failing round.

**Proposal.** Extract note-anchored values **first** and mint them as new
contenders; fold onto an existing contender only on exact or near-exact match
(reusing W2-K's tightened rule); **never emit a value absent from the note's own
words**. **Touches:** `prompts.expand_slots` instruction, `dialogue` (context
path), tests.

### W2-N · Restart keeps the profile prior — Status: IMPLEMENTED (09-08)

> **Landed 09-08.** `Round._restart` no longer blanks `profile_context` before
> the recovery reseed — a one-line deletion. The comment now states the rule
> it was violating: a restart dumps the **no/kinda score history**, never the
> **identity prior**. Not done: carrying `kinda` mass forward at reduced
> weight (the A5 erase effect) — that changes scoring, so it wants the bench.

**Problem.** `_restart` reseeds with `profile_context = ""`, so the caregiver's
ordered name list ("Rob is the number one priority") is deleted exactly when the
round is in trouble; `who` degrades to *my husband / my caregiver / a doctor*
and draws consecutive no's. Restart also drops all `kinda` mass (the A5 erase
effect, still live).

**Proposal.** Keep `profile_context` on the recovery reseed — what a restart
dumps is the *no/kinda score history*, not the *identity prior* — and carry
`kinda` credits forward at reduced weight. **Touches:** `dialogue._restart`,
tests. **Risks:** low; the profile prior scores nothing by itself, it only
shapes seeds.

### W2-O · Autopsy instrumentation — Status: IMPLEMENTED (09-08) · F2 precondition, cleared

**Problem.** §1d had to be reconstructed partly from engine source. The record
carries no `seed_context` (so `dump_recording.py`'s probe for it can never
fire), no banner text or `ready` transitions, no per-call latency or token
counts, no restart *position* (markers are stripped from `queries`), no gate
rejection reasons behind "could not produce a usable question", and no pending
unanswered question. W2-G is supposed to automate the §1 metrics; it cannot
report honestly on data that does not record them.

**Proposal.** Snapshot `banner()` alongside every query entry; record
`seed_context`; record per-call milliseconds; keep restart markers in-sequence
(flagged) rather than stripping them; record which gate rejected each attempt;
record a pending question on abandon. **Touches:** `recording.recorder`,
`dialogue` (history entries), `reasoner` (rejection reasons),
`scripts/dump_recording.py`, tests. **Risks:** record-schema change — older
recordings must still dump (the dumper already handles legacy fields).

**Landed 09-08**, with two owner decisions that changed the proposal above:

1. **Restart position, not restart markers.** Keeping `restart` in `queries`
   would push an internal belief-control marker into the two *caregiver-facing*
   surfaces that render entries with an `else → "Question N"` fallthrough
   (`transcript.to_markdown`, `web/src/review.tsx`). `board.restarts` entries
   carry **`after_query`** instead — same information, no consumer churn, and
   the transcript a caregiver reads stays conversation-only.
2. **No token counts.** Ollama returns `prompt_eval_count`/`eval_count` and
   `chat()` throws them away, but capturing them means widening
   `LLMBackend.chat`'s return type across every backend; a `last_usage`
   side-channel is unsafe because the API serves concurrent sessions off one
   backend. Latency and call/attempt counts only. Deferred, not refused.

The record gained `seed_context`, `seed_ms`, `pending_question`, per-entry
`banner` / `timing` / `rejections`, and `board.restarts[].after_query` — all
additive and omitted when empty, so every pre-W2-O recording still dumps
(verified against the 06-11, 08-31 and 09-01 files). `ReasonerError` from
`ask` / `flip` / `verify` now names the gate that fired, which reaches the
caregiver's diagnostic card as well as the record.

**One correction to the spec while implementing.** Stamping the banner only on
the success path lost the snapshot exactly where it matters most: a live
`gemma4:e4b` round left an answered query with no banner because the answer
landed and then the *next* ask failed the repeat gate, returning before the
stamp. The stamp now runs on the failure paths too, walking back past the
restart/diagnostic markers the failure path interposes — but stopping at a
caregiver edit or a synthesis, so a snapshot is never misattributed.

**What it already shows.** A single instrumented `gemma4:e4b` round:
`no-streak@q004` (restart position), 9.1 s mean per question but **19.1 s on a
3-attempt question vs 5.6 s clean** (the deliberate/format split makes the cost
of a gate re-ask visible for the first time), and 3 gate rejections across 6
questions — 2 of them the model writing either/or questions. All of it is
`--compare`-able the moment W2-G exists.

### W2-P · Focus/content divergence gate + enumeration-axis guard — Status: PROPOSED (09-08, §1d C7)

**Problem.** Roughly **9 of 45** turns assert no slot in the category the
controller asked for, so `_pick_focus` keeps re-selecting a slot the questions
never feed (r4's `what=11 / 20`). Separately, the repeat gate blocks rewordings
but not *semantic enumeration down one axis* — six confusion-frame questions,
ten sensation-quality probes — and `FUTILE_STREAK` cannot catch it because the
scattered tags never share a pair.

**Proposal.** A fifth gate rejecting any question that asserts no slot in the
requested focus category; and an axis-level guard that tracks the semantic
dimension being enumerated (sub-types of one confirmed parent) and forces a
category rotation after N misses, independent of pair identity. **Note:** W2-K
partly re-arms `FUTILE_STREAK` on its own — re-measure after W2-K before
building the axis guard.

### W2-R · The draft does not follow the leader — Status: IMPLEMENTED (09-09)

**Problem.** In a bench `cold` round the `what` slot climbed *an object → keeps
you warm → fabric → wrap yourself in → blanket* across five confirmed yeses,
and the banner text stayed on **"an object"** the whole way. Each value scored
+1.0 from a single yes, so they sat tied with no leader; none was lexically
nested in another, and the model tagged no `refines`, so `edges` stayed empty
and `facets.frontier` kept returning the incumbent family. Consequence: the
draft was offered to the caregiver **once in twenty queries**, and the round
could not converge no matter how well the questioning went.

This is the *other* half of W2-K. W2-K fixed the case where a fine value was
wrongly folded ONTO its parent; this is the case where a fine value is
correctly kept separate but never linked, so the draft cannot see it. W3-H
supplies the link when the model tags `refines` or the values nest lexically —
neither holds for a semantic ladder like object → fabric → blanket.

**The sketch above was wrong about the primary cause, and running the mechanism
down (09-09, no LLM) corrected it.** "Break the frontier tie by recency" is
neither sufficient nor necessary. The visible symptom is the stale draft; the
load-bearing defect is one level down:

| | no edges (before) | one chain (after) |
|---|---|---|
| `families` | 5 singletons @ 1.0 | one family @ **5.0** |
| `family_confident` | **False** (1.0 < ready 2.0) | **True** |
| `frontier` | `an object` — first inserted | `blanket` |

Because the slot never becomes confident, the **board never reaches
readiness**, so `_refresh_draft` never calls the LLM weave and the caregiver is
shown the raw code template for the whole round. That is why the round could
not converge — not merely why the text was stale. A fragmented slot is
*unwinnable*, however well the questioning goes.

**Landed 09-09 (owner-approved before building).** `derive_edges` gains a third
tier, applied **last**, so the model's explicit `refines` tag still wins and
lexical nesting still wins after it — inference only fills the gap where
neither spoke. The link comes from the controller, not the model: a **`drill`**
directive *is* the controller saying "narrow this slot", so a yes to the
resulting question refines the value being drilled.

**The parent is the slot's FRONTIER, not its leader** — this is the whole
design, and the experiment is unambiguous:

```
parent = leader   →  a STAR:  frontier = "keeps you warm"   ✗ confident, wrong value
parent = frontier →  a CHAIN: frontier = "blanket"          ✓ confident, right value
```

Parenting to the leader accumulates mass correctly but leaves an arbitrary
depth-1 child as the frontier. Parenting to the frontier builds the ladder the
round actually walked — and it is the honest reading: the caregiver was looking
at draft value X, the controller drilled X, the patient said yes to a narrower
Y, so Y refines X.

Captured at **ask time** (`_propose_question` → `action.drill_parent` → the
history entry → `_drill_tags`), because deriving it during replay would be
circular: the frontier is itself read off the edges. It is recorded on every
drill, answered or not, so an autopsy can see the ladder the controller was
trying to walk; `dump_recording.py` renders it as `DRILLING '<value>'`.

**Guarding (owner decision): lean on what already exists.** Only a `drill`
infers — never `probe` (an empty slot has no frontier), never `split`
(separating tied alternatives is the opposite of narrowing), never `pin`. Only
a **yes** infers. `_attach` applies the same legality rules as an explicit tag:
both values must already be on the board, first writer wins, no cycles. Beyond
that, a wrong chain is caught by the mechanisms already in place —
verify-on-lock double-checks a thin lock, the ✗-edit bans a wrong value, and
`frontier` RETREATS to the parent if the child loses support.

**One constraint worth knowing:** `drill` is priority 4, so it only becomes
available once **every core slot has a positive leader** — priority-1 PROBE
outranks it until then. The inference therefore cannot fire in a round's
opening phase, which is the correct scope (there is nothing to narrow yet) but
also means a test whose mock only ever tags one category never reaches this
code at all.

**Touches:** `facets.derive_edges` (+ `_attach`), `dialogue._propose_question` /
`answer` / `_drill_tags` / `_replay_board`, `reasoner.ReasonerAction`,
`dump_recording.py`, `web/src/types.ts`, tests.

#### Measured on the bench — the first engine change that was

Nine scenarios, `gemma4:e4b`, cap 15, seed 7, identical command both sides.
**This is a partial result and the headline metric did not move.**

| metric | before | after | Δ |
|---|---|---|---|
| **converged** | **0 / 9** | **0 / 9** | **0** |
| **reached readiness** | **0 / 9** | **0 / 9** | **0** |
| diagnostics per round | 2.22 | 1.11 | **−1.11** |
| restarts per round | 2.89 | 2.44 | −0.45 |
| gate rejections per round | 7.89 | 7.11 | −0.78 |
| informative-yes ratio | 0.79 | **1.00** | +0.21 |
| latency per question | 5.73 s | 6.25 s | +0.51 s |

Round health improved consistently and in the predicted direction — informative
yeses **11 → 23** in total, farming yeses to **zero in every round** (three
rounds had one before), rounds ending early on a diagnostic **5 → 2**. That is
exactly what un-fragmenting a slot should do: a yes now lands on a coherent
family instead of re-establishing another singleton.

**But no round reached readiness, so no round could converge.** A one-off
`cold` round run separately *did* reach readiness at q13 and wove a genuinely
good sentence — *"I need help getting a blanket over me right now"* against the
hidden need *"I am too cold and want a blanket"* — which the confirm oracle
then rejected. So readiness is reachable post-W2-R and was not pre-W2-R in any
observed round, but it is rare enough that nine rounds did not catch one.

**Two things this measurement exposed, neither of them W2-R:**

1. *A defect in the bench itself.* `aggregate_metrics` scoped
   `queries_after_ready` to CONVERGED rounds, so a run where the board reached
   readiness but the caregiver never accepted would have reported "—" for both
   sides and hidden the whole effect. Fixed, with a test: `reached_ready` and
   `ready_at_query` are now first-class and unconditioned. *(It did not in fact
   mask anything here — readiness was genuinely zero on both sides — but it
   would have.)*
2. *The confirm oracle is too strict.* It rejected a draft that plainly
   captured the need. While that holds, `converged` is capped below what the
   engine deserves and is the wrong metric to steer by; `reached_ready` is the
   honest primary for now. Queue this with the W2-G simulator notes.

**Verdict.** Kept: correct by construction (proven with no LLM), unit-tested,
improves every secondary metric, regresses nothing but ~0.5 s of latency. Not
claimed: any convergence win. The wall is now *readiness*, and the next thing
worth measuring is why a slot that has stopped fragmenting still rarely gets
two clear points ahead of its rival.

### W2-Q · `yes_memory` integrity and same-session read-back — Status: PROPOSED (09-08, §1d C7)

**Problem.** 09-01 r2 confirmed *tingling in toes*; r4, same topic, minutes
later, re-asked about tingling and got **no**. The log held the answer.
Additionally its `round_id` is an ordinal (`"r4"`) that cannot join the
recording's uuid, it carries no `session_id`, its `needs` inherit C1/C2's lossy
tagging, and `dated_path` keys on **local** date while `at` is **UTC** (hence
`yes_memory_2026-08-31.jsonl` containing only `2026-09-01T02:…` timestamps).

**Proposal.** Unify the round id, add `session_id`, make the file key and the
timestamp the same timezone, and feed *same-session, same-topic* confirmed yeses
into the seed/ask prompts as established facts. **Open question for iteration:**
the read-back is a deliberate two-layer-rule boundary (`yes_memory.py` docstring
says the engine no longer reads the log) — same-session read-back is arguably
still volatile-layer and permitted, but that is the owner's call, not a
refactor to make quietly.

### W3-H · Refinement links — coarse→fine inside a slot — Status: IMPLEMENTED (06-11)

> **Owner decisions:** (1) frontier weaves at ONE confirmed yes —
> verify-on-lock covers thin locks; (2) upward propagation DEBATED and
> resolved as **non-negative family mass** read-time shielding instead:
> a child's yes lifts the family implicitly, a child's no hits only the
> child — the parent stays locked through failed weaving below it, with
> zero propagated points (propagation would write credit onto values the
> question never said — the Aaron-bug class — and double-count evidence);
> (3) chains render in the cockpit NOW (`› child` chips with an accent
> spine in the consensus board) — the visible dive.
> **Shipped:** `facets.derive_edges` (explicit model tags, parent must
> already exist — no resurrection; lexical-subset fallback; insertion-order
> acyclicity), `families`/`family_confident`/`frontier` (descends at one
> yes, RETREATS when a fine value loses support), refines tags through
> FORMAT→`ReasonerAction.refines`→history→replay; all policy confidence
> reads (ready / retire / probe-open / pin / drill bands / verify target)
> moved to family level; the weave uses the frontier; board records carry
> `edges`; prompts render `parent›value`; the tile shows chained chips.
> **Watch:** family-level retirement can stop drilling while the frontier
> is still coarse — the ✗/pin flows reopen it, but if live rounds show
> coarse frontiers at retirement, add "frontier confirmed" to the
> retirement condition. 205 tests pass.

> **§1d (09-08): this had never engaged in production — now unblocked.**
> `board.edges` was **empty in all 8 recorded rounds** of the 08-31/09-01
> trial. The cause was upstream, in W2-K: `canonical_value` folded every fine
> value onto its coarse incumbent ("right thigh" *became* "right side"), which
> both removed the child and — by making the child equal the parent named in
> the model's `refines` tag — made `_anchored_refines` drop the edge.
> `derive_edges` then had nothing to link.
>
> **W2-K landed 09-08** and a replay of the 09-01 sequence now builds
> `right side › right leg › right thigh` with the frontier weaving the fine
> value. The Watch above is finally *evaluable*: re-check `board.edges` and
> whether retirement fires on a still-coarse frontier on the next live trial.
> Until then W3-H is IMPLEMENTED and unit-tested, **not field-validated**.

> Second sighting, opposite face: the dishes round showed the COARSE
> failure (stale leader uncatchable by refinements); the thigh round shows
> the FINE failure (pain / discomfort / pins-and-needles / tingling /
> buzzing fragmenting one sensation across five rivals at
> +5.5/+2.5/+2/+1/+1, keeping `what` least-confident and drawing 22 of 42
> focuses). One missing structure, both costs. H moves to next-after-W1-E.

**Problem (A1, audit G4 — the single biggest cost in this round).** Fine
values fight the coarse value they refine; the weave stays vague; restarts
erase the fine challengers' kinda-signal while the coarse incumbent
survives.

**Proposal sketch (to be detailed when its turn comes; key decisions
flagged for iteration):**

- Per-slot edge map `refines[child] = parent`, derived deterministically at
  the moment a value first enters the board during replay (so undo stays
  pop-and-recompute): child mentions ≥ 1 content token of parent, or the
  formatter tags `refines` explicitly; ties broken toward the
  highest-scored candidate parent; depth capped (≤ 3).
- **Scoring stays additive and flat** (the honest tile unchanged); what
  changes is *reading* the board: a slot's confidence = its best *subtree*
  mass; the **weave value = the deepest descendant with own score ≥ 1**
  (the frontier), not the root. The dishes round then weaves "a specific
  cleanup task" by attempt 4 and "the dishes" at q93/q95.
- Drill directive descends explicitly: "the confirmed idea is ⟨parent⟩;
  test a more specific version like ⟨known children⟩ or a new one."
- Restart keeps the *subtree structure* of yes-confirmed nodes (fixes the
  erase-the-challengers effect, A5).
- Open question for iteration: does a child's yes propagate a fraction
  upward (subtree mass already covers reading; propagation would also
  shield parents from W1-B retirement edge cases)?

**Touches:** `facets` (edges, frontier, subtree reads), `reasoner`
(formatter tag + drill prompt), `dialogue` (weave + restart), recorder
(edges in board record), web reasoning tile (render frontier chain),
tests — the largest item in the queue. **Risks:** wrong parentage
(mitigation: edges affect reading, never scoring; a mis-parented child
still wins on its own score); complexity. **Acceptance:** dishes-round
replay weaves a specific value by the 3rd proposal; bench convergence Δ on
all three fixtures; no honest-tile regression.

### W3-I · Mass-scaled confidence checks — Status: PROPOSED

**Problem (audit F1/F2; A5).** Absolute thresholds (ready 2.0 / margin 1.0 /
floor −2) silently change meaning as the round grows.

**Proposal.** Keep raw scores everywhere visible; change only the *checks*:
`confident()`/`tied_top()`/retirement compare the leader's **lead fraction**
(margin ÷ slot's total absolute mass, floored) against thresholds, with the
old absolute behavior as the small-mass regime. Calibrate the two regimes so
every existing unit test still passes on short rounds (back-compat by
construction), then let the bench pick defaults for long rounds.

**Touches:** `facets` checks + `config`, tests. **Risks:** subtle — gated on
W2-G existing so the change is measured, not vibed. **Acceptance:** bench:
fewer late-round fail-loops at unchanged early-round behavior.

### W4-J · Fatigue-aware stopping — Status: PROPOSED

**Problem (audit G6).** "Never self-end" is right, but the engine happily
asks 95 questions; fatigue is a clinical cost the policy never sees.

**Proposal.** Once every non-retired core slot is confident and the weave is
stable (W1-C's comparison), modifier questions must justify themselves: stop
probing/drilling modifiers whose answers cannot change the weave (their
leader already woven, or muted via W1-D); surface a gentle cockpit cue
("ready to propose") so the caregiver chooses proposal timing. No hard stop;
the cap stays the only terminator.

**Touches:** `dialogue` + one cockpit cue. **Risks:** premature proposals —
mitigated: it's a cue, not a stop. **Acceptance:** simulated long rounds
propose within ≤ 5 queries of weave-stability instead of farming modifiers.

---

## 4. Parked (explicitly not in this queue)

- **Choice cards (either/or input)** — patient-surface phase; engine emits
  `choice` queries then (audit F6 — a forced half-split, theory-optimal).
- **Per-patient answer-noise calibration (full G2)** — needs more recorded
  rounds; the dishes round alone added 95 labeled answers. Revisit after
  W2-G exists and ~5 more rounds accumulate. The likelihood-ratio update
  machinery rides in with W3-I when it comes.
- **Full UoT-style EIG (answer simulation)** — W2-E's selection is the
  cheap version; escalate only if the bench says question quality is still
  the binding constraint after Waves 1–3.

## 5. Status board

| ID | Title | Status |
|----|-------|--------|
| W1-A | Rephrase default → 1 | **IMPLEMENTED** (owner-decided 06-11) |
| W1-B | Focus v3: retire/widen/rotate-on-stall | **IMPLEMENTED** (06-11, amended: ratio retirement, banded priority) |
| W1-C | The living proposal banner (Speak / ✓ / ✗-edits) | **IMPLEMENTED** (06-11; validated in trial 2 — banner moved up top + ready-glow strengthened per owner notes) |
| W1-D | Focus directives in the context field (shrunk by C) | PROPOSED · demoted (✗-flow absorbed it in trial 2) |
| W1-E | Per-topic priorities · body-aware seeds · replacement ✗-edits | **IMPLEMENTED** (06-11; body core → what+where) |
| W1-F | The synthesis editor (selectable segments · candidates · ⟳ Restate) | **IMPLEMENTED** (06-11/12, owner-designed; ✗-note UI retired, free-text path hardened) |
| W2-E | Candidates + tag rescue + pronoun fold | PROPOSED (§1d C2: **fifth sighting** — toes, right thigh, "hurting" all wasted on established tags). Ordered after W2-K |
| W2-F | Verify-on-lock + repeat exemption | **IMPLEMENTED** (06-11) — but see W2-L: §1d found it unsafe in production |
| **W2-G** | **Noise bench** | **IMPLEMENTED (09-08)** — gate **F2**: ε-noise, `--compare`, §1 metrics read off the round record, 15 tests. Fixed two defects in the instrument (unreachable accept gate, `not_sure`-biased simulator) |
| **W2-K** | **Value identity — anchoring + folding** | **IMPLEMENTED (09-08)** — drill-down restored; verified by replay (`right side › right leg › right thigh`) |
| W2-L | Verify-turn safety | **PARTLY IMPLEMENTED (09-08)** — wording fixed; the verify-no scoring question stays open (owner-decided, wants the bench) |
| W2-M | Caregiver-note fidelity | PROPOSED (09-08, §1d C4) |
| W2-N | Restart keeps the profile prior | **IMPLEMENTED (09-08)** — one line |
| **W2-O** | **Autopsy instrumentation** | **IMPLEMENTED (09-08)** — record carries seed context, per-query banner/timing/rejections, restart positions, pending question; **F2 precondition cleared** |
| W2-P | Focus/content gate + enumeration-axis guard | PROPOSED (09-08, §1d C7) — re-measure after W2-K |
| W2-Q | `yes_memory` integrity + same-session read-back | PROPOSED (09-08, §1d C7) |
| **W2-R** | **Draft does not follow the leader** | **IMPLEMENTED (09-09)** — drill-inferred edges, parented to the FRONTIER. Measured on the bench: round health up (farming yeses to zero, diagnostics halved), **convergence and readiness unmoved at 0/9**. Kept, not claimed as a win |
| W3-H | Refinement links (coarse→fine) | **IMPLEMENTED** (06-11); was **never engaging in production** (`board.edges` empty in all 8 rounds of §1d) — **unblocked by W2-K on 09-08**, verified by replay. Confirm on the next live trial |
| W2-S | Prompt-side re-ask reduction | **TRIED AND REJECTED (09-09)** — measured 3 ways; constraining the model more doubled diagnostics and early-ending rounds. Latency lever is not prompt-side. See §1e |
| W3-I | Mass-scaled confidence | PROPOSED |
| W4-J | Fatigue-aware stopping | PROPOSED |
