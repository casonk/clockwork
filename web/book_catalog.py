"""Small Open Library lookup for To Read titles."""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def search_books(title: str, year: str = "") -> list[dict]:
    """Return matching works; an unavailable catalog simply yields no matches."""
    if not title.strip():
        return []
    query = urlencode(
        {
            "title": title[:200],
            "fields": "key,title,author_name,first_publish_year,cover_i,edition_count",
            "limit": 10,
        }
    )
    request = Request(
        f"https://openlibrary.org/search.json?{query}",
        headers={"User-Agent": "clockwork/1.0 (personal reading list)"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("docs"), list):
        return []
    wanted = _normalized(title)
    results = []
    for doc in payload.get("docs", []):
        if not isinstance(doc, dict) or _normalized(str(doc.get("title") or "")) != wanted:
            continue
        key = str(doc.get("key") or "")
        if not re.fullmatch(r"/?works/OL\d+W", key):
            continue
        published = doc.get("first_publish_year")
        cover = doc.get("cover_i")
        authors = doc.get("author_name")
        results.append(
            {
                "title": str(doc["title"]),
                "authors": (
                    [str(author) for author in authors[:3]] if isinstance(authors, list) else []
                ),
                "year": published if isinstance(published, int) else None,
                "editions": int(doc.get("edition_count") or 0),
                "url": f"https://openlibrary.org/{key.lstrip('/')}",
                "cover_url": (
                    f"https://covers.openlibrary.org/b/id/{cover}-S.jpg"
                    if isinstance(cover, int) and cover > 0
                    else None
                ),
            }
        )
    if year:
        results.sort(key=lambda book: str(book["year"] or "") != year)
    return results[:3]
