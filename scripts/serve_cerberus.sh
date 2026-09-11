#!/usr/bin/env bash
# One-command start/stop for the my20Q cockpit on cerberus, for remote trials
# over Termius (SSH) + Tailscale. Cerberus is a Windows box driven via
# MSYS2 / git-bash + tmux, so this is a bash script that launches the Windows
# venv python and tmux. The API lives in a detached tmux session, so it survives
# your SSH disconnect and you can re-attach any time to watch the logs.
#
#   ./scripts/serve_cerberus.sh            start: API in tmux + Tailscale serve
#   ./scripts/serve_cerberus.sh synthetic  same, but the DEMO persona — no real
#                                          patient data; dev capture ON
#   ./scripts/serve_cerberus.sh logs       attach to the API tmux (Ctrl-b then d to detach)
#   ./scripts/serve_cerberus.sh status     what's running + the cockpit URL
#   ./scripts/serve_cerberus.sh stop       tear it all down
#
# Run it from the repo root. If not executable: `bash scripts/serve_cerberus.sh`.
set -uo pipefail

SESSION="my20q"
PORT="${MY20Q_API_PORT:-8000}"
REPO="$HOME/Git/GitHub/my20Q"
# Per-project venv (built with `uv venv ~/venvs/my20q` + `uv pip install -e .[api,trials]`).
# Override with MY20Q_PY to point elsewhere (e.g. the old shared ~/venv) if needed.
PY="${MY20Q_PY:-$HOME/venvs/my20q/Scripts/python.exe}"

# Local piper TTS — use the shell's values if already exported, else the paths
# persisted on cerberus (via setx). Inlined into the tmux command below so the
# API gets them even if the tmux server was started with a stale environment.
export MY20Q_PIPER_BIN="${MY20Q_PIPER_BIN:-/c/Users/grey_/piper/piper/piper.exe}"
export MY20Q_PIPER_MODEL="${MY20Q_PIPER_MODEL:-/c/Users/grey_/piper/voices/en_US-amy-medium.onnx}"

# Optional Kokoro TTS (warmer voice than piper). Only used when
# MY20Q_TTS_ENGINE=kokoro; honored from the shell if exported. Inlined below for
# the same stale-tmux reason. Empty = stay on piper.
export MY20Q_TTS_ENGINE="${MY20Q_TTS_ENGINE:-}"
export MY20Q_KOKORO_MODEL="${MY20Q_KOKORO_MODEL:-}"
export MY20Q_KOKORO_VOICES="${MY20Q_KOKORO_VOICES:-}"
export MY20Q_KOKORO_VOICE="${MY20Q_KOKORO_VOICE:-}"

# Per-LLM-call timeout. A thinking model takes a two-phase deliberate→format pass
# per question and is slow, so the 30s default is too tight — give it room before
# degrading to fallback (which now only asks questions; it never ends the round).
export MY20Q_OLLAMA_TIMEOUT="${MY20Q_OLLAMA_TIMEOUT:-120}"

# Reasoning behavior knobs (see config.ReasoningTuning). Empty = code default.
# These are the ones that actually do something:
#   EXPLORE_DECAY   explore prob = base^(slot_mass/ready + 1), base in [0,1]
#                   (default 0.67; per-SLOT since 09-11 — high where the focus
#                    slot knows nothing, low where it is already confident)
#   SOFT_RESET_NOS  consecutive "no"s that trigger a restart        (default 10)
#   STALL_WINDOW    answers with no new confirmation -> restart     (default 8)
#   FACET_READY     points at which a slot counts as established    (default 2.0)
#   SPLIT_MARGIN    tie width, and the confidence lead              (default 1.0)
#   RETIRE_X        retire at >= X * FACET_READY, runner <= half    (default 3.0)
#
# MY20Q_MIN_YES / NEW_YES / REPHRASE_LIMIT / SYNTH_ATTEMPTS were REMOVED on
# 09-11. They had had no engine effect since the living proposal banner replaced
# engine-initiated synthesis in 06-11, but were still parsed, clamped, exported
# here and documented above as live tuning — so setting them looked like it did
# something and never did. A round never ends on its own: only the caregiver's
# ✓ on the banner concludes one.
export MY20Q_EXPLORE_DECAY="${MY20Q_EXPLORE_DECAY:-}"
export MY20Q_SOFT_RESET_NOS="${MY20Q_SOFT_RESET_NOS:-}"
export MY20Q_STALL_WINDOW="${MY20Q_STALL_WINDOW:-}"

# Patient profile. Honor an explicit MY20Q_PROFILE if exported; otherwise fall
# back to the standard real-patient location when that file is present. A real
# profile engages the privacy invariant (local-only LLM + recording on), so we
# only default it in when the file actually exists — never invent a path.
#
# `${MY20Q_PROFILE+set}`, NOT `${MY20Q_PROFILE:-}`: the second treats an
# exported EMPTY value as unset, so `MY20Q_PROFILE= ./serve_cerberus.sh start`
# — the obvious way to ask for a no-patient run — silently loaded the real
# profile instead. Setting it to anything, empty included, now opts out.
if [ -z "${MY20Q_PROFILE+set}" ] && [ -f "$REPO/patient_profiles/patient.yaml" ]; then
  export MY20Q_PROFILE="$REPO/patient_profiles/patient.yaml"
