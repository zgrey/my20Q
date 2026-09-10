# my20Q

A **context-driven speech emulator** for one specific person with aphasia. A
caregiver and patient use it *together*: it plays a short question-and-answer
dialogue — pairing each question with AAC-appropriate imagery — to converge on
what the person needs to express (a need, a feeling, a request for help) and
synthesizes that as a short spoken utterance.

It is **patient-specific, not global or medical** — a digital voice that
reflects one person, not a clinical average.

**Clinical intent**: assistive, **not** diagnostic. It helps a person
communicate a need to a caregiver — never medical advice, triage, or treatment
recommendations. All inference runs locally for a real patient; nothing leaves
the host.

See [`CLAUDE.md`](CLAUDE.md) for architecture/constraints,
[`docs/design/beta-retool.md`](docs/design/beta-retool.md) for the live design,
and [`docs/ROADMAP.md`](docs/ROADMAP.md) for the phased plan.

## Status

> **Parked 2026-09-09, awaiting a live trial.** `phase2-cockpit` is 78 commits
> ahead of `main`, 0 behind, and pushed. All green: **283 tests / 2 skipped**,
> ruff and cockpit typecheck clean. Phase 2 is feature-complete but **not yet
> merged** — the bar and the remaining gates are in
> [`docs/design/phase2-finalization.md`](docs/design/phase2-finalization.md).
> **Resume there**, not from `docs/ROADMAP.md` (its Phase 2 entry is stale).
>
> The bar is **F1 → F1.5 → F2 → F3 → F4 → merge**. Three gates are cleared:
>
> - **F1 ✓** — trials sat 08-31 / 09-01; autopsy in
>   [`convergence-plan.md` §1d](docs/design/convergence-plan.md).
> - **F1.5 ✓** — **W2-K**, the value-identity fix. Fine values were collapsing
>   onto their coarse incumbent, which capped the board at seed granularity,
>   wrote wrong values into the recorded dataset, and — by making a tagged
>   child equal its own named parent — made every refinement edge get dropped.
>   **Refinement links had therefore never once engaged in production.** Fixed
>   and verified by replay; **W2-L** (verify wording) and **W2-N** (restart
>   keeps the profile prior) rode along.
> - **F2 ✓** — **W2-G**, the noise bench, after its **W2-O** precondition
>   (autopsy instrumentation) landed. Engine changes are now *measured* rather
>   than argued about.
>
> **The next action is a live trial, not code.** The 09-09 trial was the first
> to converge repeatedly — **three rounds accepted at 7, 4 and 11 queries**,
> against one accept in eight rounds at 18 queries on 08-31/09-01. Since then
> **W2-M** (a caregiver note now credits only words it actually says — it could
> credit `what: pain` from a note reading "right side paralysis", at a weight
> that locks the slot instantly) and **W2-P** (the entry records the slot a
> question actually asks about, so rotation stops re-picking a starved one) have
> landed unseen by a person, along with the **iPad audio fix** — the question
> was never read aloud because each readout built a new `Audio` element, and
> iOS ties playback permission to the element.
>
> **Watch `reached_ready`, not `converged`.** The bench's confirm oracle rejects
> drafts that plainly capture the need, so convergence is capped below what the
> engine deserves; whether the board reaches readiness — the point where the
> draft becomes a woven sentence rather than the code template — is the honest
> measure. W2-P took it from **0/9 to 2/9**, the first non-zero in any bench run.
>
> Then **F3** (W2-E, tag rescue) and **F4** (doc reconciliation) → merge. Still
> open: **W2-Q** (confirmed answers never read back), the owner call on
> **W2-L**'s verify-no scoring, W2-P's unbuilt **enumeration-axis guard**, and
> **latency** — 10.2s per question, 86% of it the deliberate phase, with
> prompt-side fixes measured and rejected (**W2-S**).

- **Phase 1 — backend MVP** ✓ Dialogue engine, topics, Ollama client, safety
  layer, and a Rich-based CLI harness.
