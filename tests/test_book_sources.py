"""Book metadata and optional Prowlarr release parsing."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
import book_catalog  # noqa: E402
import release_sources  # noqa: E402


def _response(data):
    return io.BytesIO(json.dumps(data).encode())


def test_open_library_exact_title_and_year_order(monkeypatch):
    requested = []

    def urlopen(request, timeout):
        requested.append((request.full_url, timeout))
        return _response(
            {
                "docs": [
                    {
                        "title": "The Dispossessed",
                        "key": "/works/OL1W",
                        "first_publish_year": 1974,
                        "author_name": ["Ursula K. Le Guin"],
                        "cover_i": 12,
                    },
                    {"title": "The Dispossessed", "key": "/works/OL2W", "first_publish_year": 2000},
                    {"title": "The Dispossessed: A Guide", "key": "/works/OL3W"},
                    {"title": "The Dispossessed", "key": "https://outside.example/works/OL4W"},
                ]
            }
        )

    monkeypatch.setattr(book_catalog, "urlopen", urlopen)
    books = book_catalog.search_books("The Dispossessed", "2000")

    assert [book["url"] for book in books] == [
        "https://openlibrary.org/works/OL2W",
        "https://openlibrary.org/works/OL1W",
    ]
    assert books[1]["authors"] == ["Ursula K. Le Guin"]
    assert books[1]["cover_url"] == "https://covers.openlibrary.org/b/id/12-S.jpg"
    assert "title=The+Dispossessed" in requested[0][0] and requested[0][1] == 5


def test_prowlarr_uses_book_query_and_only_magnets(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_PROWLARR_URL", "http://localhost:9696")
    monkeypatch.setenv("CLOCKWORK_PROWLARR_API_KEY", "private-key")
    requested = []

    def urlopen(request, timeout):
        requested.append((request, timeout))
        return _response(
            [
                {
                    "title": "Book.epub",
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc",
                    "seeders": 8,
                    "indexer": "Books",
                    "size": 12,
                },
                {"title": "File", "protocol": "torrent", "downloadUrl": "https://example/torrent"},
                {"title": "Usenet", "protocol": "usenet", "magnetUrl": "magnet:?xt=urn:btih:def"},
            ]
        )

    monkeypatch.setattr(release_sources, "urlopen", urlopen)
    result = release_sources.search_prowlarr("A Book", "read")

    assert len(result) == 1 and result[0]["source"] == "Books"
    assert "type=book" in requested[0][0].full_url
    assert requested[0][0].get_header("X-api-key") == "private-key"
    assert requested[0][1] == 8


def test_invalid_prowlarr_url_does_not_break_search(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_PROWLARR_URL", "http://[")
    monkeypatch.setenv("CLOCKWORK_PROWLARR_API_KEY", "private-key")
    assert release_sources.search_prowlarr("A Book", "read") == []


def test_piratebay_search_builds_valid_magnets_and_skips_bad_rows(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_PIRATEBAY_SEARCH", "1")
    requested = []

    def urlopen(request, timeout):
        requested.append((request, timeout))
        return _response(
            [
                {
                    "name": "Example.Show.S02.1080p",
                    "info_hash": "A" * 40,
                    "seeders": "12",
                    "leechers": "3",
                    "size": "1000",
                },
                {"name": "Bad", "info_hash": "not-a-hash"},
            ]
        )

    monkeypatch.setattr(release_sources, "urlopen", urlopen)
    results = release_sources.search_piratebay("Example Show S02")

    assert len(results) == 1 and results[0]["source"] == "The Pirate Bay"
    assert results[0]["magnet"].startswith("magnet:?xt=urn:btih:" + "A" * 40)
    assert "cat=200" in requested[0][0].full_url and requested[0][1] == 8
