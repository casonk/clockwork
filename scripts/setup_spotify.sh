#!/usr/bin/env bash
# One-shot Spotify setup for Clockwork's To Listen page: authorize, create the playlist,
# restart the web service and verify.        Run with no arguments:   scripts/setup_spotify.sh
#
# By default it reuses the Spotify app tape-deck already uses (client ID and loopback redirect
# URI read from ~/.config/tape-deck/config.toml), so nothing needs registering or typing: the
# PKCE login needs no client secret. To use a different app:
#   scripts/setup_spotify.sh CLIENT_ID [CLIENT_SECRET]    (or SPOTIFY_CLIENT_ID / _SECRET)
# and register  http://127.0.0.1:8765/callback  on it first (SPOTIFY_CALLBACK_PORT changes the port).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
grant="${CLOCKWORK_SPOTIFY_OAUTH_FILE:-$HOME/.config/clockwork/spotify-oauth.json}"
tape_config="${TAPE_DECK_CONFIG:-$HOME/.config/tape-deck/config.toml}"
client_id="${1:-${SPOTIFY_CLIENT_ID:-}}"
export SPOTIFY_CLIENT_SECRET="${2:-${SPOTIFY_CLIENT_SECRET:-}}"
port="${SPOTIFY_CALLBACK_PORT:-8765}"

if [[ -z "$client_id" && -f "$tape_config" ]]; then
  # Reuse tape-deck's app: its client ID, and the port of the redirect URI it registered.
  read -r client_id port < <(python3 - "$tape_config" <<'PY'
import sys, tomllib, urllib.parse
cfg = tomllib.load(open(sys.argv[1], "rb"))
parts = urllib.parse.urlsplit(str(cfg.get("redirect_uri") or ""))
ok = parts.hostname == "127.0.0.1" and parts.path == "/callback" and parts.port
print(str(cfg.get("client_id") or ""), parts.port if ok else 8765)
PY
)
  echo "Reusing tape-deck's Spotify app (redirect http://127.0.0.1:${port}/callback)."
fi
if [[ -z "$client_id" ]]; then
  echo "No Spotify client ID found. Run: scripts/setup_spotify.sh CLIENT_ID [CLIENT_SECRET]" >&2
  echo "(register http://127.0.0.1:${port}/callback on that app first)" >&2
  exit 1
fi
export SPOTIFY_CLIENT_ID="$client_id"

echo "1/3 Authorizing: approve access in the browser window that opens..."
python3 "$root/scripts/authorize_spotify.py" --write --port "$port" --output "$grant" \
  ${SPOTIFY_NO_BROWSER:+--no-browser}

echo "2/3 Creating (or adopting) the private To Listen playlist..."
CLOCKWORK_SPOTIFY_OAUTH_FILE="$grant" python3 - "$root" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from web import spotify_api
playlist_id, created = spotify_api.ensure_playlist(spotify_api.settings_from_env())
print(("Created" if created else "Adopted existing") + " playlist " + playlist_id)
PY

echo "3/3 Restarting the web service..."
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
systemctl --user restart clockwork-web.service
sleep 3
systemctl --user is-active clockwork-web.service
echo "Done. Open To Listen and press 'Sync Spotify' (or browse 'Playlists')."
