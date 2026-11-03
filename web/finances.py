"""The /finances page and its read-only proxy to personal-finance's finance API.

The browser never sees the API key: this blueprint reads it from an owner-only file and adds it
to GET requests it forwards to the loopback service. Only the service's read routes are
forwardable, so the page cannot reach anything that trades or changes data (the service has no
such routes and holds no broker credentials).

Settings: ``CLOCKWORK_FINANCE_URL`` (default ``http://127.0.0.1:8769``) and
``CLOCKWORK_FINANCE_API_KEY_FILE`` (default ``~/.config/personal-finance/finance-api.key``).
"""

from __future__ import annotations

import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Blueprint, Response, render_template, request

bp = Blueprint("finances", __name__)

ALLOWED = re.compile(r"(summary|accounts|networth|freshness|accounts/[a-z0-9-]{1,120}/history)")
TIMEOUT_SECONDS = 10
MONTH = re.compile(r"\d{4}-\d{2}(-\d{2})?")
ACCOUNT_IDS = re.compile(r"[a-z0-9-]{1,120}(,[a-z0-9-]{1,120}){0,299}")
FILTERED = {"summary", "networth"}


def forwarded_query(rest: str) -> str:
    """The filters the service understands, each validated; anything else is dropped."""
    if rest not in FILTERED:
        return ""
    pairs = []
    for name, pattern in (
        ("accounts", ACCOUNT_IDS),
        ("from", MONTH),
        ("to", MONTH),
        ("months", re.compile(r"\d{1,3}")),
    ):
        value = request.args.get(name, "")
        if value and pattern.fullmatch(value):
            pairs.append((name, value))
    return ("?" + urllib.parse.urlencode(pairs, safe=",-")) if pairs else ""


def service_url() -> str:
    return os.environ.get("CLOCKWORK_FINANCE_URL", "http://127.0.0.1:8769").rstrip("/")


def key_file() -> Path:
    return Path(
        os.environ.get(
            "CLOCKWORK_FINANCE_API_KEY_FILE",
            Path.home() / ".config" / "personal-finance" / "finance-api.key",
        )
    )


def read_key() -> str:
    """The key, or '' when the file is missing or readable by group/others."""
    path = key_file()
    try:
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            return ""
        return path.read_text().strip()
    except OSError:
        return ""


def _json(status: int, body: dict) -> Response:
    return Response(
        json.dumps(body),
        status=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@bp.get("/finances")
def page():
    return render_template("finances.html")


@bp.get("/api/finances/<path:rest>")
def proxy(rest: str):
    if not ALLOWED.fullmatch(rest):
        return _json(404, {"error": "not found"})
    key = read_key()
    if not key:
        return _json(503, {"error": "finance API key is not set up"})
    url = f"{service_url()}/api/{rest}"
    url += forwarded_query(rest)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return Response(
                resp.read(),
                status=resp.status,
                mimetype="application/json",
                headers={"Cache-Control": "no-store"},
            )
    except urllib.error.HTTPError as err:
        return _json(
            err.code if err.code in (404, 401) else 502, {"error": f"finance API {err.code}"}
        )
    except (urllib.error.URLError, OSError, TimeoutError):
        return _json(502, {"error": "finance API is not reachable"})
