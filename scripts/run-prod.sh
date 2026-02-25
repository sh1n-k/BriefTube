#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export APP_CONFIG_FILE="${APP_CONFIG_FILE:-config.prod.yaml}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

if [[ -x ".venv/bin/uvicorn" ]]; then
  UVICORN_BIN=".venv/bin/uvicorn"
else
  UVICORN_BIN="uvicorn"
fi

exec "$UVICORN_BIN" app.main:app --host "$HOST" --port "$PORT"
