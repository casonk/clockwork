#!/usr/bin/env python3
"""Authorize Clockwork to read a user's YouTube playlists.

Google's installed-app loopback flow is completed locally.  The resulting
refresh token is written to an owner-only file and is never printed.
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

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_WRITE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    finished = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        type(self).result = {key: values[0] for key, values in query.items() if values}
        body = b"YouTube authorization received. You can close this tab and return to Clockwork."
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


def _client(path: Path) -> tuple[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        installed = payload.get("installed", payload)
        client_id = str(installed["client_id"]).strip()
        client_secret = str(installed["client_secret"]).strip()
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        AttributeError,
        KeyError,
        TypeError,
    ) as exc:
        raise ValueError(
            "client JSON must be a Google OAuth Desktop credential or an existing Clockwork grant"
        ) from exc
    if not client_id or not client_secret:
        raise ValueError("client JSON is missing its client ID or client secret")
    return client_id, client_secret


def _exchange(data: dict[str, str]) -> dict:
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(data).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
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
    client_json: Path,
    output: Path,
    *,
    open_browser: bool = True,
    create_playlists: bool = False,
) -> None:
    client_id, client_secret = _client(client_json)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    state = secrets.token_urlsafe(32)
    CallbackHandler.result = {}
    CallbackHandler.finished.clear()
    with ReusableTCPServer(("127.0.0.1", 0), CallbackHandler) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
        scopes = [YOUTUBE_READ_SCOPE]
        if create_playlists:
            scopes.append(YOUTUBE_WRITE_SCOPE)
        query = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "access_type": "offline",
                "prompt": "select_account consent",
                "state": state,
                "code_challenge": challenge.decode("ascii"),
                "code_challenge_method": "S256",
            }
        )
        url = f"{AUTH_URL}?{query}"
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
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    )
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        raise RuntimeError("Google returned no refresh token; revoke the old grant and try again")
    _write_owner_only(
        output,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "scopes": scopes,
            "token_uri": TOKEN_URL,
        },
    )
    print(f"Saved owner-only YouTube authorization to {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "client_json",
        type=Path,
        help="downloaded Google OAuth Desktop client JSON or existing Clockwork grant",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("~/.config/clockwork/youtube-oauth.json").expanduser(),
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URL without opening it"
    )
    parser.add_argument(
        "--create-playlists",
        action="store_true",
        help="also request Google's broad permission required to create playlists",
    )
    args = parser.parse_args(argv)
    try:
        authorize(
            args.client_json.expanduser(),
            args.output.expanduser(),
            open_browser=not args.no_browser,
            create_playlists=args.create_playlists,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"YouTube authorization failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
