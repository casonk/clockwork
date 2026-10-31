"""Clockwork pages follow the portfolio system theme and offer a rotator in the top bar."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "web" / "templates"
STATIC = ROOT / "web" / "static"
SOURCE = ROOT.parent / "traction-control" / "themes"

# Colours that do not belong to a theme: shadows and scrims are black in every theme, and
# a video stage is black by design.
NEUTRAL = re.compile(r"rgba\(0,\s*0,\s*0,[^)]*\)|#000\b")
LITERAL = re.compile(r"(?<![\w&/])#[0-9a-fA-F]{6}\b|(?<![\w&/])#[0-9a-fA-F]{3}\b(?=[;\s,)}\"'])")
# Categorical hues (grocery types, reset buttons) enter a page only as --hue, mixed with tokens.
HUE = re.compile(r"--hue:\s*#[0-9a-fA-F]{6}")


def css_and_script(text: str) -> str:
    blocks = re.findall(r"<style[^>]*>.*?</style>", text, re.S)
    return "\n".join(blocks)


def test_vendored_theme_files_match_traction_control():
    if not SOURCE.is_dir():
        return
    for name in ("portfolio-theme.css", "portfolio-theme.js"):
        assert (STATIC / name).read_text() == (SOURCE / name).read_text(), name


def test_every_page_loads_the_theme_before_its_own_styles():
    for path in TEMPLATES.glob("*.html"):
        page = path.read_text()
        if "<body>" not in page or path.name.startswith("_"):
            continue
        assert '{% include "_theme_head.html" %}' in page, path.name


def test_templates_use_theme_tokens_not_literal_colours():
    for path in [*TEMPLATES.glob("*.html"), STATIC / "site-nav.css"]:
        if path.name == "_theme_head.html":
            continue
        text = path.read_text()
        text = NEUTRAL.sub("", HUE.sub("", text))
        css = css_and_script(text) if path.suffix == ".html" else text
        assert LITERAL.findall(css) == [], f"{path.name}: {LITERAL.findall(css)[:5]}"


def test_top_bar_has_a_theme_rotator():
    nav = (TEMPLATES / "_site_nav.html").read_text()
    assert "data-theme-cycle" in nav
    assert "data-theme-icon" in nav and "data-theme-name" in nav
    css = (STATIC / "site-nav.css").read_text()
    assert ".cw-site-nav__theme" in css


def test_top_bar_offers_a_theme_menu_beside_the_rotator():
    nav = (TEMPLATES / "_site_nav.html").read_text()
    assert "data-theme-menu" in nav
    assert 'aria-label="Choose a theme"' in nav
    css = (STATIC / "site-nav.css").read_text()
    assert ".cw-site-nav__group--theme" in css and "var(--stripe" in css
    assert "contrast-light" in (STATIC / "portfolio-theme.css").read_text()
    assert "liberty" in (STATIC / "portfolio-theme.css").read_text()


def test_rendered_page_includes_theme_assets_and_rotator():
    from web import app as web_app

    client = web_app.app.test_client()
    response = client.get("/home")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "/static/portfolio-theme.css" in body
    assert "/static/portfolio-theme.js" in body
    assert "data-theme-cycle" in body
    assert client.get("/static/portfolio-theme.js").status_code == 200
