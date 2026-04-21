#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${BRIEFTUBE_LAUNCHD_LABEL:-BriefTube.dev}"
LEGACY_LABELS=("local.brieftube.dev")
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
LOG_DIR="${ROOT_DIR}/logs/launchd"
STDOUT_PATH="${LOG_DIR}/brieftube-dev.stdout.log"
STDERR_PATH="${LOG_DIR}/brieftube-dev.stderr.log"
DRY_RUN="${BRIEFTUBE_LAUNCHD_DRY_RUN:-0}"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF
LaunchAgent dry run:
  action: uninstall
  label: ${LABEL}
  plist: ${PLIST_PATH}
  stdout: ${STDOUT_PATH}
  stderr: ${STDERR_PATH}
  service_target: gui/$(id -u)/${LABEL}
  legacy_labels: ${LEGACY_LABELS[*]}
  would_remove:
    ${PLIST_PATH}
    ${STDOUT_PATH}
    ${STDERR_PATH}
EOF
  exit 0
fi

launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "${PLIST_PATH}" >/dev/null 2>&1 || true

for legacy_label in "${LEGACY_LABELS[@]}"; do
  legacy_plist="${PLIST_DIR}/${legacy_label}.plist"
  launchctl bootout "gui/$(id -u)/${legacy_label}" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u)" "${legacy_plist}" >/dev/null 2>&1 || true
  rm -f "${legacy_plist}"
done

rm -f "${PLIST_PATH}"
rm -f "${STDOUT_PATH}" "${STDERR_PATH}"

cat <<EOF
Uninstalled LaunchAgent:
  label: ${LABEL}
  plist removed: ${PLIST_PATH}
EOF
