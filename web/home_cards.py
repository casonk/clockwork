"""Home page cards the Grandmaster Clock controls: which cards are hidden, and custom link cards.

Stored in the gitignored ``config/home-cards.json`` (``CLOCKWORK_HOME_CARDS_FILE``) because link
targets can name private hosts. Hiding a card removes it from Home only; the service stays in the
Services menu and keeps running. Placeholder cards keep their own store (``web.placeholders``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from web.placeholders import DEFAULT_GROUP, GROUP_RE, UNSAFE
except ModuleNotFoundError:  # run as a script from web/
    from placeholders import DEFAULT_GROUP, GROUP_RE, UNSAFE

ROOT = Path(__file__).resolve().parents[1]
FILE = Path(os.environ.get("CLOCKWORK_HOME_CARDS_FILE", ROOT / "config" / "home-cards.json"))

DEFAULT_ICON = "🔗"
MAX_LINKS = 100
MAX_HIDDEN = 500
MAX_URL = 300
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
PATH_URL_RE = re.compile(r"/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]*")
_LINK_FIELDS = ("id", "name", "icon", "group", "url")


# Cards that are always on Home: the Grandmaster Clock is how the rest are brought back.
ALWAYS_SHOWN = frozenset({"clockwork-grandmaster"})


class HomeCardError(Exception):
    """The change was refused, or the store cannot be updated. The message is safe to show."""


def _clean_link(card: Any) -> dict[str, str]:
    if not isinstance(card, dict):
        raise ValueError("card")
    cleaned = {key: card[key] for key in _LINK_FIELDS}
    if not all(isinstance(value, str) and value for value in cleaned.values()):
        raise ValueError("card")
    return cleaned


def load(*, strict: bool = False) -> dict[str, Any]:
    """``{"hidden": list | None, "links": [...]}``; ``hidden`` is None until first saved.

    A missing file is the default. An unreadable one is the default for the portal (Home still
    renders) and an error for a change, which refuses to overwrite it.
    """
    empty: dict[str, Any] = {"hidden": None, "links": []}
    if not FILE.is_file():
        return empty
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("document")
        hidden = raw.get("hidden")
        if hidden is not None and not (
            isinstance(hidden, list) and all(isinstance(n, str) and n for n in hidden)
        ):
            raise ValueError("hidden")
        links = raw.get("links", [])
        if not isinstance(links, list):
            raise ValueError("links")
        return {"hidden": hidden, "links": [_clean_link(card) for card in links]}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        if strict:
            raise HomeCardError("the home cards file is unreadable") from exc
        return empty


def save(document: dict[str, Any]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    temporary = FILE.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(temporary, 0o600)
    os.replace(temporary, FILE)
    os.chmod(FILE, 0o600)


def hidden_names(default: set[str]) -> set[str]:
    """The cards left off Home: the saved list, or ``default`` until one has been saved."""
    stored = load()["hidden"]
    return (set(default) if stored is None else set(stored)) - ALWAYS_SHOWN


def set_hidden(name: str, hidden: bool, *, default: set[str]) -> None:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise HomeCardError("card: a service or page name")
    if hidden and name in ALWAYS_SHOWN:
        raise HomeCardError("the Grandmaster Clock card is always shown")
    document = load(strict=True)
    current = set(default) if document["hidden"] is None else set(document["hidden"])
    if hidden:
        if len(current) >= MAX_HIDDEN and name not in current:
            raise HomeCardError(f"at most {MAX_HIDDEN} hidden cards")
        current.add(name)
    else:
        current.discard(name)
    document["hidden"] = sorted(current)
    save(document)


def _valid_url(url: str) -> bool:
    if not url or len(url) > MAX_URL or re.search(r"[\s\x00-\x1f\x7f<>\"'`\\]", url):
        return False
    if url.startswith("/"):
        return bool(PATH_URL_RE.fullmatch(url))
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username


def prepare_link(payload: dict[str, Any], taken: set[str]) -> dict[str, str]:
    """Validate a link card; ``taken`` are card names already on Home."""
    if not isinstance(payload, dict):
        raise HomeCardError("send the card as a JSON object")
    name = payload.get("name")
    name = name.strip() if isinstance(name, str) else ""
    if not 1 <= len(name) <= 32 or UNSAFE.search(name):
        raise HomeCardError("name: 1 to 32 characters, with no markup or comment markers")
    icon = payload.get("icon") or ""
    if not isinstance(icon, str):
        raise HomeCardError("icon: 1 to 8 emoji or symbol characters")
    icon = icon.strip() or DEFAULT_ICON
    if not 1 <= len(icon) <= 8 or any(ord(char) < 128 for char in icon):
        raise HomeCardError("icon: 1 to 8 emoji or symbol characters")
    group = payload.get("group") or DEFAULT_GROUP
    group = group.strip() if isinstance(group, str) else ""
    if not GROUP_RE.fullmatch(group):
        raise HomeCardError("group: letters, digits, spaces, . _ or -")
    url = payload.get("url")
    url = url.strip() if isinstance(url, str) else ""
    if not _valid_url(url):
        raise HomeCardError("url: an http(s) address or a path starting with /")
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40] or "link"
    ident, number = base, 2
    while f"link-{ident}" in taken:
        ident = f"{base}-{number}"
        number += 1
    return {"id": ident, "name": name, "icon": icon, "group": group, "url": url}


def add_link(card: dict[str, str]) -> dict[str, str]:
    document = load(strict=True)
    if len(document["links"]) >= MAX_LINKS:
        raise HomeCardError(f"at most {MAX_LINKS} link cards")
    if any(existing["id"] == card["id"] for existing in document["links"]):
        raise HomeCardError(f"{card['id']} is already a link card")
    document["links"].append(card)
    save(document)
    return card


def remove_link(ident: str) -> dict[str, str]:
    document = load(strict=True)
    match = next((c for c in document["links"] if c["id"] == ident), None)
    if match is None:
        raise HomeCardError("no link card with that id")
    document["links"] = [c for c in document["links"] if c["id"] != ident]
    save(document)
    return match
