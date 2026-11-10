"""Small, dependency-free YouTube Data API client for personal list sync.

OAuth credentials live outside the repository and are deliberately loaded only
when a sync is requested.  Rendering To Watch or To Listen never contacts
Google and never reads the refresh token.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/youtube/v3"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_WRITE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_SCOPE = YOUTUBE_READ_SCOPE  # Backward-compatible name used by existing callers/tests.


class YouTubeError(RuntimeError):
    """A safe-to-display YouTube configuration or API failure."""


@dataclass(frozen=True)
class Settings:
    oauth_file: Path
    name: str = "YouTube"
    watch_playlist_id: str = ""
    listen_playlist_id: str = ""
    watch_category: str = "YouTube"
    listen_category: str = "YouTube"

    def playlist_id(self, key: str) -> str:
        if key == "watch":
            return self.watch_playlist_id
        if key == "listen":
            return self.listen_playlist_id
        raise YouTubeError("YouTube sync is available only for To Watch and To Listen")

    def category(self, key: str) -> str:
        if key == "watch":
            return self.watch_category
        if key == "listen":
            return self.listen_category
        raise YouTubeError("YouTube sync is available only for To Watch and To Listen")

    def ready(self, key: str) -> bool:
        return self.oauth_file.is_file() and bool(self.playlist_id(key))


def settings_from_env(env: dict[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    oauth_file = values.get(
        "CLOCKWORK_YOUTUBE_OAUTH_FILE", "~/.config/clockwork/youtube-oauth.json"
    )
    return Settings(
        oauth_file=Path(oauth_file).expanduser(),
        name=values.get("CLOCKWORK_YOUTUBE_ACCOUNT_NAME", "YouTube").strip() or "YouTube",
        watch_playlist_id=values.get("CLOCKWORK_YOUTUBE_WATCH_PLAYLIST_ID", "").strip(),
        listen_playlist_id=values.get("CLOCKWORK_YOUTUBE_LISTEN_PLAYLIST_ID", "").strip(),
        watch_category=values.get("CLOCKWORK_YOUTUBE_WATCH_CATEGORY", "YouTube").strip()
        or "YouTube",
        listen_category=values.get("CLOCKWORK_YOUTUBE_LISTEN_CATEGORY", "YouTube").strip()
        or "YouTube",
    )


def _owner_json(path: Path, *, label: str) -> dict[str, Any]:
    """Read an owner-only regular JSON object without following a symlink."""

    try:
        if path.is_symlink():
            raise YouTubeError(f"{label} must not be a symbolic link")
        file_stat = path.stat()
    except FileNotFoundError as exc:
        raise YouTubeError(f"{label} is missing: {path}") from exc
    except OSError as exc:
        raise YouTubeError(f"{label} cannot be inspected: {exc}") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise YouTubeError(f"{label} must be a regular file")
    if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
        raise YouTubeError(f"{label} must be owned by the Clockwork user")
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise YouTubeError(f"{label} permissions must be 0600 or stricter")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YouTubeError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise YouTubeError(f"{label} must contain a JSON object")
    return payload


def settings_list_from_env(env: dict[str, str] | None = None) -> list[Settings]:
    """Load the multi-account registry when present, else the legacy single account."""

    values = os.environ if env is None else env
    registry = Path(
        values.get(
            "CLOCKWORK_YOUTUBE_ACCOUNTS_FILE",
            "~/.config/clockwork/youtube-accounts.json",
        )
    ).expanduser()
    if not registry.exists():
        return [settings_from_env(values)]
    payload = _owner_json(registry, label="YouTube accounts file")
    rows = payload.get("accounts")
    if not isinstance(rows, list) or not rows:
        raise YouTubeError("YouTube accounts file must contain a non-empty accounts list")
    accounts: list[Settings] = []
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise YouTubeError("Each YouTube account must be a JSON object")
        name = str(row.get("name") or "").strip()
        oauth_value = str(row.get("oauth_file") or "").strip()
        if not name or not oauth_value:
            raise YouTubeError("Each YouTube account requires name and oauth_file")
        if name.casefold() in names:
            raise YouTubeError(f"Duplicate YouTube account name: {name}")
        names.add(name.casefold())
        oauth_file = Path(oauth_value).expanduser()
        if not oauth_file.is_absolute():
            oauth_file = registry.parent / oauth_file
        accounts.append(
            Settings(
                oauth_file=oauth_file,
                name=name,
                watch_playlist_id=str(row.get("watch_playlist_id") or "").strip(),
                listen_playlist_id=str(row.get("listen_playlist_id") or "").strip(),
                watch_category=str(row.get("watch_category") or "YouTube").strip() or "YouTube",
                listen_category=str(row.get("listen_category") or "YouTube").strip() or "YouTube",
            )
        )
    return accounts


def accounts_file_from_env(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return Path(
        values.get(
            "CLOCKWORK_YOUTUBE_ACCOUNTS_FILE",
            "~/.config/clockwork/youtube-accounts.json",
        )
    ).expanduser()


def playlist_links(accounts: list[Settings], key: str) -> list[dict[str, str]]:
    """Return browser links to each account's configured playlist for one list.

    Playlist IDs are not secrets, but they are account-specific, so these links
    are meant for the rendered page only and stay out of ``public_status``.
    """

    links: list[dict[str, str]] = []
    for settings in accounts:
        playlist_id = settings.playlist_id(key)
        if playlist_id:
            query = urllib.parse.urlencode({"list": playlist_id})
            links.append(
                {"account": settings.name, "url": f"https://www.youtube.com/playlist?{query}"}
            )
    return links


def public_status(accounts: Settings | list[Settings]) -> dict[str, Any]:
    """Return aggregate configuration status without opening credential files."""

    rows = [accounts] if isinstance(accounts, Settings) else accounts
    account_status = [
        {
            "name": settings.name,
            "credential_file_present": settings.oauth_file.is_file(),
            "watch_playlist_configured": bool(settings.watch_playlist_id),
            "listen_playlist_configured": bool(settings.listen_playlist_id),
        }
        for settings in rows
    ]
    return {
        "account_count": len(rows),
        "credential_file_present": any(row["credential_file_present"] for row in account_status),
        "watch": {
            "configured": any(settings.ready("watch") for settings in rows),
            "configured_accounts": sum(settings.ready("watch") for settings in rows),
            "playlist_configured": any(settings.watch_playlist_id for settings in rows),
        },
        "listen": {
            "configured": any(settings.ready("listen") for settings in rows),
            "configured_accounts": sum(settings.ready("listen") for settings in rows),
            "playlist_configured": any(settings.listen_playlist_id for settings in rows),
        },
        "accounts": account_status,
    }


def load_credentials(path: Path) -> dict[str, str]:
    """Load an owner-only OAuth file, refusing links and permissive modes."""

    payload = _owner_json(path, label="YouTube OAuth file")
    required = ("client_id", "client_secret", "refresh_token")
    credentials = {name: str(payload.get(name) or "").strip() for name in required}
    if any(not credentials[name] for name in required):
        raise YouTubeError(
            "YouTube OAuth file must contain client_id, client_secret, and refresh_token"
        )
    scopes = payload.get("scopes", payload.get("scope", []))
    if isinstance(scopes, str):
        scopes = scopes.split()
    accepted = {YOUTUBE_READ_SCOPE, YOUTUBE_WRITE_SCOPE}
    if not isinstance(scopes, list) or not accepted.intersection(scopes):
        raise YouTubeError("YouTube OAuth grant does not include the required YouTube scope")
    return credentials


def can_create_playlists(path: Path) -> bool:
    payload = _owner_json(path, label="YouTube OAuth file")
    scopes = payload.get("scopes", payload.get("scope", []))
    if isinstance(scopes, str):
        scopes = scopes.split()
    return isinstance(scopes, list) and YOUTUBE_WRITE_SCOPE in scopes


def video_id_from_url(value: str) -> str:
    """Return a video ID from a supported YouTube URL or raise a safe error."""

    text = str(value or "").strip()
    if not text:
        raise YouTubeError("YouTube URL cannot be empty")
    parsed = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").casefold().rstrip(".")
    video_id = ""
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path.rstrip("/") == "/watch":
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) >= 2 and parts[0] in {"embed", "live", "shorts"}:
            video_id = parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", video_id):
        raise YouTubeError("Enter a valid youtube.com or youtu.be video URL")
    return video_id


def video_url(video_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", str(video_id or "")):
        raise YouTubeError("YouTube video ID is invalid")
    return f"https://www.youtube.com/watch?v={video_id}"


def _json_request(request: urllib.request.Request, *, error_context: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = str(body.get("error", {}).get("message") or "").strip()
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        suffix = f": {detail}" if detail else ""
        raise YouTubeError(f"{error_context} failed with HTTP {exc.code}{suffix}") from exc
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YouTubeError(f"{error_context} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise YouTubeError(f"{error_context} returned an unexpected response")
    return payload


def access_token(credentials: dict[str, str]) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "refresh_token": credentials["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    payload = _json_request(request, error_context="YouTube token refresh")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise YouTubeError("YouTube token refresh returned no access token")
    return token


def _api_get(resource: str, token: str, **params: str | int) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{API_URL}/{resource}?{query}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    return _json_request(request, error_context=f"YouTube {resource}")


def _api_post(
    resource: str,
    token: str,
    payload: dict[str, Any],
    **params: str | int,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{API_URL}/{resource}?{query}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _json_request(request, error_context=f"YouTube {resource}")


def owned_playlists(settings: Settings) -> list[dict[str, str]]:
    credentials = load_credentials(settings.oauth_file)
    token = access_token(credentials)
    playlists: list[dict[str, str]] = []
    page_token = ""
    while True:
        params: dict[str, str | int] = {
            "part": "snippet,status",
            "mine": "true",
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _api_get("playlists", token, **params)
        for raw in payload.get("items", []):
            if not isinstance(raw, dict):
                continue
            snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
            status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
            playlist_id = str(raw.get("id") or "").strip()
            title = str(snippet.get("title") or "").strip()
            if playlist_id and title:
                playlists.append(
                    {
                        "id": playlist_id,
                        "title": title,
                        "privacy": str(status.get("privacyStatus") or "").strip(),
                    }
                )
        page_token = str(payload.get("nextPageToken") or "").strip()
        if not page_token:
            return playlists


def ensure_private_playlist(settings: Settings, title: str) -> tuple[str, bool]:
    """Return ``(playlist_id, created)`` for an exact private playlist title."""

    if not can_create_playlists(settings.oauth_file):
        raise YouTubeError(
            f"{settings.name} must be reauthorized with playlist creation permission"
        )
    credentials = load_credentials(settings.oauth_file)
    token = access_token(credentials)
    channel = _api_get("channels", token, part="id", mine="true")
    if not channel.get("items"):
        raise YouTubeError(f"{settings.name} has no YouTube channel")
    matches = [
        playlist
        for playlist in owned_playlists(settings)
        if playlist["title"].casefold() == title.casefold()
    ]
    private = next((playlist for playlist in matches if playlist["privacy"] == "private"), None)
    if private:
        return private["id"], False
    if matches:
        raise YouTubeError(
            f'{settings.name} already has a non-private playlist named "{title}"; '
            "make it private before linking it"
        )
    created = _api_post(
        "playlists",
        token,
        {
            "snippet": {
                "title": title,
                "description": "Private Clockwork queue.",
            },
            "status": {"privacyStatus": "private"},
        },
        part="snippet,status",
    )
    playlist_id = str(created.get("id") or "").strip()
    if not playlist_id:
        raise YouTubeError(f'YouTube created no usable "{title}" playlist for {settings.name}')
    return playlist_id, True


def add_video_to_playlist(settings: Settings, key: str, video_id: str) -> str:
    """Add one explicitly identified video and return its playlist-item ID."""

    playlist_id = settings.playlist_id(key)
    if not playlist_id:
        raise YouTubeError(f"No YouTube playlist is configured for To {key.title()}")
    if not can_create_playlists(settings.oauth_file):
        raise YouTubeError(f"{settings.name} must be reauthorized with playlist edit permission")
    video_url(video_id)  # Validate before contacting Google.
    token = access_token(load_credentials(settings.oauth_file))
    created = _api_post(
        "playlistItems",
        token,
        {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
        part="snippet",
    )
    playlist_item_id = str(created.get("id") or "").strip()
    if not playlist_item_id:
        raise YouTubeError(f"YouTube added no usable playlist item for {settings.name}")
    return playlist_item_id


def _write_owner_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def set_playlist_id(account_name: str, key: str, playlist_id: str) -> None:
    if key not in {"watch", "listen"}:
        raise YouTubeError("playlist key must be watch or listen")
    registry = accounts_file_from_env()
    payload = _owner_json(registry, label="YouTube accounts file")
    rows = payload.get("accounts")
    if not isinstance(rows, list):
        raise YouTubeError("YouTube accounts file must contain an accounts list")
    match = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("name") or "").casefold() == account_name.casefold()
        ),
        None,
    )
    if match is None:
        raise YouTubeError(f"YouTube account is missing from the registry: {account_name}")
    match[f"{key}_playlist_id"] = playlist_id
    _write_owner_json(registry, payload)


def playlist_videos(settings: Settings, key: str) -> list[dict[str, str]]:
    """Return every usable video in the configured playlist, in playlist order."""

    playlist_id = settings.playlist_id(key)
    if not playlist_id:
        raise YouTubeError(f"No YouTube playlist is configured for To {key.title()}")
    credentials = load_credentials(settings.oauth_file)
    token = access_token(credentials)
    videos: list[dict[str, str]] = []
    page_token = ""
    while True:
        params: dict[str, str | int] = {
            "part": "snippet,contentDetails,status",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _api_get("playlistItems", token, **params)
        for raw in payload.get("items", []):
            if not isinstance(raw, dict):
                continue
            snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
            details = (
                raw.get("contentDetails") if isinstance(raw.get("contentDetails"), dict) else {}
            )
            resource = (
                snippet.get("resourceId") if isinstance(snippet.get("resourceId"), dict) else {}
            )
            video_id = str(details.get("videoId") or resource.get("videoId") or "").strip()
            title = str(snippet.get("title") or "").strip()
            if not video_id or not title or title in {"Deleted video", "Private video"}:
                continue
            videos.append(
                {
                    "video_id": video_id,
                    "playlist_item_id": str(raw.get("id") or "").strip(),
                    "title": title,
                    "channel": str(snippet.get("videoOwnerChannelTitle") or "").strip(),
                    "account": settings.name,
                    "url": video_url(video_id),
                }
            )
        page_token = str(payload.get("nextPageToken") or "").strip()
        if not page_token:
            return videos
