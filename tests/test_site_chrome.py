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
    for label, body in re.findall(
        r"<summary>[^<]*<span[^>]*>([^<]*)</span>.*?</summary>(.*?)</details>", html, re.S
    ):
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
        {
            "label": "Pinned",
            "services": [
                {
                    "name": "sample",
                    "display": "Sample App",
                    "icon": "💼",
                    "url": "https://sample.example/",
                }
            ],
        },
        {
            "label": "Clockwork",
            "services": [{"name": "x", "display": "Local", "icon": "", "url": "/local"}],
        },
    ]
    nav = site_chrome.build_nav("https://clock.example", groups)

    assert nav["home"]["url"] == "https://clock.example/home"
    assert nav["items"][0] == {
        "label": "Clockwork",
        "mobile": "Jobs",
        "icon": "⏰",
        "url": "https://clock.example/",
    }
    lists = next(i for i in nav["items"] if i["label"] == "Lists")
    assert lists["menu"][0]["url"] == "https://clock.example/groceries"
    menu = next(i for i in nav["items"] if i["label"] == "Services")["menu"]
    assert {"section": "Pinned"} in menu
    sample = next(e for e in menu if e.get("label") == "Sample App")
    assert sample["service"] is True and sample["url"] == "https://sample.example/"
    local = next(e for e in menu if e.get("label") == "Local")
    assert local["url"] == "https://clock.example/local"


def test_publish_writes_both_files_once_and_skips_a_missing_directory(tmp_path):
    nav = site_chrome.build_nav("https://clock.example", [])
    assert site_chrome.publish(nav, tmp_path / "absent") is False

    assert site_chrome.publish(nav, tmp_path) is True
    assert json.loads((tmp_path / "nav.json").read_text())["home"]["label"] == "Home"
    assert (tmp_path / "chrome.js").read_text() == site_chrome.CHROME_SCRIPT.read_text()
    for name in ("theme.css", "theme.js"):
        assert (tmp_path / name).read_text() == site_chrome.PUBLISHED_FILES[name].read_text()
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


def test_publish_everywhere_writes_the_nav_to_the_store_and_the_files_only_to_the_caddy_dir(
    tmp_path, monkeypatch
):
    caddy = tmp_path / "caddy"
    caddy.mkdir()
    store = tmp_path / "store"
    (store / "_shared").mkdir(parents=True)
    monkeypatch.setattr(site_chrome, "DEFAULT_CHROME_DIR", caddy)
    monkeypatch.setenv("PORTFOLIO_STORE", str(store))
    nav = site_chrome.build_nav("https://clock.example", [])

    assert site_chrome.publish_everywhere(nav) is True
    assert (caddy / "chrome.js").is_file() and (caddy / "nav.json").is_file()
    assert (store / "_shared" / "nav.json").is_file()
    assert not (store / "_shared" / "chrome.js").exists()  # traction-control publishes those
    assert site_chrome.publish_everywhere(nav) is False


def test_publish_everywhere_does_nothing_when_no_target_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(site_chrome, "DEFAULT_CHROME_DIR", tmp_path / "absent")
    monkeypatch.setenv("PORTFOLIO_STORE", str(tmp_path / "no-store"))
    assert site_chrome.chrome_dirs() == []
    assert site_chrome.publish_everywhere(site_chrome.build_nav("https://c.example", [])) is False
