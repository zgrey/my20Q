# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary

`my20Q` is an **assistive game-based agent** for accelerating communication in
individuals suffering from aphasia. A patient interacts with a tablet; the system
plays a 20-questions-style dialogue, pairing each yes/no question with
AAC-appropriate imagery, to help the patient converge on what they need to
express (a need, a feeling, a request for help, an emergency).

**Clinical intent**: assistive, **not** diagnostic. The tool helps a user
communicate a need to a caregiver — it does not offer medical advice, triage, or
treatment recommendations.

## Architecture

```
 [ Android tablet, kiosk browser ]          [ cerberus, home server ]
         │                                          │
         │  Tailscale VPN (HTTPS)                   │
         │────────────────────────────────────────► │  FastAPI backend
         │                                          │    ├─ dialogue/session state
         │   PWA (web app) — static imagery         │    ├─ prompt + retrieval layer
         │                                          │    └─ Ollama HTTP client
         │                                          │         └─ local LLM
```

- **Backend**: Python (FastAPI) on `cerberus`, calling a local LLM via Ollama.
- **Frontend**: Progressive Web App (PWA) served by the backend; tablet runs a
  locked-down kiosk browser pointed at `https://cerberus.<tailnet>.ts.net`.
- **Network**: Tailscale VPN only — no public exposure, no cloud APIs.
- **Imagery**: static, curated library + open AAC pictogram sets (ARASAAC,
  Mulberry Symbols). No on-the-fly image generation.

## Locked Technical Decisions

| Topic | Choice | Rationale |
|-------|--------|-----------|
| LLM runtime | **Ollama** | Simple HTTP API, easy model swap (Llama 3.2, Phi-4, Qwen) |
| LLM interface | **Abstracted behind `LLMBackend`** | llama.cpp / vLLM swap-in later if needed |
| Backend framework | **FastAPI** | Async, typed, easy SSE, good Pydantic integration |
| Frontend (Phase 2) | **PWA (Vite + vanilla TS or preact)** | Works on any tablet w/ browser; kiosk-friendly |
| Icon sources | **ARASAAC + Mulberry Symbols** | Designed for AAC/aphasia; open licenses |
| Tablet OS | **Repurposed Android in kiosk browser** | Lowest friction; Linux-native app is a later phase |
| Transport | **Tailscale VPN** | Privacy, no cert gymnastics, MagicDNS |

## Directory Layout (target — populated incrementally by phase)

```
my20Q/
├── CLAUDE.md
├── README.md
├── LICENSE
├── pyproject.toml
├── docs/
│   ├── ROADMAP.md                # phased plan (source of truth for scope)
│   ├── design/                   # UX sketches, dialogue trees, taxonomy notes
│   └── clinical/                 # AAC references, aphasia literature
├── src/my20q/
│   ├── __init__.py
│   ├── api/                      # FastAPI app (Phase 2)
│   ├── agent/                    # dialogue engine, prompt templates
│   ├── llm/                      # Ollama client + abstract interface
│   ├── taxonomy/                 # category/question tree + YAML data
│   └── config.py
├── web/                          # PWA frontend (Phase 2)
├── assets/
│   ├── arasaac/                  # fetched (CC BY-NC-SA)
│   ├── mulberry/                 # fetched (CC0)
│   └── categories/               # hand-picked top-level imagery
├── tests/
└── scripts/
    ├── fetch_icons.py
    └── run_local.sh
```

## Development Workflow

```bash
# Phase 1+ (once pyproject.toml exists):
pip install -e ".[dev]"
pytest
ruff check .
python -m my20q.cli              # CLI dialogue harness
```

Local LLM prerequisite (on cerberus):

```bash
ollama serve &
ollama pull llama3.2:3b          # or phi-4-mini, qwen2.5:3b
```

## Git Workflow

- `main` is the protected deployment branch.
- Feature work on topic branches; squash-merge PRs into `main`.
- Conventional-ish commit messages (`feat:`, `fix:`, `docs:`, `chore:`).

## Aphasia / UX Design Principles

These are **binding** design constraints — deviations require explicit discussion.

- **One question per screen.** Never multiple questions, never scrolling text.
- **Huge tap targets.** Minimum 88 × 88 px; prefer filling a quadrant of the screen.
- **Yes / No / Not sure.** Three buttons max. No free-text input from the patient.
- **High contrast, large type.** Target WCAG AAA (contrast ≥ 7:1). Minimum 24 px body.
- **No time pressure.** No countdowns, no auto-advance, no "you've been idle" prompts.
- **Pictograms + short text.** Every question and answer has a pictogram; text is a
  concrete noun/short phrase, not a sentence.
- **Persistent escape hatches.** "🏠 Start Over" and "🆘 Emergency / Get Help" must
  be on-screen at **all** times.
- **No reading-heavy LLM prose.** Patient never sees raw model output; questions
  are either picked from a curated bank or templated from a whitelist.
- **TTS-ready.** Every on-screen string must be TTS-pronounceable (Phase 3 adds
  `piper`).

## Safety Principles

- **Emergency short-circuit.** If the user picks the "Emergency" top category, or
  any descendant flags `emergency: true`, bypass the LLM entirely and show a
  hard-coded screen with caregiver-call / 911 actions.
- **LLM output is never shown raw.** All patient-facing text flows through a
  sanitizer that enforces: max length, no URLs, no medical advice keywords,
  matches an expected template.
- **No diagnostic language.** The system asks about **needs and feelings**, never
  "do you have X condition?".
- **Failure modes are soft.** If the LLM stalls, errors, or is unreachable, the
  dialogue falls back to the static taxonomy tree — the app always keeps working.
- **No PII stored.** Session state is in-memory; optional caregiver logs store
  only taxonomy-node paths + timestamps, never patient-entered data (there isn't
  any — input is button taps).

## Privacy

- All inference runs locally on `cerberus`. No third-party APIs, no telemetry.
- Tablet ↔ cerberus traffic is confined to Tailscale.
- No analytics, no crash reporting to external services.

## Cross-Repo Context

This is a **new, independent repo** in the multi-repo workspace documented at
`C:\Users\grey_\Git\GitHub\CLAUDE.md`. It has no code dependencies on the other
repos (TDA-SST, G2Aero, etc.) and uses its own tooling.

## Supplemental Ideas (future phases, not yet in scope)

- **TTS output** via [`piper`](https://github.com/rhasspy/piper) — local, fast, runs on
  modest hardware.
- **Caregiver dashboard** — separate view showing session history as taxonomy
  paths, never raw content, for therapist/family review.
- **Session export for speech therapists** — anonymized summaries of which
  topics the patient engages with.
- **Multilingual support** — taxonomy + pictograms per locale; ARASAAC already
  supports many.
- **Speech-in** via `whisper.cpp` — some patients can speak words even if
  sentence construction is hard; let them skip ahead.
- **Eye-tracking input** as an accessibility stretch goal (Tobii or
  webcam-based) for patients with limited motor control.
- **Favorites / quick-access tiles** — the top 4–6 most-used taxonomy leaves
  surface as a shortcut grid on the home screen.
- **Per-patient profile** — family member names, medications, hobbies used as
  concrete concepts in the question bank.
- **Offline-first PWA** — service worker caches assets and taxonomy so a
  transient Tailscale hiccup doesn't stall the session.
- **Native Linux tablet app** (Phase 4 stretch) — Tauri shell reusing the PWA,
  or GTK4 + libadwaita if we commit to a specific tablet.

## Planning

The authoritative phased plan lives in [`docs/ROADMAP.md`](docs/ROADMAP.md).
Update it when scope changes rather than scattering decisions across other docs.
