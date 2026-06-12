# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Beta retool in progress.** The live plan is
> [`docs/design/beta-retool.md`](docs/design/beta-retool.md) — read it before
> working on this repo. It supersedes parts of this file where they conflict.
> `docs/ROADMAP.md` has been restructured to match it.

## Project Summary

`my20Q` is a **context-driven speech emulator** for an individual with aphasia.
It plays a question-and-answer dialogue — pairing each question with
AAC-appropriate imagery — to converge on what the person needs to express (a
need, a feeling, a request for help, an emergency) and synthesize that as a
short spoken utterance.

It is **patient-specific, not global, universal, or medical**. It is a digital
voice that reflects one person — not a clinical average. The real interface is a
**caregiver cockpit**: the caregiver and patient use the tool *together*. The
patient never operates the cockpit alone.

**Clinical intent**: assistive, **not** diagnostic. It helps a person
communicate a need to a caregiver — no medical advice, triage, or treatment
recommendations.

## Architecture

```
 [ caregiver cockpit, web browser ]         [ cerberus, home server ]
         │                                          │
         │  Tailscale VPN (HTTPS)                   │
         │────────────────────────────────────────► │  FastAPI backend
         │   living proposal banner (the evolving   │    ├─ dialogue / round state
         │   draft: Speak · ⟳ Restate · ✓ accept ·  │    ├─ prompt + retrieval layer
         │   click-to-edit segments) above 3 tiles: │    ├─ recording / dataset writer
         │   conversation · live reasoning · input  │    └─ LLMBackend → Ollama (local)
         │   (pictogram tile shelved)               │
 [ aphasia-oriented input — secondary,              │
   supplemental, later phase ]                      │
```

- **Backend**: Python (FastAPI) on `cerberus`, calling a local LLM via Ollama.
- **Caregiver cockpit**: web app served by the backend — the primary interface.
- **Aphasia-oriented UX**: a secondary, supplemental *input* surface feeding the
  cockpit. Built in a later phase.
- **Network**: Tailscale VPN only — no public exposure.
- **Imagery**: static, curated library + open AAC pictogram sets (ARASAAC,
  Mulberry Symbols), **retrieved by intent — never generated on the fly**.

## Terminology

A three-tier scale (replaces the retired "round/pass" terms):

- **Session** — one open→close of the tool.
- **Round** — one convergence attempt under a single high-level context (topic).
  Ends on the **caregiver accepting the live proposal banner (✓)** — the
  engine never proposes or self-ends — or out-of-band on topic change,
  query-budget exhaustion, or session end.
- **Query** — one generated question within a round.

Nesting: Session ⊃ Rounds ⊃ Queries. The query budget (the old "20") is a soft
safety cap, not the primary terminator.

## Modes

Two **orthogonal** axes:

