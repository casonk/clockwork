"""The themes page: your own themes through traction-control's portfolio_themes.py."""

# The route tests reuse the web fixtures from test_web_lists as parameters.
# ruff: noqa: F811, F401

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tests.test_web_lists import _csrf, client, list_files, web_app
from web import site_chrome, themes_admin

SIBLING = Path(
    os.environ.get(themes_admin.ENV) or Path(__file__).resolve().parents[2] / "traction-control"
)
needs_tool = pytest.mark.skipif(
    not (SIBLING / "scripts" / "portfolio_themes.py").is_file(),
    reason="traction-control is not checked out beside this repository",
)


@pytest.fixture
def themes_env(tmp_path, monkeypatch):
    """Point the tool at throwaway config so nothing touches the real themes."""
    config = tmp_path / "config"
    (config / "traction-control" / "sites" / "_shared").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv(themes_admin.ENV, str(SIBLING))
    monkeypatch.delenv("PORTFOLIO_THEMES_LOCAL", raising=False)
    # Saving publishes a copy into the Caddy chrome directory. Keep that copy in the test
    # directory; the real /srv/portfolio-chrome is what the live sites read.
    chrome = tmp_path / "portfolio-chrome"
    chrome.mkdir()
    monkeypatch.setattr(site_chrome, "DEFAULT_CHROME_DIR", chrome)
    return config / "traction-control"


def editor_state(client):
    return client.get("/api/themes").get_json()["state"]


def apply(client, desired, token=None):
    token = token or _csrf(client, "/api/themes")
    return client.post("/api/themes/apply", json=desired, headers={"X-CSRF-Token": token})


def unchanged(state):
    return {"themes": state["themes"], "active": state["active"]}


def test_the_page_loads_and_says_so_when_the_tool_is_missing(client, monkeypatch, tmp_path):
    monkeypatch.setenv(themes_admin.ENV, str(tmp_path / "nowhere"))
    html = client.get("/themes").get_data(as_text=True)
    assert 'data-tool="no"' in html and "portfolio_themes.py" in html
    response = client.get("/api/themes")
    assert response.status_code == 503
    assert "not found" in response.get_json()["error"]


@needs_tool
def test_the_state_lists_every_tracked_theme_with_its_origin(client, themes_env):
    assert 'data-tool="yes"' in client.get("/themes").get_data(as_text=True)
    state = editor_state(client)
    names = [t["name"] for t in state["themes"]]
    assert {"midnight", "daylight", "neon"} <= set(names)
    assert all(t["origin"] == "builtin" and t["active"] for t in state["themes"])
    assert len(state["tokens"]) == 17


@needs_tool
def test_saving_needs_a_csrf_token_and_writes_owner_only_files(client, themes_env):
    state = editor_state(client)
    assert client.post("/api/themes/apply", json=unchanged(state)).status_code == 403
    state["themes"][0]["label"] = "Night"
    state["themes"][0]["tokens"]["accent"] = "#ff8800"
    response = apply(client, unchanged(state))
    assert response.status_code == 200 and response.get_json()["ok"]
    local = themes_env / "themes.local.toml"
    assert stat.S_IMODE(local.stat().st_mode) == 0o600
    assert stat.S_IMODE((themes_env / "themes" / "rendered").stat().st_mode) == 0o700
    after = response.get_json()["state"]["themes"][0]
    assert (after["label"], after["modified"], after["tokens"]["accent"]) == (
        "Night",
        True,
        "#ff8800",
    )
    for name in ("theme.css", "theme.js", "chrome.js"):  # the store links to the one render
        assert (themes_env / "sites" / "_shared" / name).is_symlink()


@needs_tool
def test_a_theme_of_your_own_and_the_libraries_round_trip(client, themes_env):
    state = editor_state(client)
    mine = {
        **state["themes"][0],
        "name": "mine",
        "label": "Mine",
        "icon": "🚀",
        "based_on": "ember",
    }
    desired = {
        "themes": [*state["themes"], mine],
        "active": [*state["active"], "mine"],
        "palette": {"brand": "#1f6feb"},
        "emoji": {"rocket": "🚀"},
    }
    assert apply(client, desired).status_code == 200
    after = editor_state(client)
    row = next(t for t in after["themes"] if t["name"] == "mine")
    assert (row["origin"], row["active"]) == ("custom", True)
    assert (after["palette"], after["emoji"]) == ({"brand": "#1f6feb"}, {"rocket": "🚀"})


