#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install uv and run 'uv sync' first." >&2
  exit 1
fi

if [[ -z "${BRIEFTUBE_REMOTE_SYNC_DSN:-}" ]]; then
  echo "BRIEFTUBE_REMOTE_SYNC_DSN is required for remote sync." >&2
  exit 1
fi

export APP_CONFIG_FILE="${APP_CONFIG_FILE:-config.prod.yaml}"
export BRIEFTUBE_REMOTE_SYNC_ENABLED="${BRIEFTUBE_REMOTE_SYNC_ENABLED:-true}"

exec uv run brieftube
