"""The viewer's watch log: what counts as watched, the log file, and the To Watch badge."""

# The route tests reuse fixtures from test_library_pages as parameters.
# ruff: noqa: F811, F401

from __future__ import annotations

import datetime
import json

import pytest

from tests.test_library_pages import _csrf, client, library, web_app
from web import library_pages, streaming, watch_log

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
):
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


def test_the_report_rejects_unknown_files_and_bad_numbers(client, library, tmp_path, monkeypatch):
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


def test_the_play_page_wires_the_report(client, library, monkeypatch):
    monkeypatch.setattr(
        library_pages.streaming,
        "probe_media",
        lambda _path: streaming.MediaInfo(
            duration=1500,
            container=".mkv",
            video_codec="h264",
            pixel_format="yuv420p",
            audio_codec="aac",
            height=1080,
        ),
    )
    html = client.get("/library/play" + EP).get_data(as_text=True)
    # The URL comes from the live location so autoplay's in-place page swap logs the right episode.
    assert 'window.location.pathname.replace(/\\/$/, "") + "/watched"' in html
    assert 'addEventListener("ended", reportWatched)' in html
    assert "watchSent = false;" in html


@pytest.mark.parametrize("name", ["config/watch-log.jsonl"])
def test_the_watch_log_is_gitignored(name):
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", name])
    assert result.returncode == 0, "personal viewing data must not be committable"


def test_normalize_title_links_release_names_and_articles():
    key = watch_log.normalize_title
    assert key("Joe.Pickett.S01.1080p.BluRay.x265[eztv.re]") == key("Joe Pickett")
    assert key("A Clockwork Orange (1971)") == key("clockwork orange")
    assert key("Rick.and.Morty.S05E01.720p") == "rick and morty"


def test_next_episode_continues_after_the_latest_watched():
    eps = [{"rel": f"s/e{n}", "season": 1, "episode": n} for n in range(1, 5)]
    watched = {
        "s/e1": {"views": 1, "ts": "2026-11-01T10:00:00"},
        "s/e3": {"views": 1, "ts": "2026-11-02T10:00:00"},
    }
    assert watch_log.next_episode(eps, watched)["rel"] == "s/e4"
    assert watch_log.next_episode(eps, {})["rel"] == "s/e1"
    done = {e["rel"]: {"views": 1, "ts": f"2026-11-0{e['episode']}T10:00:00"} for e in eps}
    assert watch_log.next_episode(eps, done) is None


def test_stream_fallback_logs_only_a_stream_that_ran_to_the_end(
    client, library, tmp_path, monkeypatch
):
    log = tmp_path / "w.jsonl"
    monkeypatch.setenv("CLOCKWORK_WATCH_LOG_FILE", str(log))
    library_pages._SEGMENTS_SEEN.clear()
    rel = EP.lstrip("/")
    total = streaming.segment_count(1500)
    # Seeking straight to the end is not a view.
    library_pages._note_segment(rel, total - 1, 1500)
    assert watch_log.read(log) == []
    # Streaming through is.
    for index in range(total):
        library_pages._note_segment(rel, index, 1500)
    (event,) = watch_log.read(log)
    assert (event["source"], event["season"], event["episode"]) == ("stream", 1, 1)
    # A viewer report that already landed makes the fallback a no-op.
    library_pages._note_segment(rel, total - 1, 1500)
    assert len(watch_log.read(log)) == 1


def test_the_player_watches_for_a_newer_build(client, library, monkeypatch):
    monkeypatch.setattr(
        library_pages.streaming,
        "probe_media",
        lambda _path: streaming.MediaInfo(
            duration=1500,
            container=".mkv",
            video_codec="h264",
            pixel_format="yuv420p",
            audio_codec="aac",
            height=1080,
        ),
    )
    build = client.get("/api/build")
    assert build.get_json() == {"build": web_app.BUILD_ID}
    assert build.headers["Cache-Control"] == "no-store"
    html = client.get("/library/play" + EP).get_data(as_text=True)
    assert f'const loaded = "{web_app.BUILD_ID}"' in html  # the build this page was rendered by
    assert 'fetch("/api/build"' in html
    assert "window.location.reload()" in html  # only on the button: never automatically
    assert "setInterval(check" in html
