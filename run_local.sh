#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

export OPENCODE_BASE_URL="${OPENCODE_BASE_URL:-https://api.moonshot.ai/v1}"
export OPENCODE_MODEL="${OPENCODE_MODEL:-kimi-k2.6}"

if [[ -z "${OPENCODE_API_KEY:-}" ]]; then
  echo "OPENCODE_API_KEY is not set."
  read -r -s -p "Paste OPENCODE_API_KEY: " OPENCODE_API_KEY
  echo
  if [[ -z "${OPENCODE_API_KEY}" ]]; then
    echo "No key provided. Exiting."
    exit 1
  fi
  export OPENCODE_API_KEY
fi

python3 -m pip install -r requirements.txt
exec python3 app.py
