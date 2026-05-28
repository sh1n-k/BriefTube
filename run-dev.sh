#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install uv and run 'uv sync' first." >&2
  exit 1
fi

export APP_CONFIG_FILE="${APP_CONFIG_FILE:-config.dev.yaml}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-48080}"

exec uv run python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
