"""Publish the shared top bar's navigation for services outside Clockwork.

Services behind the shared Caddy load ``/_portfolio/chrome.js`` (traction-control's
``themes/portfolio-chrome.js``), which builds the same top bar Clockwork shows and
reads ``/_portfolio/nav.json``. Clockwork owns the menus and the service registry, so
it writes both files into the directory the Caddy route serves.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urljoin

try:
    from web import themes_admin
except ModuleNotFoundError:
    import themes_admin

STATIC = Path(__file__).resolve().parent / "static"
CHROME_SCRIPT = STATIC / "portfolio-chrome.js"
# Published name -> vendored source. theme.css and theme.js give a page the shared colour
# tokens and apply the saved theme before first paint (see traction-control docs/system-theme.md).
PUBLISHED_FILES = {
    "chrome.js": CHROME_SCRIPT,
    "theme.css": STATIC / "portfolio-theme.css",
    "theme.js": STATIC / "portfolio-theme.js",
}
DEFAULT_CHROME_DIR = Path(os.environ.get("CLOCKWORK_CHROME_DIR", "/srv/portfolio-chrome"))

# These mirror web/templates/_site_nav.html; tests/test_site_chrome.py fails if they drift.
LISTS_MENU = (
    ("Groceries", "🛒", "/groceries"),
    ("Shopping", "🛍️", "/shopping"),
    ("To Do", "✅", "/to-do"),
    ("To Code", "💻", "/to-code"),
    ("To Invest", "📈", "/to-invest"),
    ("Tradility", "📊", "/to-tradility"),
)
MEDIA_MENU = (
    ("To Watch", "📺", "/to-watch"),
    ("Library", "🎬", "/library"),
    ("To Read", "📚", "/to-read"),
    ("To Listen", "🎧", "/to-listen"),
)
SERVICES_MENU = (
    ("Schedule", "🗓️", "/schedule"),
    ("Themes", "🎨", "/themes"),
    ("Machine load", "🖥️", "/ecu"),
    ("All services", "", "/home"),
)


# Pages whose sub-pages also count as "here" in the top bar (/library/play/...).
PREFIX_PAGES = frozenset({"/groceries", "/shopping", "/library"})
# The fixed top-bar items: id -> (label, icon). Their ids are what a label overrides.
FIXED_ITEMS = {
    "nav:home": ("Home", "🏠"),
    "nav:clockwork": ("Clockwork", "⏰"),
    "nav:lists": ("Lists", "✅"),
    "nav:media": ("Media", "🎬"),
    "nav:services": ("Services", "🧰"),
}


def labelled(
    ident: str, label: str, icon: str, labels: dict[str, dict[str, str]] | None
) -> tuple[str, str]:
    """*label* and *icon* with your override for *ident*, if you set one."""
    mine = (labels or {}).get(ident, {})
    return mine.get("name", label), mine.get("icon", icon)


def entries_for(rows, labels: dict[str, dict[str, str]] | None = None) -> list[dict]:
    """Menu rows as dicts with your names and icons applied; a page's id is its path."""
    out = []
    for label, icon, path in rows:
        name, glyph = labelled(path, label, icon, labels)
        out.append(
            {
                "path": path,
                "label": name,
                "icon": glyph,
                "prefix": path in PREFIX_PAGES,
                "mark": path != "/home",  # "All services" is a pointer, never "the current page"
            }
        )
    return out


def site_menus(labels: dict[str, dict[str, str]] | None = None) -> dict[str, dict]:
    """What the server-rendered top bar shows, with your names and icons applied."""

    def fixed(key: str) -> dict:
        label, icon = labelled(key, *FIXED_ITEMS[key], labels)
        return {"label": label, "icon": icon}

    return {
        "home": fixed("nav:home"),
        "clockwork": {**fixed("nav:clockwork"), "mobile": "Jobs"},
        "lists": {**fixed("nav:lists"), "entries": entries_for(LISTS_MENU, labels)},
        "media": {**fixed("nav:media"), "entries": entries_for(MEDIA_MENU, labels)},
        "services": {
            **fixed("nav:services"),
            "mobile": "Sites",
            "entries": entries_for(SERVICES_MENU, labels),
        },
    }


