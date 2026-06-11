#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${BRIEFTUBE_LAUNCHD_LABEL:-BriefTube.prod}"
LEGACY_LABELS=("com.brieftube.server" "local.brieftube.prod")
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
APP_CONFIG_FILE_PATH="${APP_CONFIG_FILE:-${ROOT_DIR}/config.prod.yaml}"
PATH_VALUE="${PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}"
LAUNCHER_PATH="${ROOT_DIR}/scripts/launchd/BriefTube"
DRY_RUN="${BRIEFTUBE_LAUNCHD_DRY_RUN:-0}"

read_config_value() {
  local key="$1"
  local default_value="$2"
  if [[ ! -f "${APP_CONFIG_FILE_PATH}" ]]; then
    printf "%s\n" "${default_value}"
    return
  fi
  local value
  value="$(awk -F: -v key="${key}" '
    $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
      sub(/^[[:space:]]*/, "", $2)
      sub(/[[:space:]]*(#.*)?$/, "", $2)
      gsub(/^["'\''"]|["'\''"]$/, "", $2)
      print $2
      exit
    }
  ' "${APP_CONFIG_FILE_PATH}")"
  printf "%s\n" "${value:-${default_value}}"
}

resolve_path() {
  local path="$1"
  if [[ "${path}" == /* ]]; then
    printf "%s\n" "${path}"
    return
  fi
  printf "%s\n" "${ROOT_DIR}/${path#./}"
}

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  printf "%s\n" "${value}"
}

SERVER_HOST_VALUE="$(read_config_value "server_host" "0.0.0.0")"
SERVER_PORT_VALUE="$(read_config_value "server_port" "48080")"
APP_LOG_DIR_VALUE="$(read_config_value "log_dir" "./logs/prod")"
APP_LOG_FILE_NAME_VALUE="$(read_config_value "log_file_name" "brieftube-prod.log")"
APP_LOG_DIR="$(resolve_path "${APP_LOG_DIR_VALUE}")"
APP_LOG_PATH="${APP_LOG_DIR}/${APP_LOG_FILE_NAME_VALUE}"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF
LaunchAgent dry run:
  action: install
  label: ${LABEL}
  plist: ${PLIST_PATH}
  launcher: ${LAUNCHER_PATH}
  working_directory: ${ROOT_DIR}
  app_config_file: ${APP_CONFIG_FILE_PATH}
  server_host: ${SERVER_HOST_VALUE}
  server_port: ${SERVER_PORT_VALUE}
  log: ${APP_LOG_PATH}
  log_to_file_env: false
  service_target: gui/$(id -u)/${LABEL}
  legacy_labels: ${LEGACY_LABELS[*]}
  would_create_dirs:
    ${PLIST_DIR}
    ${APP_LOG_DIR}
  would_bootstrap: launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"
  would_kickstart: launchctl kickstart -k "gui/$(id -u)/${LABEL}"
EOF
  exit 0
fi

mkdir -p "${PLIST_DIR}" "${APP_LOG_DIR}"

LABEL_XML="$(xml_escape "${LABEL}")"
LAUNCHER_PATH_XML="$(xml_escape "${LAUNCHER_PATH}")"
ROOT_DIR_XML="$(xml_escape "${ROOT_DIR}")"
APP_CONFIG_FILE_PATH_XML="$(xml_escape "${APP_CONFIG_FILE_PATH}")"
HOME_XML="$(xml_escape "${HOME}")"
PATH_VALUE_XML="$(xml_escape "${PATH_VALUE}")"

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
    <string>${LABEL_XML}</string>

    <key>ProgramArguments</key>
    <array>
      <string>${LAUNCHER_PATH_XML}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${ROOT_DIR_XML}</string>

    <key>EnvironmentVariables</key>
    <dict>
      <key>APP_CONFIG_FILE</key>
      <string>${APP_CONFIG_FILE_PATH_XML}</string>
      <key>HOME</key>
      <string>${HOME_XML}</string>
      <key>PATH</key>
      <string>${PATH_VALUE_XML}</string>
      <key>LOG_TO_FILE</key>
      <string>false</string>
      <key>LOG_CONSOLE_COLOR</key>
      <string>NEVER</string>
    </dict>

    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${APP_LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>${APP_LOG_PATH}</string>
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
  app: http://${SERVER_HOST_VALUE}:${SERVER_PORT_VALUE}

Status:
  launchctl print gui/$(id -u)/${LABEL}
Logs:
  tail -f ${APP_LOG_PATH}
EOF
