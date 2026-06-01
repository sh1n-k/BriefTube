#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${BRIEFTUBE_LAUNCHD_LABEL:-BriefTube.prod}"
LEGACY_LABELS=("com.brieftube.server" "local.brieftube.prod")
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
LOG_DIR="${ROOT_DIR}/logs/launchd"
LAUNCHD_LOG_PATH="${LOG_DIR}/BriefTube.log"
LEGACY_STDOUT_PATH="${LOG_DIR}/brieftube-prod.stdout.log"
LEGACY_STDERR_PATH="${LOG_DIR}/brieftube-prod.stderr.log"
DRY_RUN="${BRIEFTUBE_LAUNCHD_DRY_RUN:-0}"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF
LaunchAgent dry run:
  action: uninstall
  label: ${LABEL}
  plist: ${PLIST_PATH}
  launchd_log: ${LAUNCHD_LOG_PATH}
  legacy_stdout: ${LEGACY_STDOUT_PATH}
  legacy_stderr: ${LEGACY_STDERR_PATH}
  service_target: gui/$(id -u)/${LABEL}
  legacy_labels: ${LEGACY_LABELS[*]}
  would_remove:
    ${PLIST_PATH}
    ${LAUNCHD_LOG_PATH}
    ${LEGACY_STDOUT_PATH}
    ${LEGACY_STDERR_PATH}
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
rm -f "${LAUNCHD_LOG_PATH}" "${LEGACY_STDOUT_PATH}" "${LEGACY_STDERR_PATH}"

cat <<EOF
Uninstalled LaunchAgent:
  label: ${LABEL}
  plist removed: ${PLIST_PATH}
EOF