- **Phase 2 — caregiver cockpit** ✓ FastAPI backend + Preact/Vite web cockpit:
  the **living proposal banner** on top (the evolving draft — Speak ·
  ⟳ Restate · ✓ accept with an explicit confirmation modal · click-to-edit
  segments with candidate dropdowns), three tiles (conversation · live
  reasoning with the consensus board and refinement chains + emotion sliders ·
  input with ⇄ Opposite/Undo and the guiding-context field; pictogram tile
  shelved), persistent topic bar, recording light, session **Review**
  dashboard, SSE progress channel, JSONL recording/export, and **local TTS**
  (piper or the warmer kokoro — voice readouts of queries/utterances; review
  auto-play reads each step).
- **Reasoning controller** ✓ A **5W1H facet controller** (LLM does language,
  `agent/facets.py` + `agent/dialogue.py` do control). The belief is a consensus
  **board**: per slot — who / what / when / where / why / how — contender values
  carry **additive points** ("yes" +1, "kinda" +0.5, "no" −1 to the pair the
  question asserted only; never normalized, rivals never promoted). Crediting is
  **anchored to the question text** — a value only scores if the question
  literally says it (the fix for phantom-subject score drift). Each turn the
  controller picks a **focus slot** (probe an unestablished core slot → split
  tied contenders → drill the vague leader) and the ask is **two-phase for every
  model** (deliberate → format) behind three hard gates: yes/no answerability, a
  **repeat gate** (no reworded re-asks, ever), and slot anchoring. Synthesis
  **weaves the slot leaders into one natural sentence** (placeholders for
  unknown slots) once the yes-gate or board-readiness clears; a rejected
  utterance is **rephrased**, and repeated misses / a long "no" streak /
  stalled board progress / a reasoner fail-loop all trigger the **restart
  recovery** — dump every no/kinda influence, keep caregiver context + this
  round's confirmed yeses (round-specific by construction), add fresh broad
  probes. There are **no canned fallback questions**: a turn that still can't
  produce a question surfaces a **diagnostic card** (reason + Retry) instead.
  The only model-side terminator is a **"yes" to a proposed utterance**. The
  v2 layer (June 2026): crediting is **asymmetric** (a "no" hits only the
  lowest-scoring tagged pair — confirmed anchors survive wrong guesses); only
  an **informative yes** counts as progress (no confirmation farming); person
  topics get a **direction layer** — four intent buckets (do-for-them /
  them-for-me / tell / ask) credited by a code classifier, where a "no" with
  a confirmed who-anchor nudges the **mirror bucket** (the sign flip); a
  **futility guard** bans a 4-no avenue for a turn; and the cockpit gains the
  **⇄ Opposite button** (key `o`). The v3 layer (June 2026, from live-trial
  autopsies): **focus retirement + per-topic facet layout** (settled slots
  stop drawing questions; body topics treat `where` as a body region and
  probe it early); **verify-on-lock** ("Just to double-check —" re-asks for
  values locked by a single answer); **refinement links** (coarse→fine
  families — "tingling" refines "discomfort" — with non-negative family mass
  so failed fine guesses never erode an established parent, and the draft
  weaving the deepest confirmed **frontier**); and the **synthesis editor**
  (click a draft segment → candidate dropdown / typed replacement /
  remove — refine-or-replace, no note parsing). Every knob is env-tunable.
  Mechanism + known risks: `tool-summary.html` (local) and
  `docs/design/reasoning-retro.md` §8–§9. How the engine measures
  against actual 20-questions game theory (entropy bounds, Rényi–Ulam noise,
  LLM question-asking research, SCA practice):
  [`docs/design/20q-research-audit.html`](docs/design/20q-research-audit.html).
  The prioritized fix queue derived from it (and from the 06-11 dishes-round
  autopsy) is [`docs/design/convergence-plan.md`](docs/design/convergence-plan.md)
  — proposals are iterated with the owner one at a time before implementation.
- **Phase 3 — caregiver interview + knowledge graph** — deferred (the only
  graph write path).
- **Phase 3.5 — scheduling rounds** — requested 2026-08-11, not yet designed:
  a scheduling-logistics / event-planning mode of play. The first topic where
  `when` would carry the round (every current topic ranks it 5th or last).
  Requirement and open questions:
  [`docs/design/scheduling-rounds.md`](docs/design/scheduling-rounds.md).

## Terminology

