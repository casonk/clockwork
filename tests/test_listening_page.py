"""The /listening page and /api/listening render tape-deck's summary.json and fail soft."""

from __future__ import annotations

import json

import pytest

from web import app as web_app

SUMMARY = {
    "generated": "2026-11-06T13:00:00Z",
    "plays": 120,
    "first": "2021-05-01T12:30:00Z",
    "last": "2026-11-06T12:00:00Z",
    "sources": {"export": 100, "live": 15, "export+live": 5},
    "windows": {
        "all": {
            "plays": 118,
            "hours": 410.5,
            "tracks": [
                {"name": "Song <One>", "artist": "Band", "plays": 9},
                {"name": "Two", "artist": "Duo", "plays": 3},
            ],
            "artists": [{"name": "Band", "plays": 30}, {"name": "Duo", "plays": 15}],
            "albums": [{"name": "LP", "artist": "Band", "plays": 12}],
        },
        "365d": {"plays": 40, "hours": 130.0, "tracks": [], "artists": [], "albums": []},
        "30d": {"plays": 7, "hours": 22.5, "tracks": [], "artists": [], "albums": []},
    },
    "by_hour": [0] * 8 + [5, 9, 3] + [0] * 13,
    "by_weekday": [10, 20, 30, 40, 50, 60, 70],
    "by_month": {"2026-09": 10, "2026-10": 30, "2026-11": 5},
    "recent": [
        {"played_at": "2026-11-06T12:00:00Z", "name": "Latest", "artist": "Band", "album": "LP"}
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(SUMMARY))
    monkeypatch.setattr(web_app, "LISTENING_FILE", path)
    web_app.app.config["TESTING"] = True
    client = web_app.app.test_client()
    client.summary_path = path
    return client


def test_page_shows_the_top_lists_and_escapes_names(client):
    html = client.get("/listening").get_data(as_text=True)
    assert "Top artists" in html and "Top tracks" in html and "Top albums" in html
    assert "Song &lt;One&gt;" in html and "Song <One>" not in html
    assert "410.5" in html and "since 2021-05-01" in html and "Latest" in html


def test_windows_switch_the_numbers(client):
    html = client.get("/listening?window=30d").get_data(as_text=True)
    assert "22.5" in html and "410.5" not in html
    assert 'aria-current="page">Last 30 days' in html


def test_an_unknown_window_falls_back_to_all_time(client):
    assert "410.5" in client.get("/listening?window=bogus").get_data(as_text=True)


def test_bars_scale_to_the_largest_value(client):
    view = web_app._listening_view(SUMMARY, "all")
    assert [b["pct"] for b in view["weekdays"]][-1] == 100.0
    assert view["artists"][0]["pct"] == 100.0 and view["artists"][1]["pct"] == 50.0
    assert max(b["pct"] for b in view["hours_of_day"]) == 100.0


def test_charts_have_a_table_view_and_labels(client):
    html = client.get("/listening").get_data(as_text=True)
    assert html.count('class="table-view"') == 2
    assert 'aria-label="Plays by hour of day"' in html


def test_missing_summary_explains_how_to_make_one(client):
    client.summary_path.unlink()
    response = client.get("/listening")
    assert response.status_code == 200
    assert "tape-deck poll --summary" in response.get_data(as_text=True)
    assert client.get("/api/listening").status_code == 503


def test_a_corrupt_summary_fails_soft(client):
    client.summary_path.write_text("{not json")
    assert "could not be read" in client.get("/listening").get_data(as_text=True)
    client.summary_path.write_text(json.dumps({"hello": 1}))
    assert "not a tape-deck summary" in client.get("/listening").get_data(as_text=True)


def test_the_refresh_note_carries_the_summary_time_and_the_script_parses(client, tmp_path):
    import re
    import subprocess

    html = client.get("/listening").get_data(as_text=True)
    assert 'id="refresh-note" data-generated="2026-11-06T13:00:00Z"' in html
    assert "polls every 30 minutes" in html
    script = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    path = tmp_path / "refresh_check.js"
    path.write_text(script)
    try:
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    except FileNotFoundError:
        pytest.skip("node is not installed")
    assert result.returncode == 0, result.stderr


def test_the_error_page_has_no_refresh_script(client):
    client.summary_path.unlink()
    assert 'id="refresh-note"' not in client.get("/listening").get_data(as_text=True)


def test_the_api_returns_the_document(client):
    assert client.get("/api/listening").get_json()["plays"] == 120


def test_the_page_is_in_the_media_menu(client):
    assert "/listening" in client.get("/listening").get_data(as_text=True)
