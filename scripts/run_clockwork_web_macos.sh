#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
PYTHON_BIN="${CLOCKWORK_WEB_PYTHON:-$REPO_ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "clockwork-web: missing executable Python environment: $PYTHON_BIN" >&2
  echo "clockwork-web: run scripts/bootstrap_clockwork_web_macos.sh first" >&2
  exit 78
fi

if [[ -z "${CLOCKWORK_WEB_SECRET:-}" || ${#CLOCKWORK_WEB_SECRET} -lt 32 ]]; then
  echo "clockwork-web: CLOCKWORK_WEB_SECRET must be loaded from the owner-only local env file" >&2
  exit 78
fi

if [[ "${CLOCKWORK_WEB_HOST:-127.0.0.1}" != "127.0.0.1" && "${CLOCKWORK_WEB_HOST:-}" != "::1" ]]; then
  echo "clockwork-web: macOS LaunchAgent profile requires a loopback CLOCKWORK_WEB_HOST" >&2
  exit 78
fi

if ! "$PYTHON_BIN" -c 'import flask, tomlkit' >/dev/null 2>&1; then
  echo "clockwork-web: Flask/tomlkit dependencies are missing from $PYTHON_BIN" >&2
  echo "clockwork-web: run scripts/bootstrap_clockwork_web_macos.sh first" >&2
  exit 78
fi

exec "$PYTHON_BIN" "$REPO_ROOT/web/app.py"
