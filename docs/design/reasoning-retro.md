
### 9. v2 plan landed (P0–P2) + the opposition button (2026-06-11)

The whole §8 plan is implemented and tested (asymmetric no-crediting,
what/how re-filing, informative-yes gates, Gate-4, the kinda/pin ladder, the
direction layer with the sign-flip nudge and the caregiver ask-order prior,
stalled-progress restart, futility guard, board/seed/restart recording; the
repeat gate's content-token overlap is Dice with the board's stem/prefix-
tolerant matcher, because the naive stemmer splits "comes"/"come").

**The opposition button** (owner-directed, 2026-06-11): the trial insight
"the question is right except for its direction" becomes a caregiver ACTION,
not an answer. `⇄ Opposite` (key `o`) re-renders the pending question with
its connotation reversed and keeps waiting: one fast `Reasoner.flip` JSON
call (no deliberate phase — a trigger, not a turn), primed with the bucket
mirror when the question classified into a direction. Code re-classifies the
flipped text, so answers credit the mirrored bucket automatically. The
superseded question never enters history or the board but still counts as
asked (the repeat gate spans it); an answered flip records `flipped_from`.
The repeat gate deliberately does NOT apply to the flip itself — a flip is a
near-duplicate of its source by design. Failure is soft: audit reject /
unreachable model leaves the pending question untouched (409 in the
cockpit). 176 tests pass.

**Literature audit (same day).** The June-10 research notes (20Q game
theory, LLM question-asking, SCA practice) are now a standalone illustrated
review + audit of this engine:
[`20q-research-audit.html`](20q-research-audit.html). Conclusions in brief —
architecture matches the literature (externalized belief, soft noise-tolerant
scoring, split directive, confirmation turns); the frictions are count-based
synthesis gates vs posterior-style stopping, absolute thresholds on an
unnormalized scale, no EIG at question level, a repeat gate that also outlaws
*verification* re-asks, and the rotation guard cutting drill ladders; the
ranked gaps are EIG candidate selection, an answer-noise model calibrated
per patient from the recorded dataset, verify turns, a coarse→fine ladder
within slots, a noisy-answerer bench, and fatigue-aware stopping.

**First v2 live round + the v3 queue (2026-06-11, 9:25 AM).** gemma4:e4b
**synthesized** the dishes target on `my_people` — but in 95 queries, 7
synthesis attempts, 8 restarts. Full autopsy + the prioritized,
owner-iterated fix queue: [`convergence-plan.md`](convergence-plan.md).
Headlines: the what-slot's coarse leader ("keeping our house tidy" +6)
could never be displaced by its own refinements down to "dishes" (+1), so
five proposals wove byte-identical vague slot sets; focus never returned to
`what` after q31 (non-core + positive leader = unreachable) while `who`
(+25.5) kept absorbing drills; the caregiver's "focus on WHAT, when doesn't
matter" note steered nothing and was itself mis-credited as `when: later`
+2; q93's decisive "dishes" yes was tagged onto established pairs and
wasted. The direction layer, ⇄ flips (used 5×), informative-yes flags, and
anchoring all behaved. W1-A (rephrase_limit default → 1) implemented per
the owner's same-day decision.

### 8. The 5W1H facet rebuild — consensus boards, restart recovery, no more banks (2026-06-10)

