#!/usr/bin/env bash
# Build app/static/css/tailwind.css with the pinned Tailwind v3 standalone CLI.
#   ./scripts/build-tailwind.sh          rebuild the committed CSS
#   ./scripts/build-tailwind.sh --check  fail if the committed CSS is stale
set -euo pipefail
export BROWSERSLIST_IGNORE_OLD_DATA=1

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="3.4.17"
CACHE_DIR="${ROOT_DIR}/.cache/tailwindcss/${VERSION}"
INPUT="${ROOT_DIR}/app/static/css/tailwind.input.css"
OUTPUT="${ROOT_DIR}/app/static/css/tailwind.css"
CONFIG="${ROOT_DIR}/tailwind.config.js"

mode="build"
case "${1:-}" in
  "") ;;
  --check) mode="check" ;;
  *)
    echo "usage: $0 [--check]" >&2
    exit 2
    ;;
esac

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) asset="tailwindcss-macos-arm64" sha="a1d0c7985759accca0bf12e51ac1dcbf0f6cf2fffb62e6e0f62d091c477a10a3" ;;
  Darwin-x86_64) asset="tailwindcss-macos-x64" sha="6cbdad74be776c087ffa5e9a057512c54898f9fe8828d3362212dfe32fc933a3" ;;
  Linux-x86_64) asset="tailwindcss-linux-x64" sha="7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4" ;;
  Linux-aarch64 | Linux-arm64) asset="tailwindcss-linux-arm64" sha="69b1378b8133192d7d2feb12a116fa12d035594f58db3eff215879e4ad8cf39b" ;;
  *)
    echo "unsupported platform: $(uname -s)-$(uname -m)" >&2
    exit 1
    ;;
esac

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

bin="${CACHE_DIR}/${asset}"
if [[ ! -x "${bin}" ]] || [[ "$(sha256_of "${bin}")" != "${sha}" ]]; then
  mkdir -p "${CACHE_DIR}"
  tmp_bin="$(mktemp "${CACHE_DIR}/${asset}.XXXXXX")"
  trap 'rm -f "${tmp_bin}"' EXIT
  curl -fsSL --retry 3 -o "${tmp_bin}" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/v${VERSION}/${asset}"
  actual="$(sha256_of "${tmp_bin}")"
  if [[ "${actual}" != "${sha}" ]]; then
    echo "checksum mismatch for ${asset}: expected ${sha}, got ${actual}" >&2
    exit 1
  fi
  chmod +x "${tmp_bin}"
  mv "${tmp_bin}" "${bin}"
  trap - EXIT
fi

cd "${ROOT_DIR}"
if [[ "${mode}" == "check" ]]; then
  built="$(mktemp)"
  trap 'rm -f "${built}"' EXIT
  "${bin}" -c "${CONFIG}" -i "${INPUT}" -o "${built}" --minify
  if ! cmp -s "${built}" "${OUTPUT}"; then
    echo "app/static/css/tailwind.css is stale: run ./scripts/build-tailwind.sh and commit it" >&2
    exit 1
  fi
  echo "app/static/css/tailwind.css is up to date"
else
  "${bin}" -c "${CONFIG}" -i "${INPUT}" -o "${OUTPUT}" --minify
fi