fi

magic_url() {
  local dns
  dns="$(tailscale status --json 2>/dev/null \
    | "$PY" -c "import json,sys;
try: print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))
except Exception: pass" 2>/dev/null)"
  [ -n "${dns:-}" ] && echo "https://$dns/" || echo "https://<cerberus-magicdns>/"
}

wait_health() {
  for _ in $(seq 1 20); do
    curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

start() {
  command -v tmux >/dev/null || { echo "tmux not found on PATH"; exit 1; }
  # Reasoning needs Ollama; warn (non-fatal — the app degrades to fallback).
  curl -fsS --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1 \
    || echo "WARN: Ollama not reachable on :11434 — reasoning will fall back. (ollama serve)"

  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Already running in tmux '$SESSION'.  logs: $0 logs   stop: $0 stop"
  else
    tmux new-session -d -s "$SESSION" -c "$REPO" \
      "MY20Q_PIPER_BIN='$MY20Q_PIPER_BIN' MY20Q_PIPER_MODEL='$MY20Q_PIPER_MODEL' MY20Q_TTS_ENGINE='${MY20Q_TTS_ENGINE:-}' MY20Q_KOKORO_MODEL='${MY20Q_KOKORO_MODEL:-}' MY20Q_KOKORO_VOICES='${MY20Q_KOKORO_VOICES:-}' MY20Q_KOKORO_VOICE='${MY20Q_KOKORO_VOICE:-}' MY20Q_OLLAMA_TIMEOUT='$MY20Q_OLLAMA_TIMEOUT' MY20Q_EXPLORE_DECAY='${MY20Q_EXPLORE_DECAY:-}' MY20Q_SOFT_RESET_NOS='${MY20Q_SOFT_RESET_NOS:-}' MY20Q_STALL_WINDOW='${MY20Q_STALL_WINDOW:-}' MY20Q_PROFILE='${MY20Q_PROFILE:-}' MY20Q_DEV_CAPTURE='${MY20Q_DEV_CAPTURE:-}' MY20Q_API_PORT='$PORT' '$PY' -m my20q.api"
    echo "API started in tmux '$SESSION' (127.0.0.1:$PORT)"
    if [ -n "${MY20Q_PROFILE:-}" ]; then
      echo "Profile:  $MY20Q_PROFILE"
    else
      echo "Profile:  none (synthetic/dev)"
    fi
    if [ "${MY20Q_DEV_CAPTURE:-}" = "1" ]; then
      echo "Capture:  dev_recordings/  (synthetic only — refused if a real profile loads)"
    else
      echo "Capture:  patient dataset if a real profile is loaded, otherwise none"
    fi
  fi

  # Front it over HTTPS on the tailnet (Tailscale backgrounds this itself).
  if tailscale serve --bg "$PORT" >/dev/null 2>&1; then
    echo "Tailscale serve -> 127.0.0.1:$PORT"
  else
    echo "WARN: 'tailscale serve --bg $PORT' failed — run it manually."
  fi

  if wait_health; then
    echo "Health: OK"
  else
    echo "Health: not up yet — check the logs ($0 logs)."
  fi
  echo
  echo "  cockpit:  $(magic_url)"
  echo "  logs:     $0 logs        (Ctrl-b then d to detach, leaves it running)"
  echo "  stop:     $0 stop"
}

case "${1:-start}" in
  start) start ;;
  synthetic)
    # Mechanism-only run: the packaged DEMO persona (`synthetic: true`), which
    # is the privacy pivot — no real patient context ever reaches the model.
    #
    # DEV CAPTURE is on so the round records survive for autopsy. That is a
    # different artifact from the patient dataset, with the mirror-image guard:
    # it writes only for a synthetic persona, into dev_recordings/, and the API
    # refuses to enable it if a real profile is loaded. The patient dataset
    # stays exactly as it was — real patient only, local LLM only.
    export MY20Q_PROFILE="$REPO/src/my20q/profiles/data/synthetic_demo.yaml"
    export MY20Q_DEV_CAPTURE=1
    echo "SYNTHETIC persona — no real patient data; DEV CAPTURE on (dev_recordings/)."
    start
    ;;
  logs)  tmux attach -t "$SESSION" ;;
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "tmux '$SESSION' stopped" \
      || echo "no tmux '$SESSION'"
    tailscale serve reset >/dev/null 2>&1 && echo "tailscale serve reset" || true
    ;;
  status)
    tmux has-session -t "$SESSION" 2>/dev/null \
      && echo "tmux '$SESSION': RUNNING" || echo "tmux '$SESSION': not running"
    curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" 2>/dev/null \
      && echo "  (API health OK on :$PORT)" || echo "  (API not responding on :$PORT)"
    echo "--- tailscale serve ---"; tailscale serve status 2>/dev/null || true
    echo "  cockpit:  $(magic_url)"
    ;;
  *) echo "usage: $0 [start|synthetic|logs|status|stop]"; exit 1 ;;
esac
