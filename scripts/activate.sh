#!/usr/bin/env bash
# Apply all privileged installs declared in config/activate.conf.
# Usage: sudo ./scripts/activate.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$ROOT_DIR/config/activate.conf"

[[ $EUID -eq 0 ]] || { echo "error: run as root — sudo $0" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "error: missing $CONFIG" >&2; exit 1; }

while IFS= read -r raw || [[ -n "$raw" ]]; do
  line="${raw%%#*}"
  [[ -z "${line// }" ]] && continue
  read -ra fields <<< "$line"
  [[ ${#fields[@]} -lt 2 ]] && continue

  type="${fields[0]}"
  case "$type" in
    script)
      src="$ROOT_DIR/${fields[1]}"
      dest="${fields[2]}"
      mode="${fields[3]:-755}"
      install -m "$mode" "$src" "$dest"
      echo "[ok] script  $dest"
      ;;
    sudoers)
      src="$ROOT_DIR/${fields[1]}"
      dest="${fields[2]}"
      install -m 440 "$src" "$dest"
      visudo -c -q
      echo "[ok] sudoers $dest"
      ;;
    service)
      svc="${fields[1]}"
      systemctl restart "$svc"
      echo "[ok] service $svc restarted"
      ;;
    *)
      echo "warn: unknown type '${type}' — skipped" >&2
      ;;
  esac
done < "$CONFIG"

echo "done."
