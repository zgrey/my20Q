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

- **Phase 1 — backend MVP** ✓ Dialogue engine, topics, Ollama client, safety
  layer, and a Rich-based CLI harness.
- **Phase 2 — caregiver cockpit** ✓ FastAPI backend + Preact/Vite web cockpit:
  4-tile layout (conversation · pictogram · live reasoning + emotion sliders ·
  input), persistent topic bar, recording light, session **Review** dashboard,
  SSE progress channel, JSONL recording/export, and **local piper TTS**
  (voice readouts of queries/utterances; review auto-play reads each step).
- **Reasoning controller** ✓ A **confirmation-driven** hypothesis controller
  (LLM does language, `agent/hypotheses.py` does control). Each turn it either
  **discriminates** (most-splitting yes/no over the belief, anchored to the
  affirmed cluster), **deepens** (confirms/sharpens the frontrunner once one
  emerges), or **synthesizes** — but **only after ≥5 "yes" confirmations** on a
  concentrated leader, so it never guesses on thin evidence. There's no question
  budget; a stuck round ends gracefully via a no-progress check. The cockpit's
  reasoning tile renders the live belief. Mechanism + known risks:
  `tool-summary.html` (local) and `docs/design/reasoning-retro.md`.
- **Phase 3 — caregiver interview + knowledge graph** — deferred (the only
  graph write path).

## Terminology

Three-tier scale: **Session** (one open→close) ⊃ **Round** (one convergence
attempt under a single topic, ending on synthesis / topic change / no-progress /
session end) ⊃ **Query** (one generated question). **Synthesis is readiness-driven**
(a concentrated leader confirmed by ≥5 yeses), never count-driven; there is no
question budget by default — `MY20Q_MAX_QUERIES` is only an optional hard safety
ceiling that stops a round without forcing an utterance.

## Privacy invariant

**recording ⟹ real patient ⟹ local LLM.** A single flag
(`real_patient_profile_loaded`) gates both the backend and recording: with a
real profile the LLM is local-only (Ollama) and recording is on; with a
synthetic persona the Anthropic backend is permitted and recording is off. The
recorded dataset and any cloud backend can never coexist. TTS is **always**
local (piper) — there is no cloud-voice path.

## Install

```bash
# Shared venv (owner's convention) — or your tool of choice
source ~/venv/Scripts/activate          # Windows / Git Bash
pip install -e ".[dev]"
pytest
```

Optional — local LLM for reasoning mode:

```bash
ollama serve &
ollama pull gemma3:12b                   # default; any Ollama model works
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
python -m my20q --no-llm                 # deterministic fallback mode
python -m my20q --max-queries 30         # optional safety ceiling (0 = unlimited)
```

## Answers

Each query is answered with one of four buttons (mapped to keys in the cockpit
and CLI):

- **yes** — affirmed; narrow in this direction
- **no** — rejected; pivot
- **kinda** — warmer; close to the target
- **not sure** — no information; try a different axis

## Environment

| Env var | Default | Purpose |
|---------|---------|---------|
| `MY20Q_LLM` | `1` | Set `0` to disable the LLM (same as `--no-llm`) |
| `MY20Q_BACKEND` | `ollama` | `ollama` / `anthropic` (anthropic gated off for real patients) |
| `MY20Q_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `MY20Q_OLLAMA_MODEL` | `gemma3:12b` | Ollama model tag (thinking models supported) |
| `MY20Q_OLLAMA_TIMEOUT` | `120` | Per-call timeout (s); the runaway guard for uncapped reasoning |
| `MY20Q_ANTHROPIC_MODEL` | — | Anthropic model (synthetic personas only) |
| `MY20Q_MAX_QUERIES` | `0` | `0` = unlimited; positive = hard safety ceiling (same as `--max-queries`) |
| `MY20Q_MODE` | training | Dialogue mode |
| `MY20Q_PROFILE` | — | Patient/persona profile to load |
| `MY20Q_TOPICS` | bundled | Override the topics data path |
| `MY20Q_DATA_DIR` | repo-local | Recorded-dataset directory (real patient) |
| `MY20Q_RECORDING_THRESHOLD_MB` | — | Dataset-size warning threshold |
| `MY20Q_TTS` | `1` | Set `0` to disable TTS |
| `MY20Q_PIPER_BIN` | `piper` | piper binary (name on PATH or full path) |
| `MY20Q_PIPER_MODEL` | — | Voice model `.onnx` path |
| `MY20Q_PIPER_TIMEOUT` | `20` | piper synthesis timeout (s) |

## Tests

```bash
pytest
ruff check .
```
