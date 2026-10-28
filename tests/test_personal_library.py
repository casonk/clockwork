"""Read/listen library file matching, browsing, and safe playback routes."""

from __future__ import annotations

import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_SPEC = spec_from_file_location("clockwork_web_app_personal_library", _WEB_DIR / "app.py")
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
sys.modules["clockwork_web_app_personal_library"] = web_app
_SPEC.loader.exec_module(web_app)
library = sys.modules[web_app.personal_library.import_name]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    books = tmp_path / "books"
    audio = tmp_path / "audio"
    (books / "Novels").mkdir(parents=True)
    (books / "Novels" / "The Dispossessed.epub").write_bytes(b"epub")
    (books / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    (audio / "Kind of Blue").mkdir(parents=True)
    (audio / "Kind of Blue" / "01 - So What.mp3").write_bytes(b"audio1")
    (audio / "Kind of Blue" / "02 - Freddie Freeloader.flac").write_bytes(b"audio2")
    (audio / "Plastic Love.m4a").write_bytes(b"audio3")
    (audio / "Artist" / "Double Album" / "CD1").mkdir(parents=True)
    (audio / "Artist" / "Double Album" / "CD1" / "01 - Opening.mp3").write_bytes(b"audio4")
    monkeypatch.setitem(library.ROOTS, "read", books)
    monkeypatch.setitem(library.ROOTS, "listen", audio)
    web_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return web_app.app.test_client()


def _csrf(client):
    with client.session_transaction() as session:
        session["_csrf_token"] = "token"
    return {"X-CSRF-Token": "token"}


def test_matches_files_and_album_folder(client):
    books = client.post(
        "/api/media/read/check",
        json={"titles": [{"title": "The Dispossessed"}, {"title": "Missing"}]},
        headers=_csrf(client),
    ).get_json()
    audio = client.post(
        "/api/media/listen/check",
        json={
            "titles": [
                {"title": "Kind of Blue"},
                {"title": "Plastic Love"},
                {"title": "Double Album"},
            ]
        },
        headers=_csrf(client),
    ).get_json()

    assert books["available"] is True
    assert books["results"][0]["url"] == "/media/read/view/Novels/The%20Dispossessed.epub"
    assert books["results"][1]["found"] is False
    assert audio["results"][0]["url"] == "/media/listen/play/Kind%20of%20Blue"
    assert audio["results"][1]["url"] == "/media/listen/play/Plastic%20Love.m4a"
    assert audio["results"][2]["url"] == "/media/listen/play/Artist/Double%20Album"


def test_browse_view_and_range_requests(client):
    browse = client.get("/media/read/").get_data(as_text=True)
    epub = client.get("/media/read/view/Novels/The%20Dispossessed.epub").get_data(as_text=True)
    listen = client.get("/media/listen/play/Kind%20of%20Blue").get_data(as_text=True)
    partial = client.get(
        "/media/listen/file/Kind%20of%20Blue/01%20-%20So%20What.mp3",
        headers={"Range": "bytes=0-2"},
    )

    assert "Novels" in browse
    assert "epubjs@0.3.93" in epub and "allowScriptedContent: false" in epub
    assert "Freddie Freeloader" in listen and "audio-player" in listen
    assert partial.status_code == 206 and partial.data == b"aud"


def test_hidden_and_outside_files_are_not_served(client, tmp_path, monkeypatch):
    root = library.ROOTS["read"]
    (root / ".secret.pdf").write_bytes(b"secret")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    (root / "escape.pdf").symlink_to(outside)

    assert client.get("/media/read/file/.secret.pdf").status_code == 404
    assert client.get("/media/read/file/escape.pdf").status_code == 404
    assert client.get("/media/read/file/../outside.pdf").status_code == 404
    assert "secret" not in client.get("/media/read/").get_data(as_text=True)


def test_unconfigured_library_reports_unavailable(client, monkeypatch):
    monkeypatch.setitem(library.ROOTS, "read", None)

    assert "CLOCKWORK_READ_LIBRARY_DIR" in client.get("/media/read/").get_data(as_text=True)
    data = client.post(
        "/api/media/read/check", json={"titles": [{"title": "A book"}]}, headers=_csrf(client)
    ).get_json()
    assert data == {"available": False, "results": [{"found": False, "match": None, "url": None}]}
    assert (
        client.get("/api/torrent-search?q=A%20book&title=A%20book&media_kind=read").status_code
        == 503
    )
    assert (
        client.post(
            "/api/torrent-add",
            json={"magnet": "magnet:?xt=book", "title": "A book", "media_kind": "read"},
            headers=_csrf(client),
        ).status_code
        == 503
    )


def test_download_search_and_add_recheck_local_read_library(client, monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_search_torrents_csv",
        lambda _query: [{"name": "The.Dispossessed.epub", "magnet": "magnet:?xt=book"}],
    )
    sent = []
    monkeypatch.setattr(
        web_app, "_proxy_to_magneto", lambda magnet, host: (sent.append(magnet) or True, "")
    )

    owned = client.get(
        "/api/torrent-search?q=The%20Dispossessed&cat=Books"
        "&title=The%20Dispossessed&media_kind=read"
    ).get_json()["results"][0]
    duplicate = client.post(
        "/api/torrent-add",
        json={
            "magnet": "magnet:?xt=book",
            "title": "The Dispossessed",
            "name": "The.Dispossessed.epub",
            "media_kind": "read",
        },
        headers=_csrf(client),
    )
    assert duplicate.status_code == 409 and sent == []
    missing = client.post(
        "/api/torrent-add",
        json={
            "magnet": "magnet:?xt=other",
            "title": "A Missing Book",
            "name": "A.Missing.Book.epub",
            "media_kind": "read",
        },
        headers=_csrf(client),
    )

    assert owned["library_conflict"]
    assert missing.status_code == 200 and sent == ["magnet:?xt=other"]


def test_book_metadata_endpoint_and_merged_release_sources(client, monkeypatch):
    monkeypatch.setattr(
        web_app.book_catalog,
        "search_books",
        lambda title, year: [{"title": title, "year": 1974, "authors": ["Ursula K. Le Guin"]}],
    )
    monkeypatch.setattr(
        web_app,
        "_search_torrents_csv",
        lambda _query: [{"name": "General", "magnet": "magnet:?xt=urn:btih:ABC", "seeders": 2}],
    )
    monkeypatch.setattr(
        web_app.release_sources,
        "search_prowlarr",
        lambda _query, _kind: [
            {"name": "Duplicate", "magnet": "magnet:?xt=urn:btih:abc", "seeders": 3},
            {
                "name": "Book.epub",
                "magnet": "magnet:?xt=urn:btih:DEF",
                "seeders": 5,
                "source": "Books",
            },
        ],
    )

    metadata = client.get("/api/books/search?title=The%20Dispossessed").get_json()
    releases = client.get(
        "/api/torrent-search?q=A%20book&title=A%20book&media_kind=read"
    ).get_json()["results"]

    assert metadata["results"][0]["authors"] == ["Ursula K. Le Guin"]
    assert [result["name"] for result in releases] == ["Book.epub", "General"]
    assert releases[0]["source"] == "Books"


def test_read_sources_endpoint_marks_local_matches(client, monkeypatch):
    monkeypatch.setattr(
        web_app.read_sources,
        "search_read_sources",
        lambda _title, _category: [
            {
                "source": "Internet Archive",
                "title": "The Dispossessed",
                "url": "https://archive.org/details/example",
            }
        ],
    )

    result = client.get("/api/read-sources?title=The%20Dispossessed&category=Books").get_json()[
        "results"
    ][0]

    assert result["in_library"] is True
    assert result["library_url"] == "/media/read/view/Novels/The%20Dispossessed.epub"
