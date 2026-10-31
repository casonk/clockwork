"""Placeholder Home cards: pages that are registered before they are built.

These are Clockwork portal cards, stored in the gitignored ``config/placeholders.json``
(``CLOCKWORK_PLACEHOLDERS_FILE``). They stay out of the shared theme layer, which only
renames things that already exist, and out of the published services menu, because a
planned path is not a page yet. Registering one also adds a To Code item; this module
only stores the card.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FILE = Path(os.environ.get("CLOCKWORK_PLACEHOLDERS_FILE", ROOT / "config" / "placeholders.json"))

DEFAULT_GROUP = "Clockwork"
DEFAULT_ICON = "🧩"
MAX_CARDS = 100

# A page path, matching traction-control's label id for a page, and never ".." or a scheme.
PATH_RE = re.compile(r"/[a-z0-9][a-z0-9/_-]{0,62}")
GROUP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
UNSAFE = re.compile(r"[\x00-\x1f\x7f<>&\"'`\\]|/\*|\*/")
_FIELDS = ("id", "name", "icon", "group", "repo_group", "repo")


class PlaceholderError(Exception):
    """The card was refused, or the store cannot be updated. The message is safe to show."""


def _free_path(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40] or "card"
    candidate = "/" + base
    number = 2
    while candidate in taken:
        suffix = f"-{number}"
        candidate = "/" + base[: 62 - len(suffix)] + suffix
        number += 1
        if number > MAX_CARDS + 2:
            raise PlaceholderError("could not make a free path")
    return candidate


def prepare(payload: dict[str, Any], taken: set[str], *, repo_groups: list[str]) -> dict[str, str]:
    """Validate a registration and return the card to store. ``taken`` ids are already cards."""
    if not isinstance(payload, dict):
        raise PlaceholderError("send the card as a JSON object")
    name = payload.get("name")
    if not isinstance(name, str):
        name = ""
    name = name.strip()
    if not 1 <= len(name) <= 32 or UNSAFE.search(name):
        raise PlaceholderError("name: 1 to 32 characters, with no markup or comment markers")
    icon = payload.get("icon") or ""
    if not isinstance(icon, str):
        raise PlaceholderError("icon: 1 to 8 emoji or symbol characters")
    icon = icon.strip()
    if icon == "":
        icon = DEFAULT_ICON
    elif not 1 <= len(icon) <= 8 or any(ord(char) < 128 for char in icon):
        raise PlaceholderError("icon: 1 to 8 emoji or symbol characters")
    group = payload.get("group") or DEFAULT_GROUP
    if not isinstance(group, str):
        raise PlaceholderError("group: letters, digits, spaces, . _ or -")
    group = group.strip()
    if not GROUP_RE.fullmatch(group):
        raise PlaceholderError("group: letters, digits, spaces, . _ or -")
    repo_group = payload.get("repo_group") or ""
    if not isinstance(repo_group, str) or repo_group.strip() not in repo_groups:
        raise PlaceholderError("repo group is not a To Code group")
    repo = payload.get("repo") or ""
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo.strip()):
        raise PlaceholderError("repository: letters, digits, . _ or -")
    raw_path = payload.get("path") or ""
    if not isinstance(raw_path, str):
        raise PlaceholderError("path: a lowercase page path such as /pantry-plan")
    raw_path = raw_path.strip()
    if raw_path:
        if not PATH_RE.fullmatch(raw_path):
            raise PlaceholderError("path: a lowercase page path such as /pantry-plan")
        if raw_path in taken:
            raise PlaceholderError(f"{raw_path} is already a card")
        ident = raw_path
    else:
        ident = _free_path(name, taken)
    return {
        "id": ident,
        "name": name,
        "icon": icon,
        "group": group,
        "repo_group": repo_group.strip(),
        "repo": repo.strip(),
    }


def _stored(card: Any) -> dict[str, str]:
    if not isinstance(card, dict):
        raise ValueError("card")
    cleaned = {key: card[key] for key in _FIELDS}
    if not all(isinstance(value, str) and value for value in cleaned.values()):
        raise ValueError("card")
    if not PATH_RE.fullmatch(cleaned["id"]):
        raise ValueError("id")
    return cleaned


def load(*, strict: bool = False) -> list[dict[str, str]]:
    """The registered cards. A missing file is an empty list.

    A file that cannot be read is an empty list for the portal (so Home still renders) and an
    error for a registration, which refuses to overwrite it.
    """
    path = FILE
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cards = raw.get("cards") if isinstance(raw, dict) else None
        if not isinstance(cards, list):
            raise ValueError("cards")
        return [_stored(card) for card in cards]
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        if strict:
            raise PlaceholderError("the placeholders file is unreadable") from exc
        return []


def save(cards: list[dict[str, str]]) -> None:
    path = FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"cards": cards}, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def add(card: dict[str, str]) -> None:
    cards = load(strict=True)
    if len(cards) >= MAX_CARDS:
        raise PlaceholderError(f"at most {MAX_CARDS} placeholder cards")
    if any(existing["id"] == card["id"] for existing in cards):
        raise PlaceholderError(f"{card['id']} is already a placeholder")
    cards.append(card)
    save(cards)


def remove(ident: str) -> dict[str, str]:
    cards = load(strict=True)
    match = next((card for card in cards if card["id"] == ident), None)
    if match is None:
        raise PlaceholderError("no placeholder with that path")
    save([card for card in cards if card["id"] != ident])
    return match
