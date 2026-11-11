#!/usr/bin/env python3
"""Authorize Clockwork to use a Spotify account's private playlists.

Spotify's authorization-code flow is completed locally on a loopback redirect
(register ``http://127.0.0.1:8765/callback`` on the Spotify app, or pass --port).
The resulting refresh token is written to an owner-only file and is never printed.

    python3 scripts/authorize_spotify.py --client-id ID --client-secret SECRET [--write]
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
READ_SCOPE = "playlist-read-private"
WRITE_SCOPE = "playlist-modify-private"


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    finished = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        type(self).result = {key: values[0] for key, values in query.items() if values}
        body = b"Spotify authorization received. You can close this tab and return to Clockwork."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).finished.set()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def _exchange(data: dict[str, str], client_id: str, client_secret: str) -> dict:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(data).encode("ascii"),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"token exchange failed with HTTP {exc.code}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"token exchange failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("token exchange returned an unexpected response")
    return payload


def _write_owner_only(path: Path, payload: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def authorize(
    client_id: str,
    client_secret: str,
    output: Path,
    *,
    port: int = 8765,
    open_browser: bool = True,
    write: bool = False,
) -> None:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    state = secrets.token_urlsafe(32)
    CallbackHandler.result = {}
    CallbackHandler.finished.clear()
    scopes = [READ_SCOPE] + ([WRITE_SCOPE] if write else [])
    with ReusableTCPServer(("127.0.0.1", port), CallbackHandler) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
        query = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "show_dialog": "true",
                "state": state,
                "code_challenge": challenge.decode("ascii"),
                "code_challenge_method": "S256",
            }
        )
        url = f"{AUTH_URL}?{query}"
        print(f"Redirect URI to register on the Spotify app: {redirect_uri}")
        print("Open this URL in a browser on this machine:\n")
        print(url)
        if open_browser:
            webbrowser.open(url)
        while not CallbackHandler.finished.is_set():
            server.handle_request()

    result = CallbackHandler.result
    if result.get("state") != state:
        raise RuntimeError("authorization callback state did not match")
    if result.get("error"):
        raise RuntimeError(f"authorization was refused: {result['error']}")
    code = result.get("code", "")
    if not code:
        raise RuntimeError("authorization callback did not include a code")
    tokens = _exchange(
        {
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        },
        client_id,
        client_secret,
    )
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        raise RuntimeError("Spotify returned no refresh token")
    existing: dict = {}
    if output.is_file():
        with contextlib.suppress(OSError, ValueError):
            existing = json.loads(output.read_text(encoding="utf-8"))  # keep listen_playlist_id
    _write_owner_only(
        output,
        {
            **(existing if isinstance(existing, dict) else {}),
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "scopes": scopes,
            "token_uri": TOKEN_URL,
        },
    )
    print(f"Saved owner-only Spotify authorization to {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", default=os.environ.get("SPOTIFY_CLIENT_ID", ""))
    parser.add_argument(
        "--client-secret",
        default=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
        help="or set SPOTIFY_CLIENT_SECRET (keeps it out of shell history)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("~/.config/clockwork/spotify-oauth.json").expanduser(),
    )
    parser.add_argument("--port", type=int, default=8765, help="loopback callback port")
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URL without opening it"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="also request playlist-modify-private (create the playlist, push items)",
    )
    args = parser.parse_args(argv)
    if not args.client_id or not args.client_secret:
        print("Spotify authorization failed: client ID and secret are required", file=sys.stderr)
        return 1
    try:
        authorize(
            args.client_id,
            args.client_secret,
            args.output.expanduser(),
            port=args.port,
            open_browser=not args.no_browser,
            write=args.write,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Spotify authorization failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
