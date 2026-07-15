#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${BRIEFTUBE_LAUNCHD_LABEL:-BriefTube.prod}"
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
APP_CONFIG_FILE_PATH="${APP_CONFIG_FILE:-${ROOT_DIR}/config.prod.yaml}"
SERVICE_TARGET="gui/$(id -u)/${LABEL}"
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

SERVER_HOST_VALUE="$(read_config_value "server_host" "127.0.0.1")"
SERVER_PORT_VALUE="$(read_config_value "server_port" "48080")"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF
LaunchAgent dry run:
  action: restart
  label: ${LABEL}
  plist: ${PLIST_PATH}
  service_target: ${SERVICE_TARGET}
  app_config_file: ${APP_CONFIG_FILE_PATH}
  server_host: ${SERVER_HOST_VALUE}
  server_port: ${SERVER_PORT_VALUE}
  would_enable: launchctl enable "${SERVICE_TARGET}"
  would_kickstart: launchctl kickstart -k "${SERVICE_TARGET}"
EOF
  exit 0
fi

if [[ ! -f "${PLIST_PATH}" ]]; then
  cat <<EOF
LaunchAgent plist not found:
  ${PLIST_PATH}

Install it first:
  ${ROOT_DIR}/scripts/install-launchd-prod.sh
EOF
  exit 1
fi

launchctl enable "${SERVICE_TARGET}" >/dev/null 2>&1 || true
launchctl kickstart -k "${SERVICE_TARGET}"

sleep 1

cat <<EOF
Restarted LaunchAgent:
  label: ${LABEL}
  app: http://${SERVER_HOST_VALUE}:${SERVER_PORT_VALUE}

Status:
  launchctl print ${SERVICE_TARGET}
EOF