def build_nav(
    base_url: str, service_groups: list[dict], labels: dict[str, dict[str, str]] | None = None
) -> dict:
    """The navigation document; ``base_url`` is Clockwork's own origin."""
    base = base_url.rstrip("/") + "/"
    menus = site_menus(labels)

    def own(path: str) -> str:
        return urljoin(base, path.lstrip("/"))

    def entries(key: str) -> list[dict]:
        return [
            {"label": e["label"], "icon": e["icon"], "url": own(e["path"])}
            for e in menus[key]["entries"]
        ]

    services: list[dict] = entries("services")
    for group in service_groups:
        services.append({"section": group["label"]})
        for svc in group["services"]:
            url = str(svc.get("url", ""))
            if not url:
                continue
            services.append(
                {
                    "label": svc.get("display", svc.get("name", "")),
                    "icon": svc.get("icon", ""),
                    "url": own(url) if url.startswith("/") else url,
                    "service": True,
                }
            )
    return {
        "version": 1,
        "home": {**menus["home"], "url": own("/home")},
        "items": [
            {**menus["clockwork"], "url": own("/")},
            {
                "label": menus["lists"]["label"],
                "icon": menus["lists"]["icon"],
                "menu": entries("lists"),
            },
            {
                "label": menus["media"]["label"],
                "icon": menus["media"]["icon"],
                "menu": entries("media"),
            },
            {
                "label": menus["services"]["label"],
                "mobile": "Sites",
                "icon": menus["services"]["icon"],
                "align": "right",
                "menu": services,
            },
        ],
    }


def _write_if_changed(path: Path, data: bytes) -> bool:
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return True


def store_shared_dir(env: Mapping[str, str] | None = None) -> Path:
    """``_shared`` in traction-control's portfolio site store, where every enrolled repository's
    pages look for the navigation when no Caddy answers (traction-control docs/portfolio-sites.md).
    """
    env = os.environ if env is None else env
    if env.get("PORTFOLIO_STORE"):
        return Path(env["PORTFOLIO_STORE"]) / "_shared"
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "traction-control" / "sites" / "_shared"


def chrome_dirs() -> list[Path]:
    """Existing directories to publish into: the Caddy-served chrome directory and the store."""
    return [d for d in (DEFAULT_CHROME_DIR, store_shared_dir()) if d.is_dir()]


def publish(nav: dict, directory: Path = DEFAULT_CHROME_DIR, *, files: bool = True) -> bool:
    """Write nav.json (and, with ``files``, chrome.js, theme.css and theme.js) into a directory;
    False when it is absent. The store's theme files are traction-control's to publish."""
    if not directory.is_dir():
        return False
    changed = _write_if_changed(directory / "nav.json", (json.dumps(nav, indent=2) + "\n").encode())
    if files:
        for name, source in PUBLISHED_FILES.items():
            # Your rendered themes when there are any, else the vendored copy. This directory is
            # served by Caddy, which cannot follow a link into an owner-only home, so it is a copy.
            changed |= _write_if_changed(
                directory / name, (themes_admin.asset(name) or source).read_bytes()
            )
    return changed


def publish_everywhere(nav: dict) -> bool:
    """Publish to every existing target; only the Caddy directory gets the script and theme files."""
    changed = False
    for directory in chrome_dirs():
        changed |= publish(nav, directory, files=directory == DEFAULT_CHROME_DIR)
    return changed


__all__ = [
    "CHROME_SCRIPT",
    "DEFAULT_CHROME_DIR",
    "build_nav",
    "chrome_dirs",
    "publish",
    "publish_everywhere",
    "store_shared_dir",
]