Three-tier scale: **Session** (one open→close) ⊃ **Round** (one convergence
attempt under a single topic) ⊃ **Query** (one generated question). A round ends
**only when the caregiver accepts the live proposal banner (✓)** — the engine
never proposes or self-ends on a question count or an LLM failure. Out-of-band
stops only: an emergency topic, a caregiver topic switch, or the optional
`MY20Q_MAX_QUERIES` safety ceiling. The **banner** carries an evolving draft
from the first converged slot ("I need/want something for/from Rob…" — the
alternates collapse as evidence arrives), brightens at board-readiness, and
offers Speak / ⟳ Restate (re-say the same draft in different words) / ✓ accept
(an explicit front-and-center confirmation, spoken, one "New round" action).
Corrections go through the **synthesis editor**, not free text: clicking a
woven segment opens its slot's top board candidates as one-tap chips, a typed
replacement, and **✕ remove this detail** (mutes the slot). A replacement that
lexically extends the old value ("tickets" → "Avalanche tickets") *refines* it
— the parent is kept and the frontier deepens; a genuine swap strikes the old
value and stands in at caregiver strength. The earlier free-text
"✗ reject-a-portion" note flow was **retired by W1-F** (it could not tell ban
from augment from replace — see `docs/design/convergence-plan.md` §1c).

## Privacy invariant

**recording ⟹ real patient ⟹ local LLM.** A single flag
(`real_patient_profile_loaded`) gates both the backend and recording: with a
real profile the LLM is local-only (Ollama) and recording is on; with a
synthetic persona the Anthropic backend is permitted and recording is off. The
recorded dataset and any cloud backend can never coexist. TTS is **always**
local (piper or kokoro) — there is no cloud-voice path.

## Install

```bash
# Shared venv (owner's convention) — or your tool of choice
source ~/venvs/my20q/Scripts/activate          # Windows / Git Bash
pip install -e ".[dev]"
pytest
```

Optional — local LLM for reasoning mode:

```bash
ollama serve &
ollama pull gemma4:e4b                   # default (thinking model); any Ollama model works
```

