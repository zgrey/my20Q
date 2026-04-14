# my20Q

An assistive, game-based agent that helps individuals with aphasia communicate
a need through a short 20-questions-style dialogue, pairing each yes/no
question with AAC-appropriate imagery.

**Clinical intent**: assistive, **not** diagnostic. The tool helps a patient
express a need to a caregiver — it never offers medical advice, triage, or
treatment recommendations. All LLM inference runs locally; nothing leaves the
host.

## Status

- **Phase 1 (Python backend MVP)** — complete. Dialogue engine, taxonomy,
  Ollama client, safety layer, and Rich-based CLI harness all in place. Runs
  against a local LLM in reasoning mode or deterministically against the
  taxonomy tree in fallback mode.
- **Phase 2 (FastAPI + PWA)** — not yet started.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full phased plan and
[`CLAUDE.md`](CLAUDE.md) for architecture and design constraints.

## Install

```bash
# Shared venv (owner's convention) — or use your tool of choice
source ~/venv/Scripts/activate         # Windows / Git Bash
pip install -e ".[dev]"
pytest
```

Optional: start a local LLM for reasoning mode.

```bash
ollama serve &
ollama pull gemma3:12b                 # default; any Ollama model works
```

## Example CLI run

The CLI is a **developer harness**, not the patient interface — the PWA in
Phase 2 is what a patient actually uses. Use it to iterate on prompts,
taxonomy, and dialogue behavior.

```bash
$ python -m my20q --no-llm             # deterministic fallback mode
LLM disabled. Using deterministic taxonomy walk (questions only, no reasoning).
+--------------------------------+
| my20Q - what do you need/want? |
+--------------------------------+
  1. Get help now (emergency)  (emergency)
  2. My feelings  (mental_health)
  3. My body  (physical_health)
  4. My People  (my_people)
  5. Other  (general)
  q. quit
Pick a category number (or q to quit) [1/2/3/4/5/q]: 3

+-------------------+
| turn 0 - Question |
| Are you in pain?  |
+-------------------+
yes / no / kinda / not sure [y/n/k/s] (y): n

+------------------------+
| turn 1 - Question      |
| Are you feeling tired? |
+------------------------+
yes / no / kinda / not sure [y/n/k/s] (y): n

+-------------------+
| turn 2 - Question |
| Are you hungry?   |
+-------------------+
yes / no / kinda / not sure [y/n/k/s] (y): n

+-------------------+
| turn 3 - Question |
| Are you thirsty?  |
+-------------------+
yes / no / kinda / not sure [y/n/k/s] (y): y

+------------------+
| turn 4 - Guess   |
| Are you thirsty? |
+------------------+
yes / no / kinda / not sure (q to quit) [y/n/k/s/q] (y): y

+----------------------------------+
| Summary for caregiver:           |
| They are communicating: Thirsty. |
|                                  |
| Final: Thirsty                   |
+----------------------------------+

Play again? [y/n] (y): n
goodbye
```

Drop `--no-llm` to use Ollama. The session plays to a terminal outcome —
confirmed guess, 20-turn limit, emergency, or dead-end — then offers
**play again** and returns to the category picker. Type `q` at any prompt to
quit softly.

## The "Other" category — freeform context and multi-round carryover

Selecting **Other** skips the 20-questions walk and prompts the user to type
what they want to communicate (`Explain the topic in any level of detail:`).
In reasoning mode the LLM uses that text as a seed to guide a short,
narrowing dialogue; in `--no-llm` mode it is echoed directly into the
caregiver summary.

If earlier rounds in the same CLI session produced successful outcomes, the
user is first offered an enumerated menu of those prior contexts plus **All
of the above** / **None** before typing the new detail. This lets a caregiver
manually accumulate context across plays until an LLM-backed session-history
store exists.

## CLI flags and environment

| Flag / env var | Default | Purpose |
|----------------|---------|---------|
| `--no-llm` | off | Force deterministic tree-walk mode |
| `--max-turns N` | `20` | Override the 20-turn game budget |
| `MY20Q_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `MY20Q_OLLAMA_MODEL` | `gemma3:12b` | Model tag |
| `MY20Q_OLLAMA_TIMEOUT` | `30` | Per-request timeout (seconds) |
| `MY20Q_TAXONOMY` | bundled `tree.yaml` | Override taxonomy path |
| `MY20Q_LLM` | `1` | Set to `0` to disable LLM (same as `--no-llm`) |
| `MY20Q_MAX_TURNS` | `20` | Same as `--max-turns` |

## Answers

Patients answer each turn with one of four buttons (mapped to keys in the CLI):

- **yes** — affirmed; narrow in this direction
- **no** — rejected; pivot
- **kinda** — warmer; close to the target
- **not sure** — no information; try a different axis

In fallback mode, `kinda` is treated as `yes` (go deeper here).

## Privacy

All inference is local — no cloud APIs, no telemetry, no PII stored. The
taxonomy path and timestamps are the only data a caregiver dashboard (Phase 3)
would ever see.
