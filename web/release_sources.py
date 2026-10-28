"""Optional Prowlarr search for magnet releases."""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def search_prowlarr(query: str, kind: str, limit: int = 20) -> list[dict]:
    """Search configured torrent indexers and ignore non-magnet results."""
    base = os.environ.get("CLOCKWORK_PROWLARR_URL", "").rstrip("/")
    api_key = os.environ.get("CLOCKWORK_PROWLARR_API_KEY", "")
    try:
        parsed = urlsplit(base)
        host = parsed.hostname
    except ValueError:
        return []
    if (
        not base
        or not api_key
        or parsed.scheme not in ("http", "https")
        or not host
        or parsed.username
        or parsed.password
        or kind not in ("read", "listen")
    ):
        return []
    params = urlencode(
        {"query": query[:200], "indexerIds": -2, "type": "book" if kind == "read" else "music"}
    )
    request = Request(
        f"{base}/api/v1/search?{params}",
        headers={"X-Api-Key": api_key, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.load(response)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    results = []
    for item in payload:
        if not isinstance(item, dict) or item.get("protocol") != "torrent":
            continue
        magnet = item.get("magnetUrl") or item.get("downloadUrl") or ""
        if not isinstance(magnet, str) or not magnet.startswith("magnet:?xt=urn:btih:"):
            continue
        name = str(item.get("title") or "").strip()
        if not name:
            continue
        try:
            seeders = max(0, int(item.get("seeders") or 0))
            leechers = max(0, int(item.get("leechers") or 0))
            size = max(0, int(item.get("size") or 0))
        except (TypeError, ValueError):
            continue
        results.append(
            {
                "name": name,
                "magnet": magnet,
                "seeders": seeders,
                "leechers": leechers,
                "size_bytes": size,
                "quality": "",
                "source": str(item.get("indexer") or "Prowlarr"),
            }
        )
    return sorted(results, key=lambda result: result["seeders"], reverse=True)[:limit]
