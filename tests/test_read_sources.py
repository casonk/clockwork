"""arXiv and Internet Archive reading source lookups."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
import read_sources  # noqa: E402


def test_arxiv_title_match_and_pdf_link(monkeypatch):
    atom = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/abs/1706.03762v7</id>
        <title>Attention Is All You Need</title><published>2017-06-12T00:00:00Z</published>
        <summary>Transformer paper.</summary><author><name>A. Vaswani</name></author>
      </entry>
      <entry><id>http://arxiv.org/abs/1706.03763</id><title>Another paper</title></entry>
      <entry><id>http://evil.test/abs/../../secret</id><title>Attention Is All You Need</title></entry>
    </feed>"""
    calls = []

    def urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(atom)

    monkeypatch.setattr(read_sources, "urlopen", urlopen)
    results = read_sources.search_arxiv("Attention Is All You Need")

    assert len(results) == 1
    assert results[0]["download_url"] == "https://arxiv.org/pdf/1706.03762v7"
    assert results[0]["authors"] == ["A. Vaswani"]
    assert "search_query=ti%3A%22Attention+Is+All+You+Need%22" in calls[0][0]


def test_archive_search_links_to_item_access_page(monkeypatch):
    payload = {
        "response": {
            "docs": [
                {
                    "identifier": "prideprejudice00aust",
                    "title": "Pride and Prejudice",
                    "creator": "Jane Austen",
                    "year": "1813",
                },
                {"identifier": "other", "title": "Pride and Prejudice and Zombies"},
                {"identifier": "../bad", "title": "Pride and Prejudice"},
            ]
        }
    }
    monkeypatch.setattr(
        read_sources, "urlopen", lambda _request, timeout: io.BytesIO(json.dumps(payload).encode())
    )
    results = read_sources.search_internet_archive("Pride and Prejudice")

    assert len(results) == 1
    assert results[0]["url"] == "https://archive.org/details/prideprejudice00aust"
    assert results[0]["download_url"] is None
    assert results[0]["authors"] == ["Jane Austen"]


def test_papers_searches_both_sources_and_books_only_archive(monkeypatch):
    read_sources._CACHE.clear()
    calls = []
    monkeypatch.setattr(read_sources, "search_arxiv", lambda title: calls.append("arxiv") or [])
    monkeypatch.setattr(
        read_sources, "search_internet_archive", lambda title: calls.append("archive") or []
    )

    read_sources.search_read_sources("A Paper", "Papers")
    read_sources.search_read_sources("A Book", "Books")

    assert calls == ["arxiv", "archive", "archive"]
