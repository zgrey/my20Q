#!/usr/bin/env bash
# Run the my20Q CLI against a local Ollama instance.
#
# Prerequisite: `ollama serve` running, and the model pulled:
#   ollama pull llama3.2:3b
#
# Override the model / URL / timeout via environment variables; see
# src/my20q/config.py for all options.

set -euo pipefail

: "${MY20Q_OLLAMA_URL:=http://localhost:11434}"
: "${MY20Q_OLLAMA_MODEL:=llama3.2:3b}"

export MY20Q_OLLAMA_URL MY20Q_OLLAMA_MODEL

exec python -m my20q "$@"
