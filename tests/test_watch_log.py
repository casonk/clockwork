"""The viewer's watch log: what counts as watched, the log file, and the To Watch badge."""

from __future__ import annotations

import datetime
import json

import pytest

from tests.test_library_pages import _csrf, client, library, web_app  # noqa: F401
from web import watch_log

EP = "/shows/Breaking Bad (2008)/Season 01/Breaking_Bad_S01E01_1080p.mkv"
NOW = datetime.datetime(2026, 10, 31, 12, 0, 0)


def test_parse_episode_and_normalize_title():
    assert watch_log.parse_episode("Show.Name.S02E10.1080p.mkv") == (2, 10)
    assert watch_log.parse_episode("show 1x05.mp4") == (1, 5)
    assert watch_log.parse_episode("Seven_1995_1080p.mp4") is None
    assert watch_log.normalize_title("Napoleon Dynamite (2004)") == "napoleon dynamite"
    assert watch_log.normalize_title("Rental  Family") == "rental family"


def test_record_counts_only_finished_long_enough_new_views(tmp_path):
    log = tmp_path / "w.jsonl"
    kwargs = dict(rel="shows/A/S01E01.mkv", title="A (2020)", filename="A.S01E01.mkv", now=NOW)
    assert watch_log.record(log, position=500, duration=1000, **kwargs) is None  # 50%
    assert watch_log.record(log, position=40, duration=60, **kwargs) is None  # a clip
    assert not log.exists()
    event = watch_log.record(log, position=950, duration=1000, **kwargs)
    assert (event["kind"], event["season"], event["episode"], event["source"]) == (
        "episode",
        1,
        1,
        "viewer",
    )
    # the 90% mark and then the end of the same view log once
    assert watch_log.record(log, position=1000, duration=1000, **kwargs) is None
    later = NOW + datetime.timedelta(hours=4)
    assert (
        watch_log.record(log, position=1000, duration=1000, **{**kwargs, "now": later}) is not None
    )
    assert len(watch_log.read(log)) == 2


def test_a_movie_has_no_episode_and_a_bad_log_line_is_skipped(tmp_path):
    log = tmp_path / "w.jsonl"
    event = watch_log.record(
        log,
        rel="m/Seven.mp4",
        title="Seven (1995)",
        filename="Seven.mp4",
        position=6000,
        duration=6300,
        now=NOW,
    )
    assert event["kind"] == "movie" and "season" not in event
    with log.open("a") as fh:
        fh.write("not json\n")
    assert len(watch_log.read(log)) == 1
    assert watch_log.read(tmp_path / "missing.jsonl") == []


def test_stats_link_by_title_and_badge_reads_naturally(tmp_path):
    log = tmp_path / "w.jsonl"
    for number, hours in ((1, 0), (2, 1), (2, 5)):
        watch_log.record(
            log,
            rel=f"s/Show/S01E0{number}.mkv",
            title="Show (2020)",
            filename=f"S01E0{number}.mkv",
            position=1000,
            duration=1000,
            now=NOW + datetime.timedelta(hours=hours),
        )
    row = watch_log.stats_by_title(watch_log.read(log))["show"]
    assert row["views"] == 3 and len(row["episodes"]) == 2
    assert (
        watch_log.badge(row, NOW + datetime.timedelta(days=2, hours=6))
        == "▶ 2 episodes · last S01E02 · 2 days ago"
    )
    movie = {"views": 1, "episodes": set(), "last": {"kind": "movie", "ts": NOW.isoformat()}}
    assert watch_log.badge(movie, NOW + datetime.timedelta(minutes=30)) == "▶ watched · 30 min ago"


def test_the_viewer_reports_a_watched_episode_and_to_watch_shows_it(
    client, library, tmp_path, monkeypatch
):  # noqa: F811
    log = tmp_path / "viewing.jsonl"
    monkeypatch.setenv("CLOCKWORK_WATCH_LOG_FILE", str(log))
    watch_list = tmp_path / "to-watch.json"
    watch_list.write_text(
        json.dumps(
            {
                "categories": [
                    {"name": "TV Shows", "items": [{"title": "breaking bad", "watched": False}]}
                ]
            }
        )
    )
    monkeypatch.setattr(web_app, "WATCH_LIST_FILE", watch_list)
    headers = {"X-CSRF-Token": _csrf(client)}

    body = {"position": 1400, "duration": 1500}
    assert client.post("/library/play" + EP + "/watched", json=body).status_code == 403  # no token
    first = client.post("/library/play" + EP + "/watched", json=body, headers=headers)
    assert first.get_json() == {"logged": True}
    assert client.post(
        "/library/play" + EP + "/watched", json=body, headers=headers
    ).get_json() == {"logged": False}
    (event,) = watch_log.read(log)
    assert (event["title"], event["season"], event["episode"]) == ("Breaking Bad (2008)", 1, 1)

    page = client.get("/to-watch").get_data(as_text=True)
    assert "▶ 1 episode · last S01E01" in page


def test_the_report_rejects_unknown_files_and_bad_numbers(client, library, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CLOCKWORK_WATCH_LOG_FILE", str(tmp_path / "w.jsonl"))
    headers = {"X-CSRF-Token": _csrf(client)}
    assert (
        client.post("/library/play/nope.mkv/watched", json={}, headers=headers).status_code == 404
    )
    bad = client.post(
        "/library/play" + EP + "/watched", json={"position": "x", "duration": 1}, headers=headers
    )
    assert bad.status_code == 400
    assert not (tmp_path / "w.jsonl").exists()


def test_the_play_page_wires_the_report(client, library):  # noqa: F811
    html = client.get("/library/play" + EP).get_data(as_text=True)
    import re

    assert re.search(
        r'fetch\("/library/play/shows/[^"]*Breaking_Bad_S01E01_1080p\.mkv/watched"', html
    )
    assert 'addEventListener("ended", report)' in html


@pytest.mark.parametrize("name", ["config/watch-log.jsonl"])
def test_the_watch_log_is_gitignored(name):
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", name])
    assert result.returncode == 0, "personal viewing data must not be committable"
