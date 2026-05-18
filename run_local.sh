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
export TAX_TUTOR_OPENCODE_MODEL="${TAX_TUTOR_OPENCODE_MODEL:-opencode/qwen3.6-plus-free}"
export MOONSHOT_URL="${MOONSHOT_URL:-$OPENCODE_BASE_URL}"
export MOONSHOT_MODEL="${MOONSHOT_MODEL:-kimi-k2.6}"

python3 -m pip install -r requirements.txt
exec python3 app.py