Optional — local TTS (piper). On Linux/macOS `pip install -e ".[tts]"` provides
the `piper` entry point; on **Windows** use the prebuilt
[piper release](https://github.com/rhasspy/piper/releases) binary instead, plus
a voice model (`.onnx` + `.onnx.json`), and point at them:

```bash
export MY20Q_PIPER_BIN=/path/to/piper[.exe]            # if not on PATH
export MY20Q_PIPER_MODEL=/path/to/en_US-amy-medium.onnx
# verify: GET /api/tts/status -> { "available": true }
```

For a warmer voice, use **kokoro** instead: `pip install -e ".[kokoro]"`, set
`MY20Q_TTS_ENGINE=kokoro`, and point `MY20Q_KOKORO_MODEL` / `MY20Q_KOKORO_VOICES`
at the downloaded model/voices files. See [`KOKORO.md`](KOKORO.md).

## Run

**Caregiver cockpit** (the primary interface) — backend serves the built
cockpit at `/`:

```bash
python -m my20q.api                      # http://localhost:8000  (cockpit + API)
# remote: tailscale serve --bg https / http://127.0.0.1:8000
```

Develop the cockpit with hot-reload (Vite proxies `/api/*` to the backend):

```bash
cd web && npm install && npm run dev     # http://localhost:5173
npm run build                            # production build to web/dist/
```

**CLI harness** (developer tool, *not* the patient interface — for iterating on
prompts, topics, and dialogue behavior):

```bash
python -m my20q                          # reasoning mode (needs Ollama)
python -m my20q --no-llm                 # no LLM — rounds surface diagnostics
python -m my20q --max-queries 30         # optional safety ceiling (0 = unlimited)
```

## Answers

Each query is answered with one of four buttons (mapped to keys in the cockpit
and CLI):

- **yes** — affirmed; narrow in this direction
- **no** — rejected; pivot
- **kinda** — warmer; close to the target
- **not sure** — no information; try a different axis

Plus one *action* (not an answer): **⇄ Opposite** re-renders the pending
question with its connotation reversed — who-does-for-whom mirrored, or the
key detail flipped — and waits for an answer to *that*.

## Environment

| Env var | Default | Purpose |
|---------|---------|---------|
| `MY20Q_LLM` | `1` | Set `0` to disable the LLM (same as `--no-llm`) |
| `MY20Q_BACKEND` | `ollama` | `ollama` / `anthropic` (anthropic gated off for real patients) |
| `MY20Q_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `MY20Q_OLLAMA_MODEL` | `gemma4:e4b` | Ollama model tag (a thinking model; drills best) |
| `MY20Q_OLLAMA_TIMEOUT` | `120` | Per-call timeout (s); the runaway guard for uncapped reasoning |
| `MY20Q_ANTHROPIC_MODEL` | — | Anthropic model (synthetic personas only) |
| `MY20Q_MAX_QUERIES` | `0` | `0` = unlimited; positive = hard safety ceiling (same as `--max-queries`) |
| `MY20Q_MIN_YES` | `5` | *retired* — the banner replaced engine-initiated synthesis (kept for script compat) |
| `MY20Q_NEW_YES` | `3` | *retired* — see above |
| `MY20Q_REPHRASE_LIMIT` | `1` | *retired* — see above |
| `MY20Q_SYNTH_ATTEMPTS` | `2` | *retired* — see above |
| `MY20Q_EXPLORE_DECAY` | `0.67` | exploration probability = base^(yeses+1) |
| `MY20Q_SOFT_RESET_NOS` | `10` | consecutive "no"s that trigger the restart recovery |
| `MY20Q_STALL_WINDOW` | `8` | answered queries with zero board progress → restart (0 = off) |
| `MY20Q_FACET_READY` | `2.0` | points a slot leader needs to count as determined |
| `MY20Q_SPLIT_MARGIN` | `1.0` | top-two contenders closer than this are tied → split question |
| `MY20Q_RETIRE_X` | `3.0` | slot retires from drilling once its leader ≥ this × ready points with the runner-up ≤ half |
| `MY20Q_MODE` | training | Dialogue mode |
| `MY20Q_PROFILE` | — | Patient/persona profile to load |
| `MY20Q_TOPICS` | bundled | Override the topics data path |
| `MY20Q_DATA_DIR` | repo-local | Recorded-dataset directory (real patient) |
| `MY20Q_RECORDING_THRESHOLD_MB` | — | Dataset-size warning threshold |
| `MY20Q_TTS` | `1` | Set `0` to disable TTS |
| `MY20Q_TTS_ENGINE` | `piper` | `piper` or `kokoro` (warmer voice) |
| `MY20Q_PIPER_BIN` | `piper` | piper binary (name on PATH or full path) |
| `MY20Q_PIPER_MODEL` | — | Voice model `.onnx` path |
| `MY20Q_PIPER_TIMEOUT` | `20` | piper synthesis timeout (s) |
| `MY20Q_KOKORO_MODEL` | — | kokoro model `.onnx` (when `engine=kokoro`) |
| `MY20Q_KOKORO_VOICES` | — | kokoro voices `.bin` path |
| `MY20Q_KOKORO_VOICE` | `af_heart` | kokoro voice name |

## Developer tools

Three scripts, all read-only or dry-run by default. None is part of the
cockpit; they exist for trial autopsies and engine work.

```bash
# Read a recorded session back, with its autopsy summary per round: the query
# at which the board turned propose-ready (and how many were asked after),
# restart reasons AND positions, gate rejections, per-question latency, the
# seed context, and any question left unanswered.
python scripts/dump_recording.py patient_data/<patient>/<session>.jsonl

# Drive real rounds against a simulated answerer and report the §1 metrics.
# --noise adds ε-noise (yes↔no flipped with probability ε); --compare diffs two
# runs, i.e. two engine revisions. Metrics are read off the round RECORD, so
# anything it reports is obtainable from a real recorded session.
python scripts/bench_reasoning.py --models gemma4:e4b --json before.json
python scripts/bench_reasoning.py --compare before.json after.json

# Drop demo rounds (opened, seeded, nothing answered) and archive old sessions
# out of the cockpit picker. Dry run unless --apply; backs up first; rounds it
# KEEPS are written back byte-identical. `emergency` rounds are exempt.
python scripts/prune_recordings.py                       # dry run
python scripts/prune_recordings.py --archive-before 2026-07 --apply
```

`scripts/serve_cerberus.sh [start|logs|status|stop]` runs the cockpit under
tmux for remote trials. It wires `MY20Q_PROFILE` automatically — launching
`python -m my20q.api` bare gives you **no profile and therefore no recording**.

## Tests

```bash
pytest
ruff check .
cd web && npm run typecheck
```
