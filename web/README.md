# my20Q — Caregiver Cockpit

Preact + Vite frontend for the caregiver-driven cockpit.

> **Wired stage.** The cockpit now talks to the FastAPI backend — there is
> no mock data. Start the backend first, then the cockpit. See
> `../docs/design/beta-retool.md` §6.

## Run it

Two processes:

```bash
# 1. Backend (from the repo root)
python -m my20q.api                 # serves http://localhost:8000

# 2. Cockpit (from web/)
npm install                         # once
npm run dev                         # http://localhost:5173
```

Vite proxies `/api/*` to the backend, so the browser sees one origin.

```bash
npm run build      # production build to dist/
npm run typecheck  # tsc --noEmit
```

(From the repo root, add `--prefix web` to any `npm` command.)

## Layout

Two tabs in the topbar: **Live** (the cockpit) and **Review** (session
playback). Topbar also has the topic ▼, engine badge, a **Model** selector
(pick the local Ollama model for human-interaction trials — applies to the next
question), ⬇ Save, recording light, 🔊 audio toggle, and theme.

While the model is generating, the **Conversation** window shows a prominent
animated "Thinking…" banner (latency feedback where the caregiver is looking,
not just in the reasoning tile).

```
┌─ brand · Live|Review ── Topic ▼ ── ⬇ · ● REC · 🔊 · ☀ ─┐
│ ┌──────────────────────┐ ┌─────────────────────┐ │
│ │ 1 Conversation       │ │ 2 Live reasoning    │ │
│ │   transcript + live  │ │   consensus board + │ │
│ │   query / synthesis  │ │   SSE + sliders     │ │
│ ├──────────────────────┤ │                     │ │
│ │ 3 Input  y/n/k/s/u/q │ │                     │ │
│ └──────────────────────┘ └─────────────────────┘ │
└────────────────────────────────────────────────────┘
```

> The Pictogram tile is shelved (curated retrieval mostly fell back to "?" in
> real sessions). The component and backend retrieval are retained — re-mount
> once the image slot is driven by a generator.

**Consensus board** (in the reasoning tile): the honest reasoning view. The
engine tracks the need as six 5W1H slots — Who / What / When / Where / Why /
How — each holding contender values with additive consensus points. The tile
renders one row per slot: contender chips with raw points (leader bolded,
negatives dimmed), the currently-targeted slot pulsing (from `event.facets`).
When reasoning fails, the conversation tile shows a **diagnostic card** with
the failure reason and a **Retry** button — never a canned question.

**Emotion sliders** (in the reasoning tile): ten opposed-emotion pairs, each a
coarse 5-detent scale (strong/mild each side + neutral) with large pole labels.
Every change posts the full reading to the backend to colour the next query;
a **Reset** pill snaps all to neutral.

## Review tab

Loads a saved/recorded `.jsonl` and renders the whole conversation as one
**scrollable transcript**. The active step (question + reasoning + answer) is
highlighted and auto-scrolls into view, descending one pair at a time:

- **Prev / Next** move the highlight; click any step to jump to it.
- **▶ Auto-play** steps through on its own.
- With 🔊 audio available (piper or kokoro), each step is read aloud as
  *question → reasoning → "the patient then indicated &lt;answer&gt;"*;
  auto-play advances only after each readout finishes.

## Keyboard shortcuts

`Y` yes · `N` no · `K` kinda · `S` not sure · `U` undo · `Q` new round.
Ignored while the caregiver-context field is focused, and in Review mode.

## Backend down?

If the API is not running the cockpit shows a red banner —
`python -m my20q.api` starts it.
