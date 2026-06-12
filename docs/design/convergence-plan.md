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

### W2-G · Noise bench — Status: PROPOSED

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
| W2-E | Candidates + tag rescue + pronoun fold | PROPOSED (B1: the right-leg yes wasted on established tags — third sighting) |
| W2-F | Verify-on-lock + repeat exemption | **IMPLEMENTED** (06-11; 3 clean fires in trial 2) |
| W2-G | Noise bench | PROPOSED |
| W3-H | Refinement links (coarse→fine) | **IMPLEMENTED** (06-11; non-negative family mass, frontier weaving, chains in the tile) |
| W3-I | Mass-scaled confidence | PROPOSED |
| W4-J | Fatigue-aware stopping | PROPOSED |
