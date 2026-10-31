"""The shared top bar's navigation document and its publication for other services."""

import json
import re
from pathlib import Path

from web import site_chrome

ROOT = Path(__file__).resolve().parents[1]


def template_links() -> dict[str, list[str]]:
    """The paths in each menu of web/templates/_site_nav.html, by group."""
    html = (ROOT / "web" / "templates" / "_site_nav.html").read_text()
    out = {}
    for label, body in re.findall(r"<summary>[^<]*<span[^>]*>([^<]*)</span>.*?</summary>(.*?)</details>", html, re.S):
        out[label] = re.findall(r'<a href="(/[^"]*)"', body)
    return out


def test_menus_match_the_server_rendered_navigation():
    links = template_links()
    assert [p for _, _, p in site_chrome.LISTS_MENU] == links["Lists"]
    assert [p for _, _, p in site_chrome.MEDIA_MENU] == links["Media"]
    services = [p for _, _, p in site_chrome.SERVICES_MENU]
    assert services == links["Services"][: len(services)]


def test_build_nav_makes_absolute_links_and_flags_services():
    groups = [
        {"label": "Pinned", "services": [{"name": "career", "display": "Career", "icon": "💼", "url": "https://career.example/"}]},
        {"label": "Clockwork", "services": [{"name": "x", "display": "Local", "icon": "", "url": "/local"}]},
    ]
    nav = site_chrome.build_nav("https://clock.example", groups)

    assert nav["home"]["url"] == "https://clock.example/home"
    assert nav["items"][0] == {"label": "Clockwork", "mobile": "Jobs", "icon": "⏰", "url": "https://clock.example/"}
    lists = next(i for i in nav["items"] if i["label"] == "Lists")
    assert lists["menu"][0]["url"] == "https://clock.example/groceries"
    menu = next(i for i in nav["items"] if i["label"] == "Services")["menu"]
    assert {"section": "Pinned"} in menu
    career = next(e for e in menu if e.get("label") == "Career")
    assert career["service"] is True and career["url"] == "https://career.example/"
    local = next(e for e in menu if e.get("label") == "Local")
    assert local["url"] == "https://clock.example/local"


def test_publish_writes_both_files_once_and_skips_a_missing_directory(tmp_path):
    nav = site_chrome.build_nav("https://clock.example", [])
    assert site_chrome.publish(nav, tmp_path / "absent") is False

    assert site_chrome.publish(nav, tmp_path) is True
    assert json.loads((tmp_path / "nav.json").read_text())["home"]["label"] == "Home"
    assert (tmp_path / "chrome.js").read_text() == site_chrome.CHROME_SCRIPT.read_text()
    assert site_chrome.publish(nav, tmp_path) is False  # unchanged


def test_api_site_nav_serves_the_document():
    from web import app as web_app

    response = web_app.app.test_client().get("/api/site-nav")
    body = response.get_json()

    assert response.status_code == 200
    assert body["home"]["url"].endswith("/home")
    assert [i["label"] for i in body["items"]] == ["Clockwork", "Lists", "Media", "Services"]


def test_vendored_chrome_script_matches_traction_control():
    source = ROOT.parent / "traction-control" / "themes" / "portfolio-chrome.js"
    if source.is_file():
        assert site_chrome.CHROME_SCRIPT.read_text() == source.read_text()
