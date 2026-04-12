# my20Q Roadmap

A phased plan for building the assistive 20-questions agent. Each phase is
independently testable and produces a usable artifact.

## Phase 0 — Repo scaffolding ✅

**Status**: in progress (this commit).

Deliverables:

- `CLAUDE.md` at repo root documenting architecture, decisions, UX/safety
  principles, and future ideas.
- `docs/ROADMAP.md` (this file).

No runnable code yet.

## Phase 1 — Python backend MVP (CLI-testable)

**Goal**: a working dialogue engine with Ollama, exercised from a terminal
before any UI is built.

1. `pyproject.toml` with deps: `fastapi`, `uvicorn[standard]`, `httpx`,
   `pydantic>=2`, `pyyaml`, `rich`, `pytest`, `ruff`. `src/` layout.
2. `src/my20q/llm/ollama_client.py` — thin async client over `/api/chat`.
   Abstract `LLMBackend` Protocol so llama.cpp or vLLM swap in later.
3. `src/my20q/taxonomy/` — initial YAML tree:
   - 4 top categories: mental health, physical health, emergency, general
     assistance.
   - ~3 sublevels each, each node carrying `id`, `label`, `image_ref`,
     `example_questions`, `terminal_actions`, optional `emergency: true`.
4. `src/my20q/agent/dialogue.py` — session state machine:
   - Seeded from current taxonomy node.
   - Each turn: LLM proposes a yes/no/not-sure question scoped by the current
     subtree. Candidate questions re-ranked by information gain (prefer
     splits that halve the remaining leaves).
   - `yes` → descend; `no` → prune; `not sure` → mark uncertain, try another
     axis.
   - Converge when one leaf remains or user accepts a proposed answer.
5. `src/my20q/agent/prompts.py` — templated system prompt: short questions,
   concrete concepts, no medical advice, always-available start-over hint.
6. `python -m my20q.cli` — Rich-based CLI for fast iteration before UI exists.
7. Safety layer — emergency detector surfaces a hard-coded caregiver/911
   screen regardless of LLM state.
8. Tests — unit tests with a mock LLM; optional integration test with a tiny
   local model, gated by `MY20Q_INTEGRATION=1`.

**Verification**: `pytest` passes; `python -m my20q.cli` completes a full
dialogue against `llama3.2:3b` (or `phi-4-mini`) running via Ollama.

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
