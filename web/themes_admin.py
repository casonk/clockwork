"""The themes page's backend: your own themes, through traction-control's ``portfolio_themes.py``.

traction-control owns the theme definitions and their validation. Clockwork never edits another
repository's files and never parses or writes ``themes.local.toml`` itself: it asks the tool
(``show --json``, ``apply --stdin``, ``reset``) and gets back either the state or a one-line error.

The tool renders the merged themes once into ``~/.config/traction-control/themes/rendered/``. This module
also says where a page should load a theme file from: that render when it exists, else the copy vendored
in ``web/static`` (so Clockwork still works with no traction-control checkout, and before anything is saved).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

STATIC = Path(__file__).resolve().parent / "static"
ENV = "CLOCKWORK_TRACTION_CONTROL_DIR"
TIMEOUT_SECONDS = 30
# Published name -> the vendored file that serves when nothing is rendered.
VENDORED = {
    "theme.css": STATIC / "portfolio-theme.css",
    "theme.js": STATIC / "portfolio-theme.js",
    "chrome.js": STATIC / "portfolio-chrome.js",
}
CONTENT_TYPES = {
    "theme.css": "text/css; charset=utf-8",
    "theme.js": "text/javascript; charset=utf-8",
    "chrome.js": "text/javascript; charset=utf-8",
}


class ThemesError(Exception):
    """The tool refused a change, or cannot be run; the message is safe to show."""


def config_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "traction-control"


def rendered_dir(env: dict[str, str] | None = None) -> Path:
    return config_dir(env) / "themes" / "rendered"


def tool(env: dict[str, str] | None = None) -> Path | None:
    """traction-control's ``portfolio_themes.py``: ``CLOCKWORK_TRACTION_CONTROL_DIR``, else the sibling checkout."""
    env = os.environ if env is None else env
    root = (
        Path(env[ENV]) if env.get(ENV) else Path(__file__).resolve().parents[2] / "traction-control"
    )
    script = root / "scripts" / "portfolio_themes.py"
    return script if script.is_file() else None


def asset(name: str) -> Path | None:
    """Where to load ``theme.css``, ``theme.js`` or ``chrome.js`` from: your render, else the vendored copy."""
    if name not in VENDORED:
        return None
    rendered = rendered_dir() / name
    return rendered if rendered.is_file() else VENDORED[name]


_labels_cache: tuple[tuple[float, int], dict[str, dict[str, str]]] | None = None


def labels() -> dict[str, dict[str, str]]:
    """Your names and icons for cards, menu entries and top-bar items, by id (see traction-control's
    ``[labels]``), read from the rendered ``labels.json``; empty when there is none or it is unreadable.

    Read on every page, so it is cached by file time and size.
    """
    global _labels_cache
    path = rendered_dir() / "labels.json"
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (stat.st_mtime, stat.st_size)
    if _labels_cache is None or _labels_cache[0] != key:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        clean = (
            {
                str(ident): {k: str(v) for k, v in entry.items() if k in ("name", "icon")}
                for ident, entry in data.items()
                if isinstance(entry, dict)
            }
            if isinstance(data, dict)
            else {}
        )
        _labels_cache = (key, clean)
    return _labels_cache[1]


def _run(args: list[str], stdin: str | None = None) -> str:
    script = tool()
    if script is None:
        raise ThemesError(
            f"traction-control's portfolio_themes.py was not found; set {ENV} to its checkout"
        )
    try:
        done = subprocess.run(
            [sys.executable, str(script), *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ThemesError("the themes tool timed out") from exc
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip().splitlines()
        raise ThemesError(
            message[-1].removeprefix("error: ") if message else "the themes tool failed"
        )
    return done.stdout


def state() -> dict[str, Any]:
    """Every theme with where it came from, the saved palette and emoji, and what is active."""
    return json.loads(_run(["show", "--json"]))


def apply(desired: dict[str, Any]) -> dict[str, Any]:
    """Save and publish *desired* (the shape ``state`` returns); raises ThemesError if it is refused."""
    _run(["apply", "--stdin"], json.dumps(desired))
    return state()


def reset() -> dict[str, Any]:
    _run(["reset"])
    return state()
