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

```
┌─ brand · engine ────── Topic ▼ ──────── ● REC · ☀ ─┐
│ ┌──────────────────────┐ ┌─────────────────────┐ │
│ │ 1 Conversation       │ │ 2 Pictogram         │ │
│ │   transcript + live  │ ├─────────────────────┤ │
│ │   query / synthesis  │ │ 3 Live reasoning    │ │
│ ├──────────────────────┤ │                     │ │
│ │ 4 Input  y/n/k/s/u/q │ │                     │ │
│ └──────────────────────┘ └─────────────────────┘ │
└────────────────────────────────────────────────────┘
```

## Keyboard shortcuts

`Y` yes · `N` no · `K` kinda · `S` not sure · `U` undo · `Q` new round.
Ignored while the caregiver-context field is focused.

## Backend down?

If the API is not running the cockpit shows a red banner —
`python -m my20q.api` starts it.
