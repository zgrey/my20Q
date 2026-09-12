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
question), ⬇ Save, recording light, 🔊 audio toggle, theme, **⤢ full screen**
and **⏻ Quit**.

**⤢ Full screen / return to the browser** — one button, both directions. It is
hidden where the browser has no Fullscreen API at all (iPhone Safari), rather
than offering a control that does nothing.

**⏻ Quit** — ends the session *for real*: the server finalizes and records any
live round, then shuts itself down, and the cockpit is replaced by a "safe to
exit" screen. It arms on the first press and commits on the second (eight
seconds, then it disarms). The round-level control in the input tile is
**New round** (`Q`) — it abandons the current round and opens a fresh one on
the same topic, which is all the button labelled "Quit" ever did.

While the model is generating, the **Conversation** window shows a prominent
animated "Thinking…" banner (latency feedback where the caregiver is looking,
not just in the reasoning tile).

```
┌─ brand · Live|Review ── Topic ▼ ── ⬇ · ● REC · 🔊 · ☀ · ⤢ · ⏻ ─┐
│ ┌─ PROPOSAL BANNER — “I need/want … for/from …” 🔊⟳✓ ─┐ │
│ ├──────────────────────┃─────────────────────┤ │
│ │ 1 Conversation       ┃ 2 Live reasoning    │ │
│ │   transcript + live  ┃   consensus board + │ │
│ │   query / verify     ┃   chains+SSE+sliders│ │
│ ├━━━━━━━━━━━━━━━━━━━━━━┫                     │ │
│ │ 3 Input y/n/k/s·⇄o·u/q┃                    │ │
│ └──────────────────────┸─────────────────────┘ │
└────────────────────────────────────────────────────┘
     ┃ ━ = drag to resize · double-click to reset
```

**Resizable tiles.** The gutters (`┃` and `━` above) are splitters: drag to
resize, double-click to restore the default. They work with a mouse, a finger
or a pencil, and the column split is remembered as a *proportion*, so it
survives rotating the iPad. Below 700px wide the tiles stack and the splitters
disappear.

**The shell is sized to the visible viewport**, not to `100vh`: on iOS `100vh`
is the height the page *would* have with the toolbars hidden, which used to put
the caregiver-context field below the window with no way to scroll to it. The
measured `visualViewport` also accounts for the on-screen keyboard, which no
CSS unit reports.

**Proposal banner** (top, owner-designed): the evolving draft utterance, set
large — it is what the whole round is for. Populated from the first converged
slot with ambiguous alternates and a trailing ellipsis ("I need/want something
for/from Rob …"); glowing *Pending synthesis…* before that; a breathing accent
halo + pulsing ✓ when the board says propose-ready. Per-part emphasis:
**locked** values underlined solid, *working* values dotted + pulsing.

*While a question is generating*, each **working** segment cycles through the
consensus board's actual contenders for its slot (italic, dashed) above a
sweeping bar reading "thinking — the wording can still change". The animation is
made of the round's own data: it shows *what is being weighed*, and it is the
signal that the draft is not yet stable enough to edit. Locked segments never
move, and `prefers-reduced-motion` stops it entirely.

Controls: **🔊 Speak**
(reads the draft; slashes spoken as "or"), **⟳ Restate** (say the same
thing slightly differently — draft text only), **✓** accept — pops the
explicit confirmation modal (dimmed backdrop, the final utterance front and
center, spoken, one **New round** button).

**The synthesis editor**: the draft's woven segments are **clickable**.
Clicking one opens the editor strip — the slot's top board candidates as
one-tap chips, a free-text replacement (a word or a grouped phrase; Enter
applies), and **✕ remove this detail** (mutes the slot). Replacements are
refine-or-replace: an extension ("tickets" → "Avalanche tickets") deepens
the draft without striking anything; a swap strikes the old value (struck
chip) and stands the new one in. All edits are undoable via Undo. The
engine never proposes on its own — the banner is the only synthesis path.

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

**⇄ Opposite** (in the input tile's action row): re-renders the pending
question in its opposite connotation — who-does-for-whom mirrored, or the key
detail reversed — and keeps waiting. An *action*, not an answer: nothing is
recorded until the flipped question is answered, and a failed flip leaves the
question untouched.

**🔊 Repeat** (in the input tile's action row): re-speaks the current question
aloud, unchanged. Purely an output control — it records nothing, advances
nothing, and never re-asks the engine.

**Emotion sliders** (in the reasoning tile): ten opposed-emotion pairs, each a
coarse 5-detent scale (strong/mild each side + neutral) with large pole labels.
Every change posts the full reading to the backend to colour the next query;
a **Reset** pill snaps all to neutral.

**Hide / expand.** Both sections of the reasoning tile — the consensus board and
the emotion sliders — collapse from their headings and are remembered across
sessions. Collapsed, each heading still carries information ("6 slots scored",
"set") rather than going blank.

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

`Y` yes · `N` no · `K` kinda · `S` not sure · `O` opposite (flip) · `U` undo ·
`Q` new round. Ignored while the caregiver-context field is focused, and in
Review mode.

## Backend down?

If the API is not running the cockpit shows a red banner —
`python -m my20q.api` starts it.