@needs_tool
@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["themes"][0].update(label="<script>alert(1)</script>"),
        lambda s: s["themes"][0].update(description="ok */ body{display:none}"),
        lambda s: s["themes"][0]["tokens"].update(accent="red"),
        lambda s: s["themes"][0].update(icon="x"),
        lambda s: s.update(active=["midnight", "ember"]),  # no light theme left
    ],
)
def test_an_invalid_change_is_refused_with_a_message_and_writes_nothing(client, themes_env, change):
    state = editor_state(client)
    desired = unchanged(state)
    change(desired)
    response = apply(client, desired)
    assert response.status_code == 400 and not response.get_json()["ok"]
    assert response.get_json()["error"]
    assert not (themes_env / "themes.local.toml").exists()


@needs_tool
def test_pages_load_the_rendered_themes_once_there_are_any_and_the_vendored_ones_before(
    client, themes_env
):
    before = client.get("/theme-assets/theme.css")
    assert before.status_code == 200 and b'data-theme="mine"' not in before.data
    assert before.headers["Cache-Control"] == "no-cache"
    state = editor_state(client)
    mine = {**state["themes"][0], "name": "mine", "label": "Mine", "based_on": "ember"}
    apply(client, {"themes": [*state["themes"], mine], "active": [*state["active"], "mine"]})
    assert b'data-theme="mine"' in client.get("/theme-assets/theme.css").data
    assert b"mine" in client.get("/theme-assets/theme.js").data
    assert b"theme-assets/theme.css" in client.get("/home").data  # the page head points at it


def test_only_the_three_theme_files_are_served(client):
    assert client.get("/theme-assets/themes.local.toml").status_code == 404
    assert client.get("/theme-assets/../app.py").status_code == 404


@needs_tool
def test_the_caddy_copy_follows_the_render_and_is_a_copy_not_a_link(client, themes_env, tmp_path):
    chrome = tmp_path / "chrome"
    chrome.mkdir()
    site_chrome.publish({}, chrome)
    assert b'data-theme="mine"' not in (chrome / "theme.css").read_bytes()  # the vendored copy
    state = editor_state(client)
    mine = {**state["themes"][0], "name": "mine", "label": "Mine", "based_on": "ember"}
    apply(client, {"themes": [*state["themes"], mine], "active": [*state["active"], "mine"]})
    site_chrome.publish({}, chrome)
    assert b'data-theme="mine"' in (chrome / "theme.css").read_bytes()
    assert not (chrome / "theme.css").is_symlink()  # Caddy cannot read through an owner-only home


