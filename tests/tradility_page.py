"""Render the shared Tradility template the way Clockwork serves it (embedded)."""

from __future__ import annotations

from pathlib import Path

import jinja2

PAGE = Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"


def rendered(*, embedded: bool = True) -> str:
    env = jinja2.Environment(
        loader=jinja2.DictLoader({"_theme_head.html": "", "_site_nav.html": ""})
    )
    env.globals["url_for"] = lambda endpoint, **kw: "/static/" + kw.get("filename", "")
    return env.from_string(PAGE.read_text(encoding="utf-8")).render(embedded=embedded)