Two more live trials against the target *"I need Zach to help move a large
picture in the house"* (June 9 recordings, gemma3:12b + gemma4:e4b): both
failed-but-improved. gemma3/people round 1: **81 q, abandoned**; round 2:
synthesized after **58 q** ("Could you please help me lift this picture so
everything feels more peaceful?" — close, but who/what surfaced only at q~55).
gemma4/feelings: 40 q to a confirmed utterance; gemma4/people: aborted at 0 q.

**Autopsy — four mechanisms, all confirmed in the recordings.**

1. **The "Aaron" hallucination = slot mis-attribution.** q30 *"Is this
   arrangement about a one-time event?"* was formatted with
   `new_need: "I need to ask if Aaron is coming to visit soon"` — resurrecting
   the already-rejected h3 under a fresh id (n2) — and the yes credited n2.
   q35 *"…related to physically assisting you?"* → yes was ALSO tagged
   `yes_ids:[n2]`. So "Aaron visit" gained points from questions that never
   said "Aaron", became leader, and every synthesis baked "when Aaron visits"
   in. Root cause: **crediting was never anchored to the question text**, and
   a `new_need` could re-mint an eliminated candidate at zero.
2. **Verbatim repeats.** *"Do you want to talk to Zach about something
   important?"* asked **7×** (gemma3). §7 removed the hard `is_repeat` because
   a reject then dumped the round into the bank — proving prompt nudges alone
   don't stop gemma3 repeating.
3. **Canned bank questions are pure noise.** Transient reasoner failures
   walked the my_people bank mid-round (wife/husband/mother/…), and once
   exhausted the generic holding question looped **5× verbatim** ("Is it
   something you need help with right now?" → yes, ×5). The pre-overhaul
   6:16 PM trial looped "Do you want your wife?" **14×**.
4. **Lateral churn while a core unknown starves.** Round 2 spent ~20 q on WHY
   (look better / feel right / peaceful) while WHAT ("a picture") went
   unasked until q55 — nothing in the controller knew which *dimension* of
   the need was still open.

**The rebuild (this commit).** The single ranked-needs belief is replaced by a
**5W1H consensus board** (`agent/facets.py`): per slot — who / what / when /
where / why / how — contender values with the same additive scoring (+1 yes,
+0.5 kinda, −1 no to the asserted pair only; no normalization; floor −2;
caregiver context +2). What carried over from the old controller: additive
no-renormalization scoring, two-phase deliberate→format ask, explore-decay,
rephrase ladder, yes-gates, undo-as-replay, the honest tile. What's new:

- **Slot anchoring (the Aaron fix).** The formatter tags `slots` (≤2 pairs);
  each value must be *words the question says* (`facets.mentions`, stemmed +
  prefix-tolerant) or it is dropped; variants fold onto existing contenders
  (`canonical_value`); a no-tag question falls back to deterministic
  board-scan tagging. Rejected values stay on the board at negative scores —
  nothing can be re-minted fresh at zero.
- **Hard repeat gate, safely this time.** `auditor.is_repeat` (normalized +
  SequenceMatcher ≥ 0.85) hard-rejects rewords against EVERY question asked
  this round — safe now because there is no bank to fall into; persistent
  repeats raise into the restart recovery. `audit_query` also rejects leaked
  reasoning language ("the draft", "candidate", …).
- **Code-side focus policy** (`Round._pick_focus`): probe an unestablished
  core slot → split tied top contenders (the user-spec "scores still match"
  case) → drill the weakest leader / enrich an empty modifier slot; never the
  same slot >2 turns in a row (the WHY-hammering guard). Topics declare
  `core_facets` (people: who+how; feelings: what+why; body: what+how).
- **Synthesis weaves slot leaders** into ONE natural sentence — confirmed
  slots verbatim-ish, unknown slots placeholdered (someone/something/soon) or
  omitted, never a categorical dump; board-readiness (every core slot ≥
  `facet_ready_points` with ≥ `facet_split_margin` lead) joins the yes-gate
  as the early path.
- **One restart recovery, three triggers** (synthesis-exhausted, >10-no
  streak, reasoner fail-loop): dump every no/kinda influence; rebuild from
  caregiver context + **this round's** confirmed yeses (round-specific by
  construction — recovery no longer reads the session YesMemory) + fresh
  profile-free probes. Post-restart prompts hide the dumped noise; the repeat
  gate still spans the whole round, so a restart can't cause re-asks.
- **Banks deleted; diagnostics added.** Topic fallback banks are gone from
  the YAML/model/engine. A turn that fails through recovery emits a
  `diagnostic` event — reason, what was attempted, llm-unreachable flag — and
  the cockpit renders a failure card with **Retry** (`POST …/retry`); the
  reasoning tile renders the per-slot contender scores (`event.facets`)
  instead of the candidate list.
- **Preface fluidity** enforced in code: ≤64 chars, never its own question,
  dropped when ≥50 % of its content words restate the question, terminal
  punctuation normalized to an em dash so `preface + question` reads as one
  spoken line. Preface + slots + focus are now persisted per history entry,
  so future autopsies can actually see them (they were invisible before).

**20Q-research cross-check** (sources in the session log): UoT/multi-turn
planning work finds LLMs cannot track belief implicitly across turns —
externalizing state to code and handing the model a scoreboard each turn is
the supported design. EIG/split-in-half optimality applies cleanly to the
*split* directive; with a noisy answerer (Rényi–Ulam "liar" setting) single
answers must never hard-eliminate — kept (soft scores, floor at −2,
confirmation-seeking). Slot-filling dialogue literature matches the
per-category belief + "ask the lowest-confidence slot" policy and explicit
confirmation turns (our synthesis proposals). Aphasia SCA guidance endorses
general→specific yes/no laddering and warns against relying on yes/no alone —
the caregiver context channel and the kinda button are the compensators.
**Known gaps deliberately deferred:** no true EIG question *selection* (we
pick the slot in code but trust the model for the question itself); no
explicit taxonomy ladder within a slot (coarse↔fine contenders coexist and
fine ones must out-score coarse ones); answer-noise is modeled by weights,
not by a confusion model.

Net: −1 module (`hypotheses.py`), 135 tests pass (was 128), ruff clean,
cockpit builds. **Watch in the next trial:** does the board's what-slot fill
early on the picture target; do diagnostics ever appear in normal operation
(they should be rare); does gemma4:e4b's preface now read as one sentence.

**First facet-engine trials (2026-06-10 evening) + the v2 plan.** gemma4:e4b
**synthesized the picture target** ("I need Zach to come over later today so he
can help me move and hang up some of my artwork…", 37 q — first success on
this target). gemma3:12b failed "I need Rob to clean the kitchen" (52 q, 46
no / 2 yes / 4 kinda, ZERO synthesis proposals, abandoned). What worked: slot
tagging on 52/52 and 37/37 queries, no phantom subjects (anchoring held), no
canned questions, the fail-loop restart recovered gemma3 mid-round, prefaces
fluent when present. Three root causes found in the slot data, and the plan:

*P0 (fix the observed loops)*
1. **Collateral no-damage** — gemma3 tagged "Rob" on 29 NO answers (vs 4
   positives): every "tell/remind Rob about X?"→no subtracted from the
   CONFIRMED who anchor as well as the guessed content; Rob/spouse/remind all
   got buried → scorched-earth board → rudderless enumeration. Fix:
   asymmetric crediting — yes/kinda credit all tagged pairs; a **no subtracts
   only from the lowest-scoring tagged pair** (positives protected unless
   solely tagged). (Noisy-oracle 20Q: blame the marginal hypothesis, never
   destroy accumulated consensus on one answer.)
2. **what/how category confusion + confirmation farming** — gemma4 put
   actions in "what" ("help with tasks" +6 in WHAT) so "how" never
   established → the policy probed how 17× while the vague what-leader fueled
   the kinda-loop; Zach reached +7.5 on re-confirmations and zero-info yeses
   kept satisfying the resynthesis gate. Fix: slot one-liners in the FORMAT
   prompt + code re-map of verb-led "what" values to "how"; a yes only counts
   toward the synthesis gates if it was INFORMATIVE (credited pair below
   ready_points before); ask Gate-4 rejects questions whose every tagged pair
   is already a confident leader; is_repeat adds content-token Jaccard ≥0.8.
3. **Kinda-utterance loop** — 16 near-identical kinda utterances across 5
   attempts; the caregiver again had to type "FOCUS on WHAT". Fix: kinda ⇒ at
   most ONE rephrase, then back to questioning with focus FORCED to the
   weakest slot used in the rejected utterance (+ prompt note "close — pin
   down <slot>"); reject rephrases that near-dup a rejected utterance.

*P1 (the strategy gap gemma3 exposed)*
4. **The DIRECTION layer — coarse buckets + the "opposite" sign flip**
   (owner-directed, 2026-06-10). 52 questions never tested "Rob does
   something for ME": ≥25 of the noes were mirror-image questions (Paula
   doing/telling something FOR Rob) that were perfect for a flipped target —
   the engine read each as "wrong content" when the signal was "right
   person, wrong DIRECTION". Design:
   - my_people gets `direction: true`; the four intent buckets become
     standing "how" contenders with a code-level MIRROR map (I-do-for-them ↔
     they-do-for-me; tell-them ↔ ask-them).
   - A deterministic CODE classifier labels each question's direction from
     its text + the tagged who-name ("do you want ROB to…" = them-for-me;
     "do you want to bring ROB…" = me-for-them; none when ambiguous) —
     buckets are credited exclusively by the classifier, never by model tags
     (bucket phrases are stopword-heavy; mention-anchoring can't see them).
   - Crediting: yes/kinda also credit the matching bucket; a **no whose
     who-anchor is positive adds a +0.5 nudge to the MIRROR bucket** (the
     sign flip — a no on one pole of a binary attribute is soft evidence for
     the other pole; noisy-oracle discount keeps it at kinda-strength). The
     asserted bucket takes no collateral damage (P0-1 protects it).
   - **Caregiver prior, hardcoded as ASK-ORDER not score**: profile gains a
     `caregivers:` list; when the who-leader is a caregiver and no bucket is
     positive, the FIRST how-probe tests "they do something for me" (prompt
     note: caregivers offer care as tasks — test that direction first, do
     NOT assume it). No unearned points → the honest tile stays honest, and
     genuine concern ABOUT a caregiver (why/what contenders, e.g. "worried
     about Rob") is never suppressed — one no on the care-task probe and the
     flip evidence redirects normally.
5. **Stalled-progress restart** — sparse kindas kept resetting the 10-no
   streak (runs of 9/10/12). Add: restart when no pair has crossed +1 in the
   last N≈8 answered queries.
6. **Futility guard** — K≈4 consecutive noes on questions sharing one anchor
   pair ⇒ next directive bans that contender for a turn ("stop guessing
   remind-contents; test a different action type"). With the flip nudges the
   redirect has a destination: the mirror bucket is already rising.

*P2 (observability + regression)* — record seed values, restart snapshots,
and the final board in the round record; bench scenarios for both trial
targets; rationale jargon nudge. `scripts/dump_recording.py` now prints
slots/focus.

### 7. Trial autopsy + simplification — restoring gemma3 (2026-06-09)

Two live trials: **feelings/gemma4 succeeded** (40 q, 8 yes → synthesized; allowed
to drift from "confused about the TV" to the root cause "general confusion"). **people/
gemma3 failed** (56 q, abandoned) with the old artifacts back: dumb repeated questions,
no depth.

**Autopsy (per-round stats from the recordings).** Early gemma3 (06-07): **0 fallback
turns, 0 repeats**, synthesizing in 3-16 q. The failed round (06-10): **27 of 56 turns
were fallback, 19 repeats, 45 no**. gemma4 in the same trial: **1 fallback, 0 repeats**.
So gemma3's *reasoner* was failing almost every other turn and dropping to the bank
(which looped). Root cause = the **question audits** (§5, b1bdef2): `topic_violation`
under `my_people` requires a person-word/name, but the target *"I want Zach to move a
large picture"* forces object/action questions ("move the picture") → rejected →
retries exhausted → `ReasonerError` → fallback bank → loop. **The audit added to fix
redundancy was what caused it.** gemma4 survived because its two-phase deliberate pass
phrases on-topic and clears the audits.

**Simplification (the fix).**
- **Audits non-fatal.** The ask accepts its best-effort question after the retry
  budget; only `audit_query` (is it yes/no-answerable) is enforced. `topic_violation`
  and the `is_repeat` hard-reject are gone (`auditor.py` is now just `audit_query`);
  redundancy and on-topic fit are nudged via the prompt. A real, if imperfect, question
  always beats the looping bank.
- **Two-phase for every model.** Deleted the single-call `ASK_SYSTEM`/`ask_messages`
  path and the `is_thinking_model` gate; everyone deliberates then formats. `_format_
  question` salvages the question straight from the draft if the format pass is empty.
- **Fallback never loops the bank** — a single neutral holding question when exhausted
  (and it is rare now that audits don't force it).
- **Synthesis gate = readiness OR yes-count.** A dominant leader (margin ≥
  `readiness_margin`) with ≥ `new_yes` confirmations synthesizes early, restoring the
  fast convergence gemma3 had before the hard 5-yes gate. First synth still needs
  `min_yes`; later attempts (incl. post-reseed) need `new_yes`.
- **Preface flows into the question** as one cohesive spoken read.

Net **−200 lines**. 113 tests pass, ruff clean. **Still open:** TTS drops randomly (its
own reliability pass). **Next:** re-incorporate hierarchical zoom + deep-research
alternative strategies on this simplified base.
 2 |
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

### 4. Additive point scoring + drill-down; gemma4:e4b default (2026-06-08)

A live trial with a KNOWN hidden target ("pain in my right big toe") exposed that
the §1–3 model still failed: it synthesized prematurely then dropped to fallback,
over-fit the patient profile, and gave up after ~10 questions. Recording:
`patient_data/Paula/a2d9...jsonl`. Owner's three directives drove a third model:

- **Additive points, no normalization** (`hypotheses.py` rewrite). Scores
  accumulate: yes +1, kinda +0.5, **no −1 to the targeted need ONLY** — a "no"
  never promotes the leader (the normalized model let it win by elimination).
  Scores need not sum to 1; the 0.65 concentration threshold and the balanced
  -split requirement are gone.
- **Synthesis only after ≥5 "yes" confirmations**, and the round **never
  terminates early** — the no-progress terminator was removed. It keeps drilling
  until the gate is met (or topic change / ceiling). Synthesis builds the utterance
  from the *confirmed trail*, not just the leader label.
- **kinda = "warm" drives specificity.** `ask` reads the yes/kinda trail and asks
  one step MORE specific (body → leg → foot → big toe); narrow questions are
  encouraged. The `clarify`/deepen split, the balance check, and the id-based
  redundancy guard were removed (they blocked legitimate same-need drilling).
- **Seeds mix generic + specific** and deliberately do NOT over-fit the profile.

**Validation (sim, sharpened oracle).** gemma4:e4b rode leg → ankle → foot →
toes → big toe, hit 5 yeses, synthesized "My big toe hurts, …" — the target,
which was never seeded. gemma3:12b wandered laterally and never converged.
**→ default changed to gemma4:e4b** (it drills; gemma3 does not), other models
still available. Open risks (rounds can run forever; kinda never reaches the gate;
anchoring can lock a wrong warm area; model-dependent drilling) are tracked in
`tool-summary.html`.

### 5. Question audits: redundancy, on-topic, exploratory ratio, kinda-variation (2026-06-08)

A trial (target "scared and sad because confused about my environment") showed
reworded repeats, profile fixation, an off-topic body question under feelings, and
wasted "kinda"s. Four hardcoded guards (owner's directives):

- **Redundancy** (`auditor.is_repeat` + content-word Jaccard) PLUS an explicit
  "ALREADY ASKED — do not reword these" list in the prompt (the LLM dedups
  semantics better than keyword overlap). The reworded-repeat pattern is gone.
- **On-topic** (`auditor.topic_violation`): feelings reject body words; people
  require a person reference. Off-topic questions are re-prompted.
- **2:1 exploratory:context** (`dialogue`: `query_count % 3 != 2`): 2 of every 3
  turns drop the profile and push a new avenue; the 3rd may use it.
- **kinda → variation**: warm questions are fed back as "ask a fresh variation,
  never a reword."
- Prompt now drives SUBJECT → ACTION → context-specific MODIFIERS.

**Validated mechanically** (gemma4:e4b): questions came out varied, on-topic, and
non-reworded. **But a gap surfaced:** a question had to map to an EXISTING
(profile-seeded) candidate, so exploration couldn't introduce an unseeded need.

### 6. Seed ceiling removed — exploratory questions spawn candidates (2026-06-08)

Fix for §5's gap. A question may now carry a `new_need` (first-person text) when it
explores a need NOT in the candidate list (leave `yes_ids` empty). `ask` mints a
fresh id (`n1`, `n2`, …); on a **yes/kinda** `_replay_belief` spawns it as a real
candidate (a **no** discards it). Exploratory turns are encouraged to use it. Plus
`SEED_SYSTEM` now mandates **≥ half the seeds be GENERIC** (not profile-derived).

**Validated:** live, the model spawned `n1`/`n2` for needs that were never seeded —
exploration escapes the seed set (confirmed) — and the new-need spawn/discard is
unit-tested. **Remaining risk:** once it spawns a warm-but-WRONG avenue, anchoring
can lock onto it (same lock risk, now for self-spawned needs). Whether the model
picks the *right* new avenue is an LLM-quality question best judged in a real
caregiver trial — a keyword oracle can't fairly score emotional nuance.
