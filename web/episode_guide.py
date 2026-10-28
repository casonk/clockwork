"""Compare aired TVmaze episodes with playable files in a library show folder."""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

try:
    from web import media_library as ml
except ModuleNotFoundError:
    import media_library as ml


class GuideError(Exception):
    """The external episode guide could not be used."""


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str | None) -> str:
    parser = _PlainText()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


def _fetch_json(path: str) -> object:
    request = urllib.request.Request(
        f"https://api.tvmaze.com{path}",
        headers={"User-Agent": "clockwork-library/1.0 (https://github.com/casonk/clockwork)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GuideError(f"TVmaze is unavailable: {exc}") from exc


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _candidate(show: dict) -> dict:
    premiered = show.get("premiered") or ""
    return {
        "id": show.get("id"),
        "name": show.get("name") or "",
        "year": int(premiered[:4]) if premiered[:4].isdigit() else None,
    }


def find_show(title: str, year: int | None, show_id: int | None = None) -> dict:
    query = urllib.parse.urlencode({"q": title})
    results = _fetch_json(f"/search/shows?{query}")
    if not isinstance(results, list):
        raise GuideError("TVmaze returned an invalid search response.")
    exact = [
        item["show"]
        for item in results
        if isinstance(item, dict)
        and isinstance(item.get("show"), dict)
        and _norm(item["show"].get("name") or "") == _norm(title)
    ]
    if show_id is not None:
        exact = [show for show in exact if show.get("id") == show_id]
    elif year is not None:
        year_matches = [show for show in exact if _candidate(show)["year"] == year]
        if not year_matches and exact:
            return {"ambiguous": True, "candidates": [_candidate(show) for show in exact]}
        exact = year_matches
    if not exact:
        raise GuideError("No exact TVmaze show match was found for this title and year.")
    if len(exact) > 1:
        return {"ambiguous": True, "candidates": [_candidate(show) for show in exact]}
    return exact[0]


def local_episodes(folder: Path) -> set[tuple[int, int]]:
    found: set[tuple[int, int]] = set()
    for video in ml._video_files(folder):
        parsed = ml.parse_release_name(video.name)
        if parsed.season is None or parsed.episode is None:
            continue
        found.update(
            (parsed.season, number)
            for number in range(parsed.episode, (parsed.episode_end or parsed.episode) + 1)
        )
    return found


def episode_guide(
    folder: Path,
    title: str,
    year: int | None,
    *,
    show_id: int | None = None,
    today: dt.date | None = None,
) -> dict:
    """Return known regular episodes with local, missing, new or upcoming status."""
    show = find_show(title, year, show_id)
    if show.get("ambiguous"):
        return show
    show_id = show.get("id")
    if not isinstance(show_id, int):
        raise GuideError("TVmaze returned an invalid show ID.")
    episodes = _fetch_json(f"/shows/{show_id}/episodes")
    if not isinstance(episodes, list):
        raise GuideError("TVmaze returned an invalid episode list.")
    today = today or dt.date.today()
    owned = local_episodes(folder)
    items = []
    counts = {"owned": 0, "missing": 0, "new": 0, "upcoming": 0}
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        season, number = episode.get("season"), episode.get("number")
        if not isinstance(season, int) or not isinstance(number, int) or season < 1 or number < 1:
            continue
        airdate = episode.get("airdate") or ""
        try:
            aired = dt.date.fromisoformat(airdate)
        except ValueError:
            continue
        if (season, number) in owned:
            status = "owned"
        elif aired > today:
            status = "upcoming"
        elif (today - aired).days <= 30:
            status = "new"
        else:
            status = "missing"
        counts[status] += 1
        rating = episode.get("rating") or {}
        items.append(
            {
                "season": season,
                "episode": number,
                "code": f"S{season:02d}E{number:02d}",
                "name": episode.get("name") or "",
                "airdate": airdate,
                "summary": plain_text(episode.get("summary")),
                "rating": rating.get("average") if isinstance(rating, dict) else None,
                "status": status,
            }
        )
    items.sort(key=lambda item: (item["season"], item["episode"]))
    show_rating = show.get("rating") or {}
    return {
        "show": {
            "id": show_id,
            "name": show.get("name") or title,
            "url": show.get("url") or "",
            "summary": plain_text(show.get("summary")),
            "rating": show_rating.get("average") if isinstance(show_rating, dict) else None,
        },
        "counts": counts,
        "episodes": items,
    }
