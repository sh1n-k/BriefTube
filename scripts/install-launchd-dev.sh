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
APP_CONFIG_FILE_PATH="${APP_CONFIG_FILE:-${ROOT_DIR}/config.dev.yaml}"
HOST_VALUE="${HOST:-127.0.0.1}"
PORT_VALUE="${PORT:-8000}"
PATH_VALUE="${PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}"
LAUNCHER_PATH="${ROOT_DIR}/scripts/brieftube-dev-daemon.sh"
DRY_RUN="${BRIEFTUBE_LAUNCHD_DRY_RUN:-0}"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF
LaunchAgent dry run:
  action: install
  label: ${LABEL}
  plist: ${PLIST_PATH}
  launcher: ${LAUNCHER_PATH}
  working_directory: ${ROOT_DIR}
  app_config_file: ${APP_CONFIG_FILE_PATH}
  host: ${HOST_VALUE}
  port: ${PORT_VALUE}
  stdout: ${STDOUT_PATH}
  stderr: ${STDERR_PATH}
  service_target: gui/$(id -u)/${LABEL}
  legacy_labels: ${LEGACY_LABELS[*]}
  would_create_dirs:
    ${PLIST_DIR}
    ${LOG_DIR}
  would_bootstrap: launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"
  would_kickstart: launchctl kickstart -k "gui/$(id -u)/${LABEL}"
EOF
  exit 0
fi

mkdir -p "${PLIST_DIR}" "${LOG_DIR}"

for legacy_label in "${LEGACY_LABELS[@]}"; do
  legacy_plist="${PLIST_DIR}/${legacy_label}.plist"
  launchctl bootout "gui/$(id -u)/${legacy_label}" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u)" "${legacy_plist}" >/dev/null 2>&1 || true
  rm -f "${legacy_plist}"
done

cat >"${PLIST_PATH}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
      <string>${LAUNCHER_PATH}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${ROOT_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
      <key>APP_CONFIG_FILE</key>
      <string>${APP_CONFIG_FILE_PATH}</string>
      <key>HOME</key>
      <string>${HOME}</string>
      <key>HOST</key>
      <string>${HOST_VALUE}</string>
      <key>PATH</key>
      <string>${PATH_VALUE}</string>
      <key>PORT</key>
      <string>${PORT_VALUE}</string>
    </dict>

    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${STDOUT_PATH}</string>
    <key>StandardErrorPath</key>
    <string>${STDERR_PATH}</string>
  </dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"
launchctl enable "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

cat <<EOF
Installed LaunchAgent:
  label: ${LABEL}
  plist: ${PLIST_PATH}
  app: http://${HOST_VALUE}:${PORT_VALUE}

Status:
  launchctl print gui/$(id -u)/${LABEL}
Logs:
  tail -f ${ROOT_DIR}/logs/dev/brieftube-dev.log
  tail -f ${STDOUT_PATH}
  tail -f ${STDERR_PATH}
EOF