- **Reasoning vs. fallback** — is the LLM reachable? Reasoning mode lets the
  `Reasoner` drive. There are **no canned fallback questions**: when reasoning
  fails, the round first runs its restart recovery (dump no/kinda context,
  keep caregiver context + the round's confirmed yeses) and, failing that,
  surfaces a **diagnostic card** (reason + Retry) — a useful failure beats a
  meaningless question.
- **Training vs. operational** — do we trust the input? *Training* is
  caregiver-driven with reliable input (**the beta builds this only**).
  *Operational* is patient-solo with noisy input, leaning on the knowledge
  graph (deferred).

"Testing" is not a mode — it is training mode run against a *synthetic persona*
for development.

## Locked Technical Decisions

| Topic | Choice | Rationale |
|-------|--------|-----------|
| LLM runtime | **Ollama** | Simple HTTP API, easy model swap (default `gemma4:e4b`) |
| LLM interface | **Abstracted behind `LLMBackend`** | llama.cpp / vLLM / Anthropic swap-in |
| Dev-trial backend | **Anthropic (Opus)** | Strong model for iterating agentic logic — **synthetic personas only**, see Privacy |
| Backend framework | **FastAPI** | Async, typed, SSE/WebSocket streaming |
| Caregiver cockpit | **Web app, local server** | Browser-based; rich but simple |
| Icon sources | **ARASAAC + Mulberry Symbols** | Designed for AAC/aphasia; open licenses; retrieved not generated |
| Transport | **Tailscale VPN** | Privacy, no cert gymnastics, MagicDNS |

## Privacy & Safety

Privacy is foundational — the tool conditions on broad, sensitive patient
context, so storage and communication must be tightly controlled.

**The privacy invariant.** A single flag — `real_patient_profile_loaded` — gates
both the backend and recording:

| Real patient profile loaded | LLM backend | Recording |
|---|---|---|
| **true** | **Local only** (Ollama); Anthropic backend unavailable | **On** (caregiver may pause) |
| **false** (synthetic persona) | Anthropic/Opus permitted | Off |

→ **recording ⟹ real patient ⟹ local LLM.** The cloud backend and the recorded
dataset can never coexist.

- All real-patient storage (profile, knowledge graph, recorded dataset) is
  **local, encrypted at rest, never leaves the network, caregiver-purgeable**.
  *(This replaces the obsolete "no PII stored" rule — the graph and dataset
  require local storage by design.)*
- No third-party APIs for real patients, no telemetry, no external crash
  reporting.
- **Emergency short-circuit.** Picking the "Emergency" topic, or any descendant
  flagged `emergency: true`, bypasses the LLM entirely for a hard-coded
  caregiver-call / 911 screen.
- **LLM output is never shown raw to the patient.** Patient-facing strings
  (questions, and the synthesized utterance) pass through a sanitizer enforcing
  max length, no URLs, no medical-advice keywords, template match.
- **No diagnostic language.** Asks about needs and feelings, never "do you have
  X condition?".
- **Failure modes are soft.** A reasoning failure never ends a round: the
  engine auto-recovers (context restart), and if that fails the cockpit shows
  an honest diagnostic with a Retry — never a canned, meaningless question.

## Directory Layout (target — populated incrementally by phase)

```
my20Q/
├── CLAUDE.md
├── README.md · LICENSE · pyproject.toml
├── docs/
│   ├── ROADMAP.md                # phased plan (scope source of truth)
│   ├── design/                   # design docs — beta-retool.md is the live plan
│   └── clinical/                 # AAC references, aphasia literature
├── src/my20q/
│   ├── __main__.py               # `python -m my20q` entry point
│   ├── api/                      # FastAPI app + cockpit endpoints (Phase 2)
│   ├── agent/
│   │   ├── dialogue.py           # round state machine (rewindable history)
│   │   ├── facets.py             # 5W1H consensus board (the belief)
│   │   ├── reasoner.py           # LLM-driven question/synthesis calls
│   │   ├── auditor.py            # yes/no audit + repeat gate
│   │   ├── prompts.py            # system prompts + templating
│   │   └── safety.py             # emergency detector + output sanitizer
│   ├── cli.py                    # Rich-based developer harness (retained)
│   ├── llm/                      # LLMBackend protocol + Ollama + Anthropic + mock
│   ├── topics/                   # flat editable topic list + YAML data
│   ├── recording/                # 3-tier session/round/query dataset writer
│   ├── profiles/                 # patient-profile loader
│   └── config.py
├── web/                          # caregiver cockpit frontend (Phase 2)
├── assets/                       # ARASAAC / Mulberry pictograms + attribution
├── tests/
└── scripts/
```

## Development Workflow

```bash
pip install -e ".[dev]"
pytest
ruff check .
python -m my20q                  # CLI harness — reasoning mode
python -m my20q --no-llm         # CLI harness — no LLM (diagnostics only)
```

Local LLM prerequisite:

```bash
ollama serve &
ollama pull gemma4:e4b           # default (thinking model; drills best)
```

Gate the integration test against a real Ollama instance:

```bash
MY20Q_INTEGRATION=1 pytest tests/test_llm_backends.py
```

## Dialogue Philosophy — two-layer persistence

The tool gets better over time by accumulating patient-specific context — but
strictly along two separated layers:

- **Stable layer — persists and grows.** Who the patient is: the caregiver
  profile and the knowledge graph. It informs *how* the agent asks — never
  *what* it guesses.
- **Volatile layer — never persists.** The current need is inferred fresh each
  round. Today's need is not predicted by last week's; letting past needs bleed
  through as guess-priors would bury the current signal.

Within a round, reasoning mode balances **exploiting** current-round signals
(history, caregiver context typed mid-round, the profile) against **exploring**
an under-sampled dimension — an RL-style policy. Never echo a known subject as a
guess: the topic identifies *what* the subject is; the job is to narrow what is
unclear *about* it.

The **knowledge graph is written only via the caregiver interview tool** — there
is no automated write path, which makes automated poisoning impossible. See
`docs/design/beta-retool.md` §9.

## Caregiver Cockpit

A rich-but-simple web interface — **caregiver-only**. The **living proposal
banner** on top (the evolving draft utterance: Speak · ⟳ Restate · ✓ accept ·
click-to-edit segments with candidate dropdowns — the ONLY way a round
concludes in success), three active tiles (conversation · live reasoning with
the consensus board and refinement chains · input with answer buttons, the
⇄ opposition flip, undo, and the guiding-context field), a persistent topic
dropdown, and a recording light. The pictogram tile is shelved. Full spec in
`docs/design/beta-retool.md` §6; the engine/fix queue in
`docs/design/convergence-plan.md`. The aphasia UX constraints below do
**not** apply to the cockpit — they govern the future patient-facing
interface.

## Patient-Interface UX Principles

These are **binding** constraints for the *patient-facing* interface (the
secondary aphasia-oriented input surface, and the future operational-mode
interface). They do not constrain the caregiver cockpit.

- **One question per screen.** Never multiple questions, never scrolling text.
- **Huge tap targets.** Minimum 88 × 88 px; prefer filling a screen quadrant.
- **Yes / No / Kinda / Not sure.** Four answers; no free-text from the patient.
  `kinda` = "you're warm".
- **High contrast, large type.** WCAG AAA (contrast ≥ 7:1). Minimum 24 px body.
- **No time pressure.** No countdowns, auto-advance, or idle prompts.
- **Pictograms + short text.** Concrete noun / short phrase, never a sentence.
- **Persistent escape hatches.** "🏠 Start Over" and "🆘 Emergency" always visible.
- **No reading-heavy LLM prose.** Patient never sees raw model output.
- **TTS-ready.** Every on-screen string must be TTS-pronounceable.

## Git Workflow

- `main` is the protected deployment branch.
- Feature work on topic branches; squash-merge PRs into `main`.
- Conventional-ish commit messages (`feat:`, `fix:`, `docs:`, `chore:`).

## Cross-Repo Context

This is a **new, independent repo** in the multi-repo workspace documented at
`C:\Users\grey_\Git\GitHub\CLAUDE.md`. It has no code dependencies on the other
repos and uses its own tooling.

## Supplemental Ideas (future phases, not yet in scope)

- **TTS output** via [`piper`](https://github.com/rhasspy/piper) — local, fast.
- **Speech-in** via `whisper.cpp` — let patients who can speak words skip ahead.
- **Eye-tracking input** as an accessibility stretch goal.
- **Multilingual support** — topics + pictograms per locale.
- **Per-patient weight fine-tuning** ("path b") — using the recorded dataset,
  budget permitting; deferred.
- **Native Linux tablet app** — Tauri shell or GTK4 + libadwaita.

## Planning

The phased plan lives in [`docs/ROADMAP.md`](docs/ROADMAP.md); the current beta
design lives in [`docs/design/beta-retool.md`](docs/design/beta-retool.md);
the **reasoning-engine fix queue and trial autopsies** live in
[`docs/design/convergence-plan.md`](docs/design/convergence-plan.md) (iterated
with the owner one proposal at a time, statuses tracked in-doc), grounded in
the literature audit
[`docs/design/20q-research-audit.html`](docs/design/20q-research-audit.html).
Update them when scope changes rather than scattering decisions across docs.
