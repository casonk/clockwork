"""Small, dependency-free Spotify Web API client for the To Listen playlist sync.

Mirrors ``youtube_api``: OAuth credentials live outside the repository in an owner-only file and
are read only when a sync is requested. Rendering To Listen never contacts Spotify.

Playlist endpoints follow Spotify's February 2026 API (``/me/playlists``, ``/playlists/{id}/items``,
``item`` instead of ``track``). Only tracks and episodes can be playlist items; albums, artists,
shows and playlists can be linked on an item but are not pushed.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # owner-only JSON helpers shared with the YouTube adapter
    from web import youtube_api
except ModuleNotFoundError:  # web/app.py run as a script
    import youtube_api  # type: ignore[no-redef]

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
READ_SCOPE = "playlist-read-private"
WRITE_SCOPE = "playlist-modify-private"
KINDS = ("track", "album", "artist", "playlist", "episode", "show")
PUSHABLE_KINDS = ("track", "episode")
_ID = re.compile(r"[A-Za-z0-9]{22}")
PLAYLIST_TITLE = "To Listen"


class SpotifyError(RuntimeError):
    """A safe-to-display Spotify configuration or API failure."""


@dataclass(frozen=True)
class Settings:
    oauth_file: Path
    playlist_id: str = ""
    category: str = "Spotify"

    def ready(self) -> bool:
        return self.oauth_file.is_file() and bool(self.playlist_id)


def settings_from_env(env: dict[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    oauth_file = Path(
        values.get("CLOCKWORK_SPOTIFY_OAUTH_FILE", "~/.config/clockwork/spotify-oauth.json")
    ).expanduser()
    playlist_id = values.get("CLOCKWORK_SPOTIFY_LISTEN_PLAYLIST_ID", "").strip()
    if not playlist_id and oauth_file.is_file():
        # `ensure_playlist` stores the id it created beside the grant.
        try:
            playlist_id = str(_owner_json(oauth_file).get("listen_playlist_id") or "").strip()
        except SpotifyError:
            playlist_id = ""
    return Settings(
        oauth_file=oauth_file,
        playlist_id=playlist_id,
        category=values.get("CLOCKWORK_SPOTIFY_LISTEN_CATEGORY", "Spotify").strip() or "Spotify",
    )


def _owner_json(path: Path) -> dict[str, Any]:
    try:
        return youtube_api._owner_json(path, label="Spotify OAuth file")
    except youtube_api.YouTubeError as exc:
        raise SpotifyError(str(exc)) from exc


def public_status(settings: Settings) -> dict[str, Any]:
    """Configuration status without opening the credential file."""
    return {
        "credential_file_present": settings.oauth_file.is_file(),
        "playlist_configured": bool(settings.playlist_id),
        "configured": settings.ready(),
    }


def playlist_link(settings: Settings) -> str:
    return (
        f"https://open.spotify.com/playlist/{settings.playlist_id}"
        if _ID.fullmatch(settings.playlist_id)
        else ""
    )


def load_credentials(path: Path) -> dict[str, str]:
    """client_id and refresh_token are required; client_secret is optional (PKCE grants have none)."""
    payload = _owner_json(path)
    credentials = {
        name: str(payload.get(name) or "").strip()
        for name in ("client_id", "client_secret", "refresh_token")
    }
    if not credentials["client_id"] or not credentials["refresh_token"]:
        raise SpotifyError("Spotify OAuth file must contain client_id and refresh_token")
    scopes = payload.get("scopes", payload.get("scope", []))
    if isinstance(scopes, str):
        scopes = scopes.split()
    if not isinstance(scopes, list) or READ_SCOPE not in scopes:
        raise SpotifyError(f"Spotify OAuth grant does not include {READ_SCOPE}")
    return credentials


def can_write(path: Path) -> bool:
    scopes = _owner_json(path).get("scopes", [])
    if isinstance(scopes, str):
        scopes = scopes.split()
    return isinstance(scopes, list) and WRITE_SCOPE in scopes


# --- links -------------------------------------------------------------------------------------


def parse_link(value: str) -> dict[str, str]:
    """``spotify_kind``/``spotify_id``/``spotify_uri``/``spotify_url`` from a Spotify URL or URI."""
    text = str(value or "").strip()
    if not text:
        raise SpotifyError("Spotify link cannot be empty")
    kind = spotify_id = ""
    if text.casefold().startswith("spotify:"):
        parts = text.split(":")
        if len(parts) == 3:
            kind, spotify_id = parts[1].casefold(), parts[2]
    else:
        parsed = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
        if (parsed.hostname or "").casefold().rstrip(".") == "open.spotify.com":
            parts = [p for p in parsed.path.split("/") if p]
            if parts and parts[0].startswith("intl-"):  # open.spotify.com/intl-de/track/...
                parts = parts[1:]
            if len(parts) >= 2:
                kind, spotify_id = parts[0].casefold(), parts[1]
    if kind not in KINDS or not _ID.fullmatch(spotify_id):
        raise SpotifyError(
            "Enter a valid open.spotify.com link (track, album, artist, playlist, episode or show)"
        )
    return {
        "spotify_kind": kind,
        "spotify_id": spotify_id,
        "spotify_uri": f"spotify:{kind}:{spotify_id}",
        "spotify_url": f"https://open.spotify.com/{kind}/{spotify_id}",
    }


# --- HTTP --------------------------------------------------------------------------------------


def _request(request: urllib.request.Request, *, context: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            error = body.get("error")
            detail = str(
                error.get("message") if isinstance(error, dict) else body.get("error_description")
            ).strip()
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        hint = (
            " (Spotify may be restricting writes for this app: check the app's quota mode)"
            if exc.code == 403 and request.get_method() != "GET"
            else ""
        )
        suffix = f": {detail}" if detail and detail != "None" else ""
        raise SpotifyError(f"{context} failed with HTTP {exc.code}{suffix}{hint}") from exc
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpotifyError(f"{context} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SpotifyError(f"{context} returned an unexpected response")
    return payload


def access_token(settings: Settings) -> str:
    """Refresh an access token; a rotated refresh token is saved back to the grant file."""
    credentials = load_credentials(settings.oauth_file)
    form = {"grant_type": "refresh_token", "refresh_token": credentials["refresh_token"]}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if credentials["client_secret"]:  # confidential client
        basic = base64.b64encode(
            f"{credentials['client_id']}:{credentials['client_secret']}".encode()
        ).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"
    else:  # PKCE grant: the client ID travels in the body
        form["client_id"] = credentials["client_id"]
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(form).encode("ascii"),
        headers=headers,
        method="POST",
    )
    payload = _request(request, context="Spotify token refresh")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise SpotifyError("Spotify token refresh returned no access token")
    rotated = str(payload.get("refresh_token") or "").strip()
    if rotated and rotated != credentials["refresh_token"]:
        grant = _owner_json(settings.oauth_file)
        grant["refresh_token"] = rotated
        youtube_api._write_owner_json(settings.oauth_file, grant)
    return token


def _get(path: str, token: str, **params: str | int) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{API_URL}/{path}" + (f"?{query}" if query else ""),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    return _request(request, context=f"Spotify {path.split('/')[0]}")


def _post(path: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{API_URL}/{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _request(request, context=f"Spotify {path.split('/')[0]}")


# --- playlist ----------------------------------------------------------------------------------


def ensure_playlist(settings: Settings, title: str = PLAYLIST_TITLE) -> tuple[str, bool]:
    """``(playlist_id, created)`` for a private playlist named ``title``; the id is stored."""
    if not can_write(settings.oauth_file):
        raise SpotifyError(
            f"Spotify grant needs {WRITE_SCOPE}: rerun scripts/authorize_spotify.py --write"
        )
    token = access_token(settings)
    offset = 0
    while True:
        page = _get("me/playlists", token, limit=50, offset=offset)
        for row in page.get("items", []):
            if (
                isinstance(row, dict)
                and str(row.get("name") or "").casefold() == title.casefold()
                and row.get("id")
                and not row.get("public")
            ):
                _store_playlist_id(settings, str(row["id"]))
                return str(row["id"]), False
        if not page.get("next"):
            break
        offset += 50
    created = _post(
        "me/playlists",
        token,
        {"name": title, "public": False, "description": "Private Clockwork queue."},
    )
    playlist_id = str(created.get("id") or "").strip()
    if not playlist_id:
        raise SpotifyError("Spotify created no usable playlist")
    _store_playlist_id(settings, playlist_id)
    return playlist_id, True


def _store_playlist_id(settings: Settings, playlist_id: str) -> None:
    grant = _owner_json(settings.oauth_file)
    grant["listen_playlist_id"] = playlist_id
    youtube_api._write_owner_json(settings.oauth_file, grant)


def playlist_items(settings: Settings) -> list[dict[str, str]]:
    """Every track/episode in the configured playlist, in playlist order."""
    if not settings.playlist_id:
        raise SpotifyError("No Spotify playlist is configured for To Listen")
    token = access_token(settings)
    out: list[dict[str, str]] = []
    offset = 0
    while True:
        page = _get(f"playlists/{settings.playlist_id}/items", token, limit=50, offset=offset)
        for entry in page.get("items", []):
            if not isinstance(entry, dict):
                continue
            item = entry.get("item") or entry.get("track")  # `track` before the 2026 rename
            if not isinstance(item, dict) or not item.get("id") or item.get("is_local"):
                continue
            kind = str(item.get("type") or "track")
            if kind not in PUSHABLE_KINDS:
                continue
            by = (
                (item.get("show") or {}).get("name")
                if kind == "episode"
                else ", ".join(
                    str(a.get("name")) for a in item.get("artists", []) if isinstance(a, dict)
                )
            )
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "spotify_kind": kind,
                    "spotify_id": str(item["id"]),
                    "spotify_uri": f"spotify:{kind}:{item['id']}",
                    "spotify_url": f"https://open.spotify.com/{kind}/{item['id']}",
                    "spotify_artist": str(by or "").strip(),
                    "title": f"{by} – {name}" if by else name,
                }
            )
        if not page.get("next"):
            return out
        offset += 50


def create_playlist(settings: Settings, title: str, description: str = "") -> str:
    """Always create a new private playlist and return its id (``ensure_playlist`` reuses one)."""
    if not can_write(settings.oauth_file):
        raise SpotifyError(f"Spotify grant needs {WRITE_SCOPE}: rerun authorize_spotify.py --write")
    created = _post(
        "me/playlists",
        access_token(settings),
        {"name": title[:100], "public": False, "description": description[:300]},
    )
    playlist_id = str(created.get("id") or "").strip()
    if not playlist_id:
        raise SpotifyError("Spotify created no usable playlist")
    return playlist_id


def add_items(settings: Settings, uris: list[str], playlist_id: str = "") -> int:
    """Append track/episode URIs (100 per request) to a playlist; returns how many were sent."""
    if not uris:
        return 0
    playlist_id = playlist_id or settings.playlist_id
    if not _ID.fullmatch(playlist_id):
        raise SpotifyError("Spotify playlist id is invalid")
    if not can_write(settings.oauth_file):
        raise SpotifyError(f"Spotify grant needs {WRITE_SCOPE}: rerun authorize_spotify.py --write")
    token = access_token(settings)
    for start in range(0, len(uris), 100):
        _post(f"playlists/{playlist_id}/items", token, {"uris": uris[start : start + 100]})
    return len(uris)


def user_playlists(settings: Settings) -> list[dict[str, str]]:
    """Every playlist the account owns or follows: id, title, track count and link."""
    token = access_token(settings)
    out: list[dict[str, str]] = []
    offset = 0
    while True:
        page = _get("me/playlists", token, limit=50, offset=offset)
        for row in page.get("items", []):
            if not isinstance(row, dict) or not row.get("id"):
                continue
            counts = row.get("items") or row.get("tracks") or {}  # `tracks` before the 2026 rename
            owner = row.get("owner") if isinstance(row.get("owner"), dict) else {}
            out.append(
                {
                    "id": str(row["id"]),
                    "title": str(row.get("name") or "Untitled playlist"),
                    "count": str(counts.get("total", "")) if isinstance(counts, dict) else "",
                    "url": f"https://open.spotify.com/playlist/{row['id']}",
                    "owner": str(owner.get("display_name") or ""),
                    "private": "" if row.get("public") else "private",
                }
            )
        if not page.get("next"):
            return out
        offset += 50
