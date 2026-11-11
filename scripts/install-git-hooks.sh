#!/usr/bin/env bash
# Install the git hooks that run this repo's checks before a commit and before a push.
# Run once per clone (hooks are not part of a checkout). Safe to run again.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v pre-commit >/dev/null 2>&1; then
  echo "pre-commit is not installed. Install it first: pipx install pre-commit (or pip install --user pre-commit)" >&2
  exit 1
fi

pre-commit install --hook-type pre-commit --hook-type pre-push
echo "Installed: pre-commit (style, secrets) and pre-push (style again, then pytest)."
echo "Hook environments are built the first time they run."
