#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${BRIEFTUBE_LAUNCHD_LABEL:-BriefTube.dev}"
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
HOST_VALUE="${HOST:-127.0.0.1}"
PORT_VALUE="${PORT:-8000}"
SERVICE_TARGET="gui/$(id -u)/${LABEL}"

if [[ ! -f "${PLIST_PATH}" ]]; then
  cat <<EOF
LaunchAgent plist not found:
  ${PLIST_PATH}

Install it first:
  ${ROOT_DIR}/scripts/install-launchd-dev.sh
EOF
  exit 1
fi

launchctl enable "${SERVICE_TARGET}" >/dev/null 2>&1 || true
launchctl kickstart -k "${SERVICE_TARGET}"

sleep 1

cat <<EOF
Restarted LaunchAgent:
  label: ${LABEL}
  app: http://${HOST_VALUE}:${PORT_VALUE}

Status:
  launchctl print ${SERVICE_TARGET}
EOF
