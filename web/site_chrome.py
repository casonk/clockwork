"""Publish the shared top bar's navigation for services outside Clockwork.

Services behind the shared Caddy load ``/_portfolio/chrome.js`` (traction-control's
``themes/portfolio-chrome.js``), which builds the same top bar Clockwork shows and
reads ``/_portfolio/nav.json``. Clockwork owns the menus and the service registry, so
it writes both files into the directory the Caddy route serves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urljoin

CHROME_SCRIPT = Path(__file__).resolve().parent / "static" / "portfolio-chrome.js"
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
    ("Machine load", "🖥️", "/ecu"),
    ("All services", "", "/home"),
)


def build_nav(base_url: str, service_groups: list[dict]) -> dict:
    """The navigation document; ``base_url`` is Clockwork's own origin."""
    base = base_url.rstrip("/") + "/"

    def own(path: str) -> str:
        return urljoin(base, path.lstrip("/"))

    def entries(rows) -> list[dict]:
        return [{"label": label, "icon": icon, "url": own(path)} for label, icon, path in rows]

    services: list[dict] = entries(SERVICES_MENU)
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
        "home": {"label": "Home", "icon": "🏠", "url": own("/home")},
        "items": [
            {"label": "Clockwork", "mobile": "Jobs", "icon": "⏰", "url": own("/")},
            {"label": "Lists", "icon": "✅", "menu": entries(LISTS_MENU)},
            {"label": "Media", "icon": "🎬", "menu": entries(MEDIA_MENU)},
            {
                "label": "Services",
                "mobile": "Sites",
                "icon": "🧰",
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


def publish(nav: dict, directory: Path = DEFAULT_CHROME_DIR) -> bool:
    """Write nav.json and chrome.js into the shared directory; False when it is absent."""
    if not directory.is_dir():
        return False
    changed = _write_if_changed(directory / "nav.json", (json.dumps(nav, indent=2) + "\n").encode())
    changed |= _write_if_changed(directory / "chrome.js", CHROME_SCRIPT.read_bytes())
    return changed


__all__ = ["CHROME_SCRIPT", "DEFAULT_CHROME_DIR", "build_nav", "publish"]
