# Scheduling rounds — logistics & event planning

> **Status: NOTED (owner-requested 2026-08-11). Not yet iterated, not yet
> designed, nothing implemented.** Recorded here so the requirement is not
> lost while Phase 2 is finalized. Per the repo protocol
> ([`convergence-plan.md`](convergence-plan.md)), this gets iterated with the
> owner one proposal at a time before any implementation.

## The ask

The end user wants a **scheduling-specific mode of play**: rounds whose job is
to converge on *scheduling logistics or event planning*, and to synthesize an
utterance in that register — arranging a thing in time, with people, at a
place.

Targets look like:

- *"Aaron's birthday dinner is Saturday — can we do it at our house?"*
- *"Remind Rob the appointment moved to Tuesday morning."*
- *"I want to go to the store before the visit this afternoon."*
- *"Can someone take me to the doctor next Thursday?"*

This is distinct from the existing `my_people` topic, which converges on an
*intent toward a person*. Scheduling converges on an **arrangement**: a thing,
a time, and usually people and a place, bound together.

## Why this is not just another `topics.yaml` entry

A new topic entry is the *starting point*, not the whole feature. Three
structural notes, grounded in what the trials already showed:

**1. It inverts the facet priorities the engine has been tuned around.**
Every current topic treats `when` as a low-priority modifier — `my_people`,
`physical_health`, and `mental_health` all rank it 5th; `general` ranks it
last. That was deliberate: the 06-11 dishes autopsy counted ~6 "when-farming"
queries as pure waste (§1, A3), and the caregiver's *"the 'when' does not seem
important"* note is one of the motivating failures in the plan. A scheduling
round wants roughly `core_facets: [when, what]` with `who` close behind — the
first topic where `when` carries the round.

**2. `when` fragments worse than any other slot, and the fix already exists.**
The dishes round split `when` across later / today / after-dinner /
by-the-end-of-the-day at +3/+1/+1/+1 (§1, A4) — four rivals for one time.
Time is *natively hierarchical* (`this week › Saturday › the evening`), which
is exactly the coarse→fine structure **W3-H refinement links** already
implements. Scheduling should be the topic that exercises W3-H hardest, and is
a good validation target for it. It likely also needs `when`-specific
canonical folding (tomorrow/Saturday/the 14th may denote one time) — the
`when` analogue of W2-E's pronoun folding.

**3. Event planning may exceed one-value-per-slot.** "Dinner at our house on
Saturday with Aaron and Julie" is a *frame* with multiple bound participants,
not a single winning value per slot. The current board holds one leader per
facet. Whether scheduling can be served by the existing board (probably yes
for simple logistics: one thing, one time, one person) or needs multi-value
slots (probably for real event planning) is **the first question to iterate**,
because it decides whether this is a data change or an engine change.

## Open questions for iteration (do not pre-answer)

1. **Scope split** — is v1 "simple logistics" (one arrangement, reuses the
   existing board) with event planning deferred? Recommended framing, but the
   owner's call.
2. **Relative vs absolute time** — the patient means "next Thursday"; the
   caregiver needs a date. Does the synthesized utterance resolve relative
   time (needs a clock/calendar reference), or stay in the patient's own
   relative register? This has a privacy dimension if it ever touches a real
   calendar.
3. **Board shape** — single-leader slots vs a participant list for `who`.
4. **Does it earn its own topic, or a flag on existing ones?** A scheduling
   *aspect* can arise inside a `my_people` round ("remind Rob — Tuesday").
   Separate topic, or a mode that can be switched on mid-round?
5. **Calendar integration is explicitly NOT assumed.** Reading or writing a
   real calendar is a new external surface and would need its own privacy
   review against the §5 invariants. Out of scope unless the owner asks.

## Placement

This is **not** Phase 2 scope and must not gate the Phase 2 merge (see
[`phase2-finalization.md`](phase2-finalization.md)). It slots after the beta
merge, and benefits from the **W2-G bench** existing first so a `when`-centric
topic can be measured rather than judged by a single trial.
