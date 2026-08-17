#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
PYTHON_BOOTSTRAP="${CLOCKWORK_BOOTSTRAP_PYTHON:-$(command -v python3 || true)}"
VENV_DIR="${CLOCKWORK_WEB_VENV:-$REPO_ROOT/.venv}"
ENV_FILE="${CLOCKWORK_WEB_ENV_FILE:-$HOME/.config/clockwork/clockwork-web.env}"

if [[ -z "$PYTHON_BOOTSTRAP" || ! -x "$PYTHON_BOOTSTRAP" ]]; then
  echo "clockwork-web bootstrap: Python 3.10 or newer is required" >&2
  exit 69
fi

if ! "$PYTHON_BOOTSTRAP" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "clockwork-web bootstrap: $PYTHON_BOOTSTRAP is older than Python 3.10" >&2
  echo "clockwork-web bootstrap: set CLOCKWORK_BOOTSTRAP_PYTHON to a newer interpreter" >&2
  exit 69
fi

"$PYTHON_BOOTSTRAP" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$REPO_ROOT[web]"

if [[ ! -e "$ENV_FILE" ]]; then
  umask 077
  mkdir -p "$(dirname -- "$ENV_FILE")"
  SECRET="$($VENV_DIR/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  printf 'CLOCKWORK_WEB_SECRET=%s\n' "$SECRET" >"$ENV_FILE"
fi

if [[ -L "$ENV_FILE" || ! -f "$ENV_FILE" ]]; then
  echo "clockwork-web bootstrap: refusing non-regular or symlink env file: $ENV_FILE" >&2
  exit 78
fi
chmod 600 "$ENV_FILE"

"$VENV_DIR/bin/python" -c 'import flask, tomlkit'
echo "clockwork-web bootstrap complete"
echo "  python: $VENV_DIR/bin/python"
echo "  secret: $ENV_FILE (owner-only; value not printed)"
