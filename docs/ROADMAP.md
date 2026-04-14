# my20Q Roadmap

A phased plan for building the assistive 20-questions agent. Each phase is
independently testable and produces a usable artifact.

## Phase 0 — Repo scaffolding ✅

Deliverables:

- `CLAUDE.md` at repo root documenting architecture, decisions, UX/safety
  principles, and future ideas.
- `docs/ROADMAP.md` (this file).

## Phase 1 — Python backend MVP (CLI-testable) ✅

**Goal**: a working dialogue engine with Ollama, exercised from a terminal
before any UI is built.

1. `pyproject.toml` with deps: `fastapi`, `uvicorn[standard]`, `httpx`,
   `pydantic>=2`, `pyyaml`, `rich`, `pytest`, `ruff`. `src/` layout.
2. `src/my20q/llm/ollama_client.py` — thin async client over `/api/chat`.
   Abstract `LLMBackend` Protocol so llama.cpp or vLLM swap in later.
3. `src/my20q/taxonomy/` — initial YAML tree:
   - 5 top categories: `emergency` (pinned first), `mental_health`
     ("My feelings"), `physical_health` ("My body"), `my_people`
     (immediate-family titles), `general` ("Other", always pinned last).
   - Each node carries `id`, `label`, `image`, `question`, optional
     `description` and `emergency: true` flag.
4. `src/my20q/agent/dialogue.py` — session state machine with two modes:
   - **Reasoning mode** (LLM available): `Reasoner` drives the game. Each
     turn, the LLM proposes a `question` or a concrete `guess` as strict JSON
     given the accumulated history; the user answers `yes`/`no`/`kinda`/
     `not_sure`. A `yes` on a guess ends the session.
   - **Fallback mode** (LLM unreachable): deterministic breadth-first walk of
     the taxonomy subtree under the chosen category. `yes` descends, `no`
     prunes, `not_sure` defers, `kinda` is treated as `yes`.
   - Emergency categories/descendants short-circuit to a hard-coded screen in
     both modes. On final-turn or dead-end, a caregiver-facing summary is
     generated via a constrained LLM call (or a static fallback).
5. `src/my20q/agent/reasoner.py` — LLM-driven action proposer. Strict JSON
   output, sanitized, with explicit "final turn must be a guess" enforcement.
6. `src/my20q/agent/prompts.py` — templated system prompts for rephrasing,
   path summaries, reasoning-mode action proposals, and game summaries.
7. `python -m my20q` — Rich-based CLI harness with a **play again** loop,
   `--no-llm` and `--max-turns` flags.
8. Safety layer — emergency detector, LLM-output sanitizer (length / URL /
   medical-advice filtering) shared across modes.
9. Tests — unit tests with a mock LLM backend; integration test gated by
   `MY20Q_INTEGRATION=1`.

**Verification**: `pytest` (21 passed, 1 integration skipped); `python -m
my20q` completes a full dialogue against `gemma3:12b` via Ollama, or in
`--no-llm` fallback mode against the bundled taxonomy.

## Phase 2 — Web UI (PWA) + FastAPI service

**Goal**: tablet-ready interface, served over Tailscale from cerberus.

1. FastAPI app in `src/my20q/api/`:
   - `POST /session` → create session, return initial category screen.
   - `POST /session/{id}/answer` → submit yes/no/skip, return next question +
     image refs.
   - `GET /session/{id}/state` → resume support.
   - Static mounts for `/assets/` and the built PWA.
   - Optional SSE for streaming question text.
2. Frontend — decision at start of Phase 2, narrowed to:
   - **Recommended**: Vite + vanilla TS (or preact). Zero tablet-side runtime
     deps, trivial kiosk deployment.
   - Alternative: SvelteKit if offline-cache cleverness is needed.
3. UX baselines baked in from `CLAUDE.md` (huge tap targets, one question per
   screen, persistent Start-Over / Emergency, AAA contrast).
4. `scripts/fetch_icons.py` pulls ARASAAC pictograms via their open API and
   Mulberry from GitHub, writing an attribution manifest.
5. Tailscale deployment doc in `docs/` covering MagicDNS cert, systemd unit
   for the FastAPI service on cerberus, kiosk-browser setup on the Android
   tablet.

**Verification**: complete a dialogue from a desktop browser over Tailscale,
then from the tablet in kiosk mode. Lighthouse accessibility audit ≥ 95.

## Phase 3 — Clinical & usability iteration

1. Local-only session logging for caregiver review — taxonomy paths +
   timestamps, never free text.
2. TTS output via `piper` (local, fast).
3. Expand taxonomy with caregiver input; per-patient customization (family
   names, medications, hobbies, favorite topics).
4. Latency budget: < 1.5 s per question on cerberus-class hardware.

## Phase 4 — Native Linux tablet app (stretch)

Revisit only after Phase 2 validates the UX clinically. Options:

- **Tauri** shell wrapping the PWA (lowest risk, reuses frontend).
- **GTK4 + libadwaita** (best-in-class Linux feel, commits us to a tablet).
- **Qt6 / QML** (broadest Linux-tablet hardware support).

Decision deferred. The PWA remains the primary deliverable regardless.
