"""Tests for the library title, playback, browse and manage pages."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_SPEC = spec_from_file_location("clockwork_web_app_library_pages", _WEB_DIR / "app.py")
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
sys.modules["clockwork_web_app_library_pages"] = web_app
_SPEC.loader.exec_module(web_app)

ml = web_app.media_library
pages = sys.modules[web_app.library_pages.import_name]

HD = None


def _touch(path: Path, data: bytes = b"x" * 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class Provider:
    name = "fake"

    def search(self, title, year, kind):
        if title == "Seven":
            return ml.Metadata(
                directors=["David Fincher"],
                cast=["Brad Pitt", "Morgan Freeman"],
                genres=["thriller"],
                imdb_id="tt0114369",
                sources=["fake"],
            )
        return None


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = tmp_path / "watch"
    movie = root / "movies" / "Thriller" / "Seven (1995)"
    _touch(movie / "Seven_1995_1080p_David_Fincher.mp4", bytes(range(256)) * 40)
    _touch(
        movie / "Seven_1995_1080p_David_Fincher.en.srt",
        b"1\r\n00:00:01,000 --> 00:00:02,500\r\nWhat's in the box?\r\n",
    )
    _touch(movie / ".release" / "RARBG.txt")
    season = root / "shows" / "Breaking Bad (2008)" / "Season 01"
    _touch(season / "Breaking_Bad_S01E01_1080p.mkv")
    _touch(season / "Breaking_Bad_S01E02_1080p.mkv")
    _touch(root / "Gran Torino (2008) [1080p]" / "Gran.Torino.mp4")
    db = tmp_path / "lib.sqlite"
    conn = ml.connect(db)
    ml.scan_library(
        root,
        conn,
        providers=[Provider()],
        prober=lambda _p: ml.StreamInfo(width=1920, height=800, video_codec="h264"),
    )
    conn.close()
    monkeypatch.setattr(ml, "LIBRARY_ROOT", root)
    monkeypatch.setattr(ml, "CATALOG_DB", db)
    monkeypatch.setattr(ml, "RENAME_JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setattr(ml, "tracked_names", lambda _root: ({"Gran Torino (2008) [1080p]"}, None))
    return root


@pytest.fixture()
def client(library):
    web_app.app.config["TESTING"] = True
    web_app.app.config["SECRET_KEY"] = "test-secret"
    return web_app.app.test_client()


def _csrf(client) -> str:
    with client.session_transaction() as session:
        session["_csrf_token"] = "token"
    return "token"


def test_pages_share_the_media_library_module():
    assert pages.ml is ml


def test_title_page_shows_metadata_files_and_companions(client):
    resp = client.get("/library/title/movies/Thriller/Seven (1995)")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "David Fincher" in html and "Brad Pitt" in html
    assert "Seven_1995_1080p_David_Fincher.mp4" in html
    assert "💬 en" in html
    assert "/library/play/movies/Thriller/Seven" in html
    assert "/Seven_1995_1080p_David_Fincher.mp4" in html
    assert "RARBG" not in html
    assert 'option value="Thriller" selected' in html


def test_show_page_groups_episodes_by_season(client):
    html = client.get("/library/title/shows/Breaking Bad (2008)").get_data(as_text=True)

    assert "Season 1" in html and "2 episodes" in html
    assert "S01E02" in html
    assert "Genre folder" not in html


def test_paths_outside_or_hidden_are_refused(client):
    assert client.get("/library/title/../../etc").status_code == 404
    assert (
        client.get("/library/media/movies/Thriller/Seven (1995)/.release/RARBG.txt").status_code
        == 404
    )
    assert client.get("/library/browse/nope").status_code == 404
    assert client.get("/library/media/movies").status_code == 404


def test_media_supports_range_requests_and_downloads(client):
    url = "/library/media/movies/Thriller/Seven (1995)/Seven_1995_1080p_David_Fincher.mp4"

    partial = client.get(url, headers={"Range": "bytes=0-9"})
    assert partial.status_code == 206
    assert partial.data == bytes(range(10))

    download = client.get(url + "?download=1")
    assert "attachment" in download.headers["Content-Disposition"]


def test_subtitles_are_served_as_webvtt(client):
    resp = client.get(
        "/library/subtitle/movies/Thriller/Seven (1995)/Seven_1995_1080p_David_Fincher.en.srt"
    )

    assert resp.mimetype == "text/vtt"
    assert resp.get_data(as_text=True) == (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.500\nWhat's in the box?\n"
    )


@pytest.fixture()
def probed(monkeypatch):
    streaming = pages.streaming

    def probe(path):
        if path.suffix == ".mp4":
            return streaming.MediaInfo(5400.0, ".mp4", "h264", "yuv420p", "aac", height=800)
        return streaming.MediaInfo(
            13.0, ".mkv", "hevc", "yuv420p10le", "aac", ((0, "eng"),), height=1080
        )

    monkeypatch.setattr(streaming, "probe_media", probe)
    return streaming


def test_play_page_plays_mp4_directly_and_streams_mkv(client, probed):
    html = client.get(
        "/library/play/movies/Thriller/Seven (1995)/Seven_1995_1080p_David_Fincher.mp4"
    ).get_data(as_text=True)

    assert "<video" in html and 'kind="subtitles"' in html
    assert 'data-direct="/library/media/movies/Thriller/Seven' in html
    assert 'value="original">Original (800p, direct)' in html
    assert 'value="720p">720p' in html and 'value="1080p"' not in html

    mkv = client.get(
        "/library/play/shows/Breaking Bad (2008)/Season 01/Breaking_Bad_S01E01_1080p.mkv"
    ).get_data(as_text=True)
    assert 'data-hls="/library/stream/shows/Breaking' in mkv
    assert "index.m3u8" in mkv and "hls.min.js" in mkv and 'integrity="sha384-' in mkv
    assert "hevc 10-bit → h264" in mkv and "data-direct" not in mkv
    assert 'value="original">Original (1080p)' in mkv and 'value="720p">720p · ~4.2 Mb/s' in mkv
    assert "/sub/0.vtt" in mkv and "eng (embedded)" in mkv
    assert "Breaking Bad (2008)" in mkv


def test_stream_routes_serve_playlist_segments_and_subtitles(client, probed, tmp_path, monkeypatch):
    segment_file = tmp_path / "seg_1.ts"
    segment_file.write_bytes(b"\x47" * 188)
    calls = []

    class FakeManager:
        def session(self, source, quality=None):
            calls.append(("session", quality))
            return probed.Session("k", source, tmp_path, probed.probe_media(source))

        def segment(self, source, index, quality=None):
            calls.append(("segment", index, quality))
            return segment_file if index == 1 else None

    monkeypatch.setattr(probed, "manager", lambda: FakeManager())
    monkeypatch.setattr(
        probed, "subtitle_vtt", lambda _source, index: f"WEBVTT\n\nstream {index}\n"
    )
    base = "/library/stream/shows/Breaking Bad (2008)/Season 01/Breaking_Bad_S01E01_1080p.mkv"

    playlist = client.get(base + "/index.m3u8?q=720p")
    assert playlist.mimetype == "application/vnd.apple.mpegurl"
    assert playlist.get_data(as_text=True).count("seg/") == 3
    assert "seg/0.ts?q=720p" in playlist.get_data(as_text=True)
    assert ("session", "720p") in calls
    assert client.get(base + "/index.m3u8?q=8k").status_code == 404

    segment = client.get(base + "/seg/1.ts")
    assert segment.status_code == 200 and segment.mimetype == "video/mp2t"
    assert client.get(base + "/seg/2.ts").status_code == 503

    assert client.get(base + "/sub/0.vtt").get_data(as_text=True) == "WEBVTT\n\nstream 0\n"
    assert client.get("/library/stream/../../etc/passwd/index.m3u8").status_code == 404
    assert ("segment", 1, "original") in calls
    client.get(base + "/seg/1.ts?q=480p")
    assert ("segment", 1, "480p") in calls


def test_browse_lists_folders_and_hides_release_clutter(client):
    top = client.get("/library/browse/").get_data(as_text=True)
    assert "movies" in top and "shows" in top and "Gran Torino" in top

    title = client.get("/library/browse/movies/Thriller/Seven (1995)").get_data(as_text=True)
    assert "Seven_1995_1080p_David_Fincher.mp4" in title
    assert ".release" not in title
    assert "Details" in title


def test_change_genre_moves_folder_and_keeps_catalog(client, library):
    resp = client.post(
        "/library/title/movies/Thriller/Seven (1995)/genre",
        data={"genre": "Crime", "csrf_token": _csrf(client)},
    )

    assert resp.status_code == 302
    assert "/library/title/movies/Crime/Seven" in resp.headers["Location"]
    assert (
        library / "movies" / "Crime" / "Seven (1995)" / "Seven_1995_1080p_David_Fincher.mp4"
    ).exists()
    assert not (library / "movies" / "Thriller").exists()
    conn = ml.connect(ml.CATALOG_DB)
    row = ml.search_catalog(conn, category="Crime")[0]
    assert row["directors"] == ["David Fincher"]
    files = conn.execute("SELECT path FROM files WHERE folder = ?", (row["folder"],)).fetchall()
    assert files[0]["path"].startswith(str(library / "movies" / "Crime"))
    assert ml.recent_batches(ml.RENAME_JOURNAL)[0]["count"] == 1

    bad = client.post(
        "/library/title/movies/Crime/Seven (1995)/genre",
        data={"genre": "Nope", "csrf_token": _csrf(client)},
    )
    assert bad.status_code == 302
    assert (library / "movies" / "Crime" / "Seven (1995)").exists()


def test_manage_page_lists_attention_and_starts_sync(client, monkeypatch):
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        stdout = "ActiveState=inactive\nResult=success\n" if "show" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(pages.subprocess, "run", fake_run)

    html = client.get("/library/manage").get_data(as_text=True)
    assert "still seeding" in html
    assert "Gran Torino" in html
    assert "Breaking Bad" in html  # no metadata from the fake provider

    resp = client.post("/library/manage/sync", data={"csrf_token": _csrf(client)})
    assert resp.status_code == 302
    assert [
        "systemctl",
        "--user",
        "start",
        "--no-block",
        "clockwork-media-library.service",
    ] in calls


def test_search_api_links_titles(client):
    data = client.get("/api/library?director=fincher").get_json()
    assert data["items"][0]["path"] == "movies/Thriller/Seven (1995)"
    assert data["facets"]["actors"] == ["Brad Pitt", "Morgan Freeman"]
    assert 'list="actor-list"' in client.get("/library").get_data(as_text=True)


def test_srt_to_vtt_strips_bom():
    assert pages.srt_to_vtt("﻿1\n00:00:01,000 --> 00:00:02,000\nHi\n").startswith(
        "WEBVTT\n\n1\n00:00:01.000"
    )


def test_ai_render_appears_in_quality_menu_and_streams(
    client, library, probed, tmp_path, monkeypatch
):
    upscale = pages.upscale
    source = (
        library / "shows" / "Breaking Bad (2008)" / "Season 01" / "Breaking_Bad_S01E01_1080p.mkv"
    )
    rel = "/shows/Breaking Bad (2008)/Season 01/Breaking_Bad_S01E01_1080p.mkv"

    html = client.get("/library/play" + rel).get_data(as_text=True)
    assert "AI 4K" not in html
    assert client.get("/library/stream" + rel + "/index.m3u8?v=ai-4k").status_code == 404

    _touch(upscale.output_path(library, source, 2160))
    html = client.get("/library/play" + rel).get_data(as_text=True)
    assert 'value="ai-4k">✨ AI 4K (experimental)' in html
    assert 'value="360p">360p' in html

    sources = []

    class FakeManager:
        def session(self, src, quality=None):
            sources.append(src)
            return probed.Session("k", src, tmp_path, probed.probe_media(src))

    monkeypatch.setattr(probed, "manager", lambda: FakeManager())
    playlist = client.get("/library/stream" + rel + "/index.m3u8?q=original&v=ai-4k")
    assert "seg/0.ts?q=original&v=ai-4k" in playlist.get_data(as_text=True)
    assert sources[-1] == upscale.output_path(library, source, 2160)
    assert client.get("/library/stream" + rel + "/index.m3u8?v=ai-8k").status_code == 404


def test_manage_page_shows_ai_status_and_controls_the_service(client, monkeypatch, tmp_path):
    upscale = pages.upscale
    status = tmp_path / "upscale-status.json"
    upscale.write_status(
        {
            "state": "rendering",
            "title": "Futurama",
            "file": "Futurama_S03E01_SD.mp4",
            "target": "1080p",
            "model": "realesr-animevideov3",
            "frames": 500,
            "total_frames": 2000,
            "fps": 85.0,
            "eta_seconds": 1200,
            "queued": 436,
            "free_gb": 5700,
            "min_free_gb": 1000,
        },
        status,
    )
    monkeypatch.setattr(upscale, "STATUS_FILE", status)
    monkeypatch.setattr(upscale, "read_status", lambda path=status: json.loads(status.read_text()))
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        out = "ActiveState=active\nLoadState=loaded\n" if "show" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(pages.subprocess, "run", fake_run)

    html = client.get("/library/manage").get_data(as_text=True)
    assert "AI super-resolution" in html and "Futurama" in html
    assert "25.0%" in html and "85.0 fps" in html and "436 queued" in html
    assert "⏸ Stop" in html

    resp = client.post("/library/manage/upscale/stop", data={"csrf_token": _csrf(client)})
    assert resp.status_code == 302
    assert ["systemctl", "--user", "stop", "--no-block", "clockwork-upscale.service"] in calls
    assert (
        client.post("/library/manage/upscale/rm", data={"csrf_token": _csrf(client)}).status_code
        == 404
    )
