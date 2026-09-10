# Beta Retool — Design Doc

**Status:** approved 2026-05-18. `ROADMAP.md` and `CLAUDE.md` rewritten to match;
auto-memory updated. This doc is now the live plan for the beta.
**Supersedes:** parts of `docs/ROADMAP.md` Phases 0–2 and three sections of `CLAUDE.md`
(see §13).

This captures the design conversation that followed the first trials of the Phase 1
CLI MVP. It is the source of truth for the retool; once approved, `ROADMAP.md`,
`CLAUDE.md`, and the auto-memory get rewritten to match.

---

## 1. Why we are retooling

The Phase 1 CLI MVP works, but trials surfaced a different shape for the product:

- The real interface is a **caregiver cockpit**, not a patient kiosk. The beta is
  caregiver-driven; the patient is present but never operates the cockpit.
- The tool must **accumulate patient-specific context over time** — this contradicts
  the current "rounds do not persist" philosophy and needs a principled rewrite.
- Recorded interactions are a **future training dataset**, not just an audit log.
- The product is a **context-driven speech emulator** for one specific patient — not a
  global, universal, or medical tool. It is a digital voice; it must reflect the
  patient, not a clinical average.

---

## 2. Locked terminology

The session/round/pass terminology is replaced by a three-tier scale. **"Pass" is
retired.**

| Term | Definition |
|------|-----------|
| **Session** | One open→close of the tool. (Previously called a "round.") |
| **Round** | One convergence attempt under a single high-level context (topic). Ends on **synthesis**, topic change, query-budget exhaustion, or session end. |
| **Query** | One generated question within a round. |

Nesting: **Session ⊃ Rounds ⊃ Queries.**

- A round **ends on synthesis.** The query budget (the old "20") is a *soft safety
  cap*, not the primary terminator — caregiver and patient patience set the real
  length. Branding aside, this is no longer literally a 20-questions game.
- Re-selecting the same topic after a synthesis starts a **new round, same topic**.
  Changing the topic mid-round ends the current round (recorded as abandoned) and
  starts a new one.
- The ordered sequence of round topics within a session is itself signal — see §7.4.

---

## 3. The system, restated

We are building a **context-driven speech emulator** for one patient. Architecture:

- The **caregiver cockpit** is the *primary* interface to the emulator backend.
- An **aphasia-oriented UX** is a *secondary, supplemental input* surface that feeds
  the cockpit. (Built after the cockpit — see roadmap.)
- The patient **never operates the cockpit.** Communication is always caregiver +
  patient together; that is the entire point.
- "Training a custom LLM" means **accumulating conditioning context** (profile +
  knowledge graph + recorded dataset) around a fixed local base model — *not*
  fine-tuning weights. Weight fine-tuning (path "b") is explicitly deferred for
  budget reasons; the recorded dataset (§8) is the groundwork that makes it
  possible later.

---

## 4. Mode model

Two **orthogonal** axes — a 2×2, not a 1×4:

| Axis | Values | Meaning |
|------|--------|---------|
| Reasoning vs. fallback | `reasoning` / `fallback` | Is the LLM reachable? |
| Training vs. operational | `training` / `operational` | Do we trust the input? |

- **Training mode** — caregiver-driven, inputs assumed reliable. The caregiver +
  patient + tool discover the need together. **Beta builds training mode only.**
- **Operational mode** — patient drives the tool solo, inputs noisy, leans on the
  graph as a prior. Deferred. It still plays the full game; it is not a
  graph-lookup shortcut.
- `reasoning`/`fallback` *(revised 2026-06-10)* — there are **no canned fallback
  questions** anymore. A reasoning failure triggers the round's restart recovery
  (dump no/kinda influence; keep caregiver context + the round's confirmed
  yeses); if that fails too, the cockpit shows a diagnostic card with a Retry.
  "Fallback" now only labels the no-LLM-configured state.

