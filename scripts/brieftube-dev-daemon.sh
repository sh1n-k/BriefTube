#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export APP_CONFIG_FILE="${APP_CONFIG_FILE:-config.dev.yaml}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

UV_BIN="${UV_BIN:-$(command -v uv 2>/dev/null || true)}"
if [[ -z "${UV_BIN}" ]]; then
  echo "uv is required but was not found on PATH." >&2
  exit 127
fi

exec -a "BriefTube Dev" "$UV_BIN" run python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
