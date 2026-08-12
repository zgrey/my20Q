# my20Q Roadmap

A phased plan for building the assistive speech-emulator agent. Each phase is
independently testable and produces a usable artifact.

> Restructured 2026-05-18 after the Phase 1 trials. The design behind Phases 2+
> lives in [`design/beta-retool.md`](design/beta-retool.md) — the source of
> truth for beta scope. Phases 0–1 are historical and complete.

> **⚠ The Phase 2 entry below is stale (2026-08-11).** It predates the
> 5W1H facet-controller rebuild and the entire banner era, and its
> verification numbers are wrong (211 tests pass now, not 51). For the
> current state, what remains, and the merge plan, read
> [`design/phase2-finalization.md`](design/phase2-finalization.md). Rewriting
> this section is item F4 of that plan.

## Phase 0 — Repo scaffolding ✅

- `CLAUDE.md` documenting architecture, decisions, UX/safety principles.
- `docs/ROADMAP.md` (this file).

## Phase 1 — Python backend MVP (CLI-testable) ✅

A working dialogue engine with Ollama, exercised from a terminal before any UI.

- `pyproject.toml`, `src/` layout, `LLMBackend` Protocol + Ollama client + mock.
- Taxonomy YAML tree; `agent/dialogue.py` two-mode state machine (reasoning +
  fallback); `agent/reasoner.py` strict-JSON action proposer; `agent/prompts.py`.
- `python -m my20q` Rich CLI harness; safety layer; unit + gated integration
  tests.

**Verification**: `pytest` green; `python -m my20q` completes a full dialogue
against `gemma4:e4b` (default), or in `--no-llm` fallback mode.

The trials of this MVP motivated the retool — see `design/beta-retool.md` §1.
The dialogue engine here is the seed for the Phase 2 cockpit.

## Phase 2 — Caregiver Cockpit (beta) ✅

The caregiver-driven web cockpit — the real product interface. Delivered:

- **FastAPI backend** + local server; the async engine; round-lifecycle
  endpoints; an SSE progress channel; serves the built cockpit and the
  pictogram assets.
- **Preact + Vite cockpit** — four tiles (conversation · AAC pictogram · live
  reasoning + emotional sliders · input), persistent topic dropdown, recording
  light with a dataset-size monitor, dark/light theme; `y/n/k/s/q` + undo +
  mid-round caregiver context.
- **Retooled dialogue engine** — session/round/query model, async,
  synthesis-terminated rounds, rewindable history/undo, training/operational
  mode axis, and the format auditor (re-prompts non-yes/no queries).
  **Rebuilt 2026-06-10 as the 5W1H facet controller**: per-slot consensus
  scores (who/what/when/where/why/how) anchored to the question text, a
  code-level focus policy (probe → split ties → drill), a hard repeat gate,
  leader-weaving synthesis with placeholders, and a unified restart recovery —
  canned fallback questions removed in favor of diagnostic cards (see
  `design/reasoning-retro.md` §8).
- **Flat topic list** replacing the taxonomy tree; **3-tier recording/dataset**
  writer + Job-B metric, gated by `real_patient_profile_loaded` + a caregiver
  pause control; **pictogram retrieval** from a curated ARASAAC catalog.
- **Ollama + Anthropic/Opus backends** behind the hard privacy gate; minimal
  patient-profile loader; **emotional sliders** feeding the reasoner.
- CLI harness rewired to the async engine.

**Verification**: `pytest` (51 passed, 1 gated integration skipped); `ruff`
clean; cockpit typecheck + build clean; the Opus backend is refused when a
real profile is loaded.

Backlog raised during the build (see `design/beta-retool.md` §15): emergency
false-alarm metric, metrics-over-time visualization, an emotion-weighted
conditional-sampling scheme, and an on-topic LLM auditor.

## Phase 3 — Caregiver interview tool + knowledge graph

**Goal**: Job A — controlled, human-curated patient context.

1. **Caregiver interview tool** — caregiver-initiated, nudged after ~10 logged
   rounds. Opens with open-recall prompts, then grounds against the logs and
   proposes concrete graph edits the caregiver approves or rejects.
2. **Knowledge-graph data model** — edge weight = decayed count of
   caregiver-confirmed occurrences; written only via the interview tool.
3. **Interactive graph visualization** that builds as edits are approved.
4. **Expensive loop detection** — bridging queries across oscillating topics;
   topic-correlations feeding the graph.

## Phase 3.5 — Scheduling rounds (owner-requested 2026-08-11)

**Goal**: a scheduling-specific mode of play — rounds that converge on
scheduling logistics or event planning and synthesize an utterance in that
register ("Aaron's dinner is Saturday — at our house?").

Notable because it is the first topic where `when` carries the round: every
existing topic ranks `when` 5th or last, and the trials treated `when`-farming
as waste. Time is natively hierarchical, so it leans hard on W3-H refinement
links. Not yet iterated or designed — the requirement, the structural notes,
and the open questions are recorded in
[`design/scheduling-rounds.md`](design/scheduling-rounds.md).

## Phase 4 — Patient operational interface

Revisit only after Phase 2 validates the cockpit. The aphasia-oriented secondary
input surface as a real interface; operational mode (patient-solo, noisy input,
graph-driven inference). Bound by the patient-interface UX principles in
`CLAUDE.md`.

## Phase 5 — Clinical & usability iteration

1. TTS output via `piper` (local, fast).
2. Full caregiver-configured patient profile (family names, medications,
   hobbies, dietary/sensory preferences, frequent-need shortcuts).
3. Latency budget: < 1.5 s per query on cerberus-class hardware.
4. Caregiver review of recorded sessions.

## Phase 6 — Native Linux tablet app (stretch)

Tauri shell wrapping the web frontend, or GTK4 + libadwaita. Decision deferred;
the web app remains the primary deliverable regardless.