**"Testing" is not a fourth mode.** It is training mode run against a *synthetic
persona* for development. The distinguishing flag is **"is a real patient profile
loaded?"** — see §5.

---

## 5. Privacy invariants

These are binding. The recorded dataset (§8) is the most sensitive artifact in the
system; it must physically be unable to reach a cloud backend.

A single flag — **`real_patient_profile_loaded`** — gates two behaviors:

| `real_patient_profile_loaded` | LLM backend | Recording |
|---|---|---|
| **true** (real patient) | **Local only** (Ollama). Anthropic backend unavailable. | **On** (caregiver may pause). |
| **false** (synthetic persona) | Anthropic/Opus permitted. | Off — not patient data. |

Invariant: **recording ⟹ real patient ⟹ local LLM.** The cloud backend and the
recorded dataset can never coexist.

- The Opus/Anthropic backend exists **only** for development trials against synthetic
  personas — to iterate on agentic-workflow logic with a strong model.
- Pausing recording governs *storage* only; it does **not** re-enable the cloud
  backend (the patient is still present). Two separate controls.
- Profile data, graph, and recorded dataset live encrypted, on-network, and are
  caregiver-purgeable. The old "no PII stored" principle is **obsolete** (see §13).

---

## 6. The caregiver cockpit

A rich-but-simple web interface, served by a local server. Caregiver-only — the
binding aphasia UX constraints in `CLAUDE.md` apply to the *future patient
interface*, not this cockpit.

### 6.1 Layout

