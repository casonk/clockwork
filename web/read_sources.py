"""Open reading sources for To Read titles."""

from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?\Z")
_ARCHIVE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,99}\Z")
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_CACHE_LOCK = threading.Lock()


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _title_matches(wanted: str, found: str) -> bool:
    actual = _normalized(found)
    expected = _normalized(wanted)
    return bool(expected and actual == expected)


def _cached(provider: str, title: str, fetch) -> list[dict]:
    key = (provider, title.casefold().strip())
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
    results = fetch(title)
    with _CACHE_LOCK:
        if len(_CACHE) >= 256:
            _CACHE.clear()
        lifetime = (86400 if provider == "arxiv" else 1800) if results else 120
        _CACHE[key] = (time.monotonic() + lifetime, results)
    return results


def search_arxiv(title: str) -> list[dict]:
    """Find matching paper titles in the official arXiv Atom API."""
    if not title.strip():
        return []
    phrase = title[:200].replace('"', " ").strip()
    params = urlencode({"search_query": f'ti:"{phrase}"', "start": 0, "max_results": 10})
    request = Request(
        f"https://export.arxiv.org/api/query?{params}",
        headers={"User-Agent": "clockwork/1.0 (personal reading list)"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            root = ET.fromstring(response.read(2_000_000))
    except (OSError, ValueError, ET.ParseError):
        return []
    results = []
    for entry in root.findall(f"{_ATOM}entry"):
        found_title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        url = (entry.findtext(f"{_ATOM}id") or "").strip()
        identifier = url.rsplit("/abs/", 1)[-1]
        if not _title_matches(title, found_title) or not _ARXIV_ID.fullmatch(identifier):
            continue
        published = entry.findtext(f"{_ATOM}published") or ""
        authors = [
            (author.findtext(f"{_ATOM}name") or "").strip()
            for author in entry.findall(f"{_ATOM}author")[:3]
        ]
        results.append(
            {
                "source": "arXiv",
                "identifier": identifier,
                "title": found_title,
                "authors": [author for author in authors if author],
                "year": published[:4] if re.match(r"\d{4}-", published) else None,
                "url": f"https://arxiv.org/abs/{identifier}",
                "download_url": f"https://arxiv.org/pdf/{identifier}",
                "summary": " ".join((entry.findtext(f"{_ATOM}summary") or "").split())[:400],
            }
        )
        if len(results) == 5:
            break
    return results


def search_internet_archive(title: str) -> list[dict]:
    """Find matching Archive.org text items; the item page governs access."""
    if not title.strip():
        return []
    phrase = title[:200].replace('"', " ").replace("\\", " ").strip()
    params = urlencode(
        {
            "q": f'mediatype:texts AND title:"{phrase}"',
            "fl[]": ["identifier", "title", "creator", "year", "date"],
            "rows": 15,
            "sort[]": "downloads desc",
            "output": "json",
        },
        doseq=True,
    )
    request = Request(
        f"https://archive.org/advancedsearch.php?{params}",
        headers={"User-Agent": "clockwork/1.0 (personal reading list)"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.load(response)
    except (OSError, ValueError, TypeError):
        return []
    response = payload.get("response") if isinstance(payload, dict) else None
    docs = response.get("docs", []) if isinstance(response, dict) else []
    if not isinstance(docs, list):
        return []
    results = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        identifier = str(doc.get("identifier") or "")
        found_title = str(doc.get("title") or "").strip()
        if not _ARCHIVE_ID.fullmatch(identifier) or not _title_matches(title, found_title):
            continue
        creator = doc.get("creator") or []
        if isinstance(creator, str):
            creator = [creator]
        published = str(doc.get("year") or doc.get("date") or "")
        results.append(
            {
                "source": "Internet Archive",
                "identifier": identifier,
                "title": found_title,
                "authors": (
                    [str(author) for author in creator[:3]] if isinstance(creator, list) else []
                ),
                "year": published[:4] if re.match(r"\d{4}", published) else None,
                "url": f"https://archive.org/details/{identifier}",
                "download_url": None,
                "summary": "Open the item to see its reading, borrowing, and download options.",
            }
        )
        if len(results) == 5:
            break
    return results


def search_read_sources(title: str, category: str) -> list[dict]:
    results = []
    if category.casefold() in ("papers", "articles"):
        results.extend(_cached("arxiv", title, search_arxiv))
    results.extend(_cached("archive", title, search_internet_archive))
    return results