@needs_tool
def test_reset_removes_the_layer_and_the_links(client, themes_env):
    state = editor_state(client)
    state["themes"][0]["label"] = "Night"
    apply(client, unchanged(state))
    token = _csrf(client, "/api/themes")
    response = client.post("/api/themes/reset", json={}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert not (themes_env / "themes.local.toml").exists()
    assert not (themes_env / "sites" / "_shared" / "theme.css").is_symlink()
    assert client.post("/api/themes/reset", json={}).status_code == 403


def test_the_tool_is_looked_up_by_environment_then_beside_this_checkout(monkeypatch, tmp_path):
    fake = tmp_path / "tc" / "scripts"
    fake.mkdir(parents=True)
    (fake / "portfolio_themes.py").write_text("")
    monkeypatch.setenv(themes_admin.ENV, str(tmp_path / "tc"))
    assert themes_admin.tool() == fake / "portfolio_themes.py"
    monkeypatch.setenv(themes_admin.ENV, str(tmp_path / "missing"))
    assert themes_admin.tool() is None
    with pytest.raises(themes_admin.ThemesError):
        themes_admin.state()


def test_asset_falls_back_to_the_vendored_copy_and_knows_only_three_names(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert themes_admin.asset("theme.css") == themes_admin.VENDORED["theme.css"]
    rendered = tmp_path / "traction-control" / "themes" / "rendered"
    rendered.mkdir(parents=True)
    (rendered / "theme.css").write_text("/* mine */")
    assert themes_admin.asset("theme.css") == rendered / "theme.css"
    assert themes_admin.asset("chrome.js") == themes_admin.VENDORED["chrome.js"]  # not rendered yet
    assert themes_admin.asset("secrets.txt") is None


@needs_tool
def test_the_shared_font_and_size_round_trip_and_a_bad_one_is_refused(client, themes_env):
    html = client.get("/themes").get_data(as_text=True)
    assert 'id="type-font"' in html and 'id="type-size"' in html
    state = editor_state(client)
    assert state["type"] == {"font": "system", "size": "medium"}
    assert [item["name"] for item in state["sizes"]] == ["tiny", "small", "medium", "large"]
    assert {face["name"] for face in state["fonts"]} == {"system", "serif", "mono"}
    desired = {**unchanged(state), "palette": state["palette"], "emoji": state["emoji"]}
    desired["type"] = {"font": "serif", "size": "large"}
    assert apply(client, desired).status_code == 200
    assert editor_state(client)["type"] == {"font": "serif", "size": "large"}
    css = client.get("/theme-assets/theme.css").data
    assert b"Iowan Old Style" in css and b"zoom: 1.25;" in css and b"--font-size: 14px" in css
    local = (themes_env / "themes.local.toml").read_text(encoding="utf-8")
    assert 'font = "serif"' in local and 'size = "large"' in local
    tracked = (SIBLING / "themes" / "themes.toml").read_text(encoding="utf-8")
    assert 'font = "system"' in tracked and 'size = "medium"' in tracked
    refused = apply(client, {**desired, "type": {"font": "comic sans", "size": "huge"}})
    assert refused.status_code == 400 and not refused.get_json()["ok"]
    refused_size = apply(client, {**desired, "type": {"font": "serif", "size": 13}})
    assert refused_size.status_code == 400 and not refused_size.get_json()["ok"]
    assert editor_state(client)["type"] == {"font": "serif", "size": "large"}


def test_the_home_page_has_a_themes_card(client):
    assert b'href="/themes"' in client.get("/home").data


def test_the_services_menu_links_the_themes_page(client):
    items = client.get("/api/site-nav").get_json()["items"]
    menu = next(i for i in items if i["label"] == "Services")["menu"]
    entry = next(e for e in menu if e.get("label") == "Themes")
    assert entry["icon"] == "🎨" and entry["url"].endswith("/themes")
    assert 'href="/themes"' in client.get("/themes").get_data(as_text=True)


# -- names and icons for cards, menu entries and top-bar items -----------------


def write_labels(config: Path, labels: dict) -> None:
    rendered = config / "traction-control" / "themes" / "rendered"
    rendered.mkdir(parents=True, exist_ok=True)
    (rendered / "labels.json").write_text(json.dumps(labels), encoding="utf-8")


def test_labels_are_read_from_the_rendered_file_and_cached_by_its_time(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert themes_admin.labels() == {}  # no file
    write_labels(tmp_path, {"/groceries": {"name": "Pantry", "icon": "🥫"}, "bad": "x"})
    assert themes_admin.labels() == {"/groceries": {"name": "Pantry", "icon": "🥫"}}
    path = tmp_path / "traction-control" / "themes" / "rendered" / "labels.json"
    path.write_text("{ not json")
    assert themes_admin.labels() == {}  # unreadable means no overrides, never an error page


def test_menus_and_the_published_nav_apply_your_names_and_icons(client):
    labels = {
        "nav:lists": {"name": "Boards", "icon": "📋"},
        "/groceries": {"name": "Pantry", "icon": "🥫"},
        "/home": {"name": "Everything"},
    }
    menus = site_chrome.site_menus(labels)
    assert (menus["lists"]["label"], menus["lists"]["icon"]) == ("Boards", "📋")
    first = menus["lists"]["entries"][0]
    assert (first["path"], first["label"], first["icon"]) == ("/groceries", "Pantry", "🥫")
    assert menus["media"]["label"] == "Media"  # untouched
    nav = site_chrome.build_nav("https://c.example/", [], labels)
    lists = next(i for i in nav["items"] if i["label"] == "Boards")
    assert lists["menu"][0] == {
        "label": "Pantry",
        "icon": "🥫",
        "url": "https://c.example/groceries",
    }
    services = next(i for i in nav["items"] if i.get("align") == "right")["menu"]
    assert any(e["label"] == "Everything" for e in services)
    assert site_chrome.build_nav("https://c.example/", []) == site_chrome.build_nav(
        "https://c.example/", [], {}
    )


def test_prefix_pages_and_all_services_are_flagged_for_the_template():
    entries = {
        e["path"]: e
        for m in ("lists", "media", "services")
        for e in site_chrome.site_menus()[m]["entries"]
    }
    assert entries["/library"]["prefix"] and entries["/groceries"]["prefix"]
    assert not entries["/to-do"]["prefix"]
    assert entries["/home"]["mark"] is False and entries["/to-do"]["mark"] is True


def test_the_server_rendered_top_bar_and_home_cards_show_your_labels(client, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    before = client.get("/home").get_data(as_text=True)
    assert "Groceries" in before
    write_labels(
        tmp_path,
        {
            "/groceries": {"name": "Pantry", "icon": "🥫"},
            "nav:media": {"name": "Watch"},
            "nav:home": {"icon": "🏡"},
        },
    )
    html = client.get("/home").get_data(as_text=True)
    assert "🥫 Pantry" in html  # the menu entry and the Home card share the path's label
    assert "<span>Watch</span>" in html
    assert "🏡" in html
    assert ">Groceries<" not in html.split("<main")[0]  # the top bar no longer says Groceries


def test_a_registry_service_is_renamed_by_its_name(client, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    registry = tmp_path / "wh"
    registry.mkdir()
    (registry / "services.toml").write_text(
        '[[services]]\nname = "demo-svc"\ndescription = "Demo service"\nhostname = "demo.internal"\n'
        'owner_repo = "clockwork"\naccess_mode = "shared-mtls"\nport = 9\n'
    )
    monkeypatch.setattr(web_app, "WIRING_HARNESS_DIR", registry)
    with web_app.app.test_request_context("/home"):
        cards = {c["name"]: c for _, cs in web_app._load_portal_groups() for c in cs}
    assert cards["demo-svc"]["display"] == "Demo service"
    write_labels(tmp_path / "cfg", {"demo-svc": {"name": "Renamed", "icon": "🧪"}})
    with web_app.app.test_request_context("/home"):
        cards = {c["name"]: c for _, cs in web_app._load_portal_groups() for c in cs}
        defaults = {
            c["name"]: c for _, cs in web_app._load_portal_groups(apply_labels=False) for c in cs
        }
    assert (cards["demo-svc"]["display"], cards["demo-svc"]["icon"]) == ("Renamed", "🧪")
    assert defaults["demo-svc"]["display"] == "Demo service"


def test_the_catalog_lists_every_renameable_thing_once_with_its_defaults(client):
    with web_app.app.test_request_context("/themes"):
        catalog = web_app._label_catalog()
    ids = [item["id"] for item in catalog]
    assert len(ids) == len(set(ids))
    assert {
        "nav:home",
        "nav:lists",
        "/groceries",
        "/to-code",
        "/schedule",
        "/themes",
        "/home",
    } <= set(ids)
    top = next(i for i in catalog if i["id"] == "nav:lists")
    assert (top["name"], top["icon"], top["group"]) == ("Lists", "✅", "Top bar")


@needs_tool
def test_saving_labels_goes_through_the_tool_and_shows_up_in_pages_and_the_published_nav(
    client, themes_env
):
    data = client.get("/api/themes").get_json()
    assert any(i["id"] == "/groceries" for i in data["catalog"])
    desired = unchanged(data["state"])
    desired["labels"] = {
        "/groceries": {"name": "Pantry", "icon": "🥫"},
        "nav:lists": {"name": "Boards"},
    }
    response = apply(client, desired)
    assert (
        response.status_code == 200 and response.get_json()["state"]["labels"] == desired["labels"]
    )
    assert (
        stat.S_IMODE((themes_env / "themes" / "rendered" / "labels.json").stat().st_mode) == 0o600
    )
    html = client.get("/home").get_data(as_text=True)
    assert "🥫 Pantry" in html and "<span>Boards</span>" in html
    nav = client.get("/api/site-nav").get_json()
    assert any(item["label"] == "Boards" for item in nav["items"])
    token = _csrf(client, "/api/themes")
    assert (
        client.post("/api/themes/reset", json={}, headers={"X-CSRF-Token": token}).status_code
        == 200
    )
    assert "🥫 Pantry" not in client.get("/home").get_data(as_text=True)


@needs_tool
@pytest.mark.parametrize(
    "labels",
    [
        {"/a": {"name": "<b>x</b>"}},
        {"/a": {"icon": "ab"}},
        {"Bad Id": {"name": "x"}},
        {"/a": {"name": "x", "url": "//evil"}},
    ],
)
def test_a_bad_label_is_refused_and_nothing_is_saved(client, themes_env, labels):
    desired = unchanged(editor_state(client))
    desired["labels"] = labels
    response = apply(client, desired)
    assert response.status_code == 400 and response.get_json()["error"]
    assert not (themes_env / "themes.local.toml").exists()


def test_the_page_has_the_names_and_icons_section(client):
    html = client.get("/themes").get_data(as_text=True)
    assert 'id="labels"' in html and 'id="labels-filter"' in html and 'id="emoji-list"' in html