```
┌─ [ Topic ▼ persistent ]            [● REC / ⏸] ──────────────┐
│ ┌─────────────────────────┐ ┌──────────────────────────────┐ │
│ │ 1. Conversation /        │ │ 2. AAC pictogram             │ │
│ │    question stream +     │ │    (current question)        │ │
│ │    history               │ ├──────────────────────────────┤ │
│ │                          │ │ 3. Live LLM reasoning        │ │
│ ├─────────────────────────┤ │    "exploring … because …"   │ │
│ │ 4. [y][n][k][s][q]  ...  │ │                              │ │
│ │    [ text input ][send][↶]│ │                              │ │
│ └─────────────────────────┘ └──────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

### 6.2 The proposal banner + tiles

0. **The living proposal banner** *(owner design, 2026-06-11 — sits above the
   tiles, directly under the topbar; the ONLY way a round concludes in
   success)*: the evolving draft utterance, populated from the first
   converged core slot with ambiguous alternates and an ellipsis ("I
   need/want something for/from Rob …"), glowing *Pending synthesis…*
   before that, and a breathing accent halo at board-readiness. Controls:
   **🔊 Speak**, **⟳ Restate** (same content, different words), **✓ accept**
   (explicit confirmation modal — dimmed backdrop, utterance spoken, one
   "New round" action). The woven segments are **clickable — the synthesis
   editor**: candidate dropdown from the board, typed replacement
   (refine-or-replace: extensions deepen the draft via refinement links;
   swaps strike the old value), or ✕ remove (slot mute). Engine-initiated
   synthesis no longer exists. Detail: `convergence-plan.md` W1-C/W1-F.

1. **Conversation / question stream** — the round transcript: questions, answers,
   and caregiver context inputs, in order. Streamed.
2. **AAC pictogram** — *shelved* (curated retrieval mostly fell back to "?" in
   real sessions; component + retrieval retained for a future generator-driven
   re-mount). The spec below is kept for that revival: the curated pictogram
   that best matches the current question's intent. **Retrieved**, never
   generated (see §6.4).
3. **Live LLM reasoning** — human-readable narration of the reasoner's decision
   ("exploring the notion of …"). Renders the `rationale` field the reasoner already
   emits, plus explore/exploit move and on-topic audit status.
4. **Input** — quick-answer buttons `y / n / k / s / q` (yes / no / kinda / not sure
   / quit) **and** undo `↶`, plus a free-text field with `send`. The buttons relay
   the patient's answer; the text field is the caregiver's **guiding-context**
   channel only (proposal edits go through the banner's synthesis editor).
   Also the **opposition button** `⇄ o` — an *action, not an answer*: it re-renders
   the pending question in its opposite connotation (who-does-for-whom mirrored via
   the direction buckets, or the key detail reversed) and keeps waiting. The
   superseded question still counts as asked for the repeat gate; the answered
   flip records its origin (`flipped_from`) in the dataset.

### 6.3 Persistent topic dropdown + recording light

- **Topic dropdown** — selects the high-level context. Visible and persistent for the
  whole round. Sourced from an easily-editable topic list (§6.5).
- **Recording light** — on whenever a real patient profile is loaded. Doubles as a
  **pause control**: the caregiver can pause capture for a sensitive exchange;
  paused queries do not enter the dataset.

### 6.4 Pictograms — retrieved, not generated

`CLAUDE.md` locks "no on-the-fly image generation," and that holds. Tile 2 maps
question intent → the closest pictogram in the curated ARASAAC / Mulberry set;
a neutral placeholder shows when nothing matches well. Rationale: a training tool
needs *consistent* symbols so the patient learns a stable visual vocabulary —
varying generated images would undermine the reinforcement.

### 6.5 Topic list

The deep `taxonomy/data/tree.yaml` is retired as the primary structure. It is
replaced by a **flat, easily-editable topic list** (YAML) — the high-level contexts.
Each topic declares its **core 5W1H facets** (`core_facets` — the slots that must
be determined before a synthesis is board-ready) and an optional reasoning hint.
*(The per-topic fallback question banks were removed 2026-06-10 — the June-9
trials showed bank questions are pure noise mid-round, and the bank-exhausted
holding question looped verbatim. Failures surface diagnostics instead.)*
Emergency short-circuit behavior is retained unchanged.

---

## 7. Round lifecycle & dialogue flow

### 7.1 Synthesis-terminated rounds

A round runs queries until the reasoner's synthesis confidence crosses a threshold,
then produces a **synthesized utterance** — the candidate "voice" output. This is a
new first-class artifact, distinct from questions: it must have its own sanitizer
and must be **caregiver-confirmed** before it counts as a successful round.

### 7.2 Rewindable history & undo

The session state machine must be a **navigable, branchable turn history**, not a
linear log — built this way from the start of the retool even though the undo *UI*
is minimal in the beta.

- Undo rewinds to an arbitrary prior query; **everything downstream of it is
  discarded** (later questions were conditioned on the now-changed answer).
- In reasoning mode, undo reconstructs the LLM prompt history *without* the undone
  turn — not just a UI hide.
- **Nothing writes to the graph until a round is finalized**, so undo within a round
  is free. (The graph is also only ever written via the interview tool — §9.)
- A heavily-undone query is a signal that the question was confusing — logged.

### 7.3 Mid-round caregiver context

The tile-4 text field is the caregiver's context-injection channel. Typed context
is threaded into the reasoning prompt for subsequent queries and recorded as
first-class dataset content (it reveals what information the model lacked).

### 7.4 Loop detection (cheap version — in beta)

The 3-tier log records the round→topic sequence for free. The cockpit shows a simple
indicator — "topic revisited: People ×3." When it fires it can suggest context to
the caregiver ("the patient keeps returning to People and Health"), which flows into
the existing tile-4 channel. The *expensive* version — the reasoner generating
bridging queries across oscillating topics, and topic-correlations feeding the
graph — is deferred (§9, roadmap).

---

## 8. Three-tier logging / dataset schema

In **training mode with a real patient**, interactions are recorded as a
**training-dataset-grade interaction trace**, not a thin audit log. One dataset per
patient ("patient-centric"). It serves three consumers:

1. The caregiver interview tool (graph curation — §9).
2. The future per-patient training set (deferred path "b").
3. The Job-B trial metric (§9).

Structure mirrors the terminology — **session → round → query**:

- **Session** — id, patient id, timestamp, mode, backend/model + prompt version.
- **Round** — topic, ordered queries, outcome (`synthesized` / `abandoned` /
  `topic-changed` / `budget-exhausted`), the synthesized utterance, caregiver
  confirmation (correct / not), **query-count-to-synthesis**, Job-B score.
- **Query** — question text, answer (`y/n/k/s`), any caregiver context typed before
  it, undo events.

In **testing** (synthetic persona) nothing is recorded; ephemeral trial metrics may
still be captured for Job-B comparison but are not part of any patient dataset.

---

## 9. Job A / Job B — success and the knowledge graph

The earlier single "success metric" is split into two distinct jobs.

### 9.1 Job B — trial-comparison metric (cheap, low-stakes)

A per-round scalar to compare prompt/agent variants during Opus trials. Demoted from
"verdict" to "triage pointer" — its only operational use is flagging which rounds the
interview tool surfaces first.

```
Q_round = W_final · (1 if caregiver-confirmed else 0)  +  (Σ answer_weights) / n_queries
```

Answer weights: `yes +2`, `kinda +1`, `no −2`, `not sure 0`. Per-query normalization
keeps it measuring *quality*, not round length. `W_final ≈ 10` so "did we win"
dominates, with the affirmation path as tiebreaker.

### 9.2 Job A — the knowledge graph (sensitive, human-curated)

- The graph is **written only via a caregiver interview tool** — there is **no
  automated write path**. This makes automated graph poisoning structurally
  impossible.
- The interview is **caregiver-initiated, and the tool nudges after ~10 logged
  rounds.**
- The interview **opens with open prompts** ("what worked, what was missed, what
  didn't make sense?") so the caregiver free-associates first — unprompted recall is
  itself signal — **then the LLM grounds and cross-checks against the logs**,
  walking the caregiver through actual recorded rounds and proposing concrete graph
  edits the caregiver approves or rejects.
- Graph growth model: an edge's weight is a **decayed count of caregiver-confirmed
  occurrences** — recency-weighted so it tracks how the patient changes. Failed
  rounds write nothing.
- The interview tool displays an **interactive graph visualization** that builds as
  the caregiver approves edits (`cytoscape.js` / d3-force) — doubling as the
  caregiver's picture of what the tool knows about the patient.

The graph data model is designed *with* the interview tool, in a later phase. The
beta only has to **log in a graph-friendly shape** (§8).

---

## 10. LLM backends

The existing `LLMBackend` Protocol (`src/my20q/llm/base.py`) already abstracts this.

- **Ollama** — existing, the production/real-patient backend.
- **Anthropic (Opus)** — new `LLMBackend` implementation, for synthetic-persona
  development trials only. Hard-gated by `real_patient_profile_loaded` (§5).

Known refactor: `DialogueSession` is currently synchronous (`_run_sync` errors
inside an event loop). The FastAPI cockpit needs the async path wired through.

---

## 11. Restructured roadmap (proposed)

| Phase | Was | Now |
|-------|-----|-----|
| 0 | Scaffolding ✅ | unchanged |
| 1 | CLI MVP ✅ | unchanged (historical; its dialogue engine seeds the cockpit) |
| **2** | Web UI / PWA | **Caregiver Cockpit (beta)** — FastAPI + web cockpit, training mode, 4 tiles + topic dropdown + recording light, flat topic list, mid-round context, rewindable history/undo, 3-tier logging/dataset, Job-B metric, Ollama + Opus backends with the profile gate, minimal patient-profile loader. CLI retained as a dev harness. |
| **3** | Clinical iteration | **Interview tool + knowledge graph** — Job A: caregiver interview, graph data model, interactive graph viz, expensive loop-detection (bridging queries). |
| **4** | Native app | **Patient operational interface** — the aphasia-oriented secondary input as a real surface, operational mode, graph-driven inference. |
| **5** | — | **Clinical & usability iteration** — TTS (`piper`), full patient profile, latency budget, caregiver review. |
| **6** | — | **Native Linux tablet app** (stretch). |

Note: a *minimal* patient-profile loader moves up into Phase 2 because the
`real_patient_profile_loaded` flag is core to the privacy invariants. The full rich
profile stays in Phase 5.

---

## 12. Beta (Phase 2) scope checklist

**In:** FastAPI local server; web cockpit (4 tiles, topic dropdown, recording light);
training mode; flat editable topic list; reasoning + fallback; mid-round caregiver
context; rewindable history + undo; synthesis-terminated rounds + utterance
sanitizer + caregiver confirmation; 3-tier logging/dataset; Job-B metric; cheap
loop-detection indicator; Ollama + Opus backends + profile gate; minimal profile
loader; pictogram retrieval.

**Out (later phases):** knowledge graph + interview tool; graph viz; operational
mode; aphasia-oriented patient input surface; bridging-query loop detection; TTS;
weight fine-tuning; native app.

> **Reconciled against what shipped — 2026-09-10 (gate F4).** Four line items
> above did not land as written:
>
> - **"4 tiles" → three, under the living proposal banner.** The banner (§6)
>   was designed after this checklist and became the only synthesis path.
> - **"pictogram retrieval" → SHELVED, not delivered.** Retrieval mostly fell
>   back to "?", so the tile is unmounted. Reviving it means either fixing
>   retrieval or *generating* imagery — and generating reopens the locked
>   "retrieved, never generated" decision and needs its own privacy review.
>   Tracked in §15, not here.
> - **"synthesis-terminated rounds" → caregiver-terminated.** The engine never
>   proposes; the caregiver accepts the banner draft (✓). "The only model-side
>   terminator" no longer exists.
> - **TTS moved from Out to In.** Local piper/kokoro readouts shipped, and the
>   09-09 trial confirmed them on the caregiver's iPad.
>
> Everything else in **In** landed. Nothing in **Out** was pulled forward
> except TTS.

---

## 13. Documentation debt — DONE (applied 2026-05-18)

- ✅ `CLAUDE.md` — rewritten: two-layer Dialogue Philosophy, the training/operational
  mode axis, Privacy & Safety with the §5 invariants ("no PII stored" removed),
  session/round/query terminology, caregiver-cockpit architecture.
- ✅ `docs/ROADMAP.md` — restructured to the §11 phase plan.
- ✅ Auto-memory `project_dialogue_philosophy.md` and `project_my20q.md` — updated.

---

## 14. Decisions made for review — veto freely

1. **Topic structure** — flatten the deep `tree.yaml` into a flat editable topic
   list; each topic carries an optional small fallback question bank.
   *(Superseded 2026-06-10: the banks are removed — failures surface diagnostic
   cards; topics carry `core_facets` instead. See reasoning-retro.md §8.)*
2. **On-topic audit** — *block* (silently re-prompt the model on drift) **and**
   *surface* the event in the reasoning tile.
3. **Topic dropdown mid-round** — changeable; a change ends the current round
   (recorded `topic-changed`) and starts a new one.
4. **Recording light** — also a pause control, not just an indicator.
5. **Job-B weights** — `W_final ≈ 10`, per-query normalization (§9.1).
6. **Emergency short-circuit** — retained unchanged.

---

## 15. Backlog — raised during the Phase 2 build

Items surfaced after the plan was approved; not yet scheduled into a phase.

- **Emotional sliders** — beneath the reasoning tile in the cockpit, a row of
  10–12 click-drag sliders, each spanning a pair of opposed emotions
  (e.g. `Sad ——O—— Happy`). Lets the caregiver register the patient's
  emotional state as steering signal alongside the dialogue.
- **Recording compaction** — the per-patient dataset grows with use. The
  cockpit now monitors its size against a configurable threshold (§8, the
  recording-light readout); when the threshold is routinely exceeded, add
  periodic compaction (summarize / roll up old sessions) rather than
  letting files grow unbounded.
- **On-topic LLM auditor** — the *format* auditor (single yes/no queries,
  no either/or) ships in the beta; a *topic-drift* judge needs an LLM call
  and is deferred (`agent/auditor.py` is where it lands).
- **Emergency false-alarm metric** — the tool must NOT attempt to *perceive*
  emergencies; an LLM cannot make that safety call — it is the caregiver's
  alone. But the caregiver can mark a recorded emergency round as a false
  alarm. The false-alarm *rate over time* is a human-mistake metric: a
  monotonic rise may indicate tool misuse, or be an early signal of a
  genuinely developing situation worth caregiver attention.
- **Metrics over time** — a caregiver-facing trend view: emotional metrics
  (from the emotional sliders) and the false-alarm rate plotted over
  sessions, so the caregiver can spot developing patterns.
- **Emotion-weighted conditional sampling** — the emotional sliders
  currently reach the reasoner as plain prompt context. The intended next
  step is a conditional sampling scheme that uses the slider *weights* to
  bias query generation toward the patient's emotional state — a more
  principled mechanism than prompt text alone. To be iterated on next.
- **Scheduling rounds (owner-requested 2026-08-11).** A scheduling-specific
  mode of play: converge on scheduling logistics / event planning and
  synthesize in that register. Post-beta; the requirement and its open
  questions are recorded in [`scheduling-rounds.md`](scheduling-rounds.md).
- **Pictogram tile shelved; image slot → generator (under review).** In
  real sessions the curated ARASAAC retrieval mostly fell back to "?", so
  the pictogram tile is unmounted from the cockpit (the `PictogramTile`
  component and the backend retrieval are kept for easy re-enable). The
  proposed future is to drive the image slot from an actual image
  generator. **This reopens a locked decision** — "imagery is *retrieved*,
  never *generated* on the fly" — and brushes against "LLM output is never
  shown raw to the patient": a generator emits unvetted imagery to a
  vulnerable patient. For a *real* patient it must run **locally** (no
  cloud image API) to hold the privacy invariant. Options to weigh: local
  generation; pre-generate + cache a caregiver-vetted per-patient set; or
  simply make retrieval work (fetch the ARASAAC PNGs via
  `scripts/fetch_icons.py`). Not finalized — see the task backlog.

---

## 16. Remote operation on cerberus (Tailscale + tmux)

Cerberus is the home server; the caregiver browser reaches it over the
Tailscale VPN. Day-to-day operation lives in a tmux session so the
caregiver can disconnect without killing anything.

**Two tmux windows**:

1. The server itself: `python -m my20q.api`. Bind to the loopback
   (`MY20Q_API_HOST=127.0.0.1`, the default) — Tailscale fronts it.
2. The Tailscale serve route: `tailscale serve --bg https / http://127.0.0.1:8000`
   (or `tailscale funnel` if you want it reachable beyond the tailnet —
   keep it off by default).

**Clean shutdown — the supported sequence**:

1. In the server window: a single Ctrl+C. Uvicorn enters graceful
   shutdown; the lifespan hook (`api/app.py::_lifespan`) sets the
   shutdown event and pushes a sentinel into every active SSE
   subscriber queue, so each `/api/.../events` generator wakes from
   `queue.get()` and exits its loop. The process exits within a
   second or two even with open EventSource streams.
2. As a backstop, `uvicorn.run(..., timeout_graceful_shutdown=5)` (in
   `api/__main__.py`, overridable with `MY20Q_API_GRACEFUL_TIMEOUT`)
   force-closes anything still in flight after 5 seconds.
3. Tear down the route: `tailscale serve --bg --remove` (or
   `tailscale serve reset`). Then detach/kill the tmux session.

If the first Ctrl+C ever appears to hang past ~6 seconds (it shouldn't),
a second Ctrl+C triggers uvicorn's force-exit immediately. As a
last resort over SSH:
`pkill -INT -f "my20q.api"` then `pkill -KILL -f "my20q.api"`.
