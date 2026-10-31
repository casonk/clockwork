"""A log of what was watched in the library viewer, linked to the To Watch list.

The viewer reports when a video has been (nearly) watched; this module records it as one JSON
line in ``config/watch-log.jsonl`` (personal viewing data: gitignored, ``CLOCKWORK_WATCH_LOG_FILE``
moves it) and turns the log into per-title statistics the To Watch page shows beside each item.
Titles are linked by name, so a library folder "Show Name (2020)" matches a list item "show name".
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Any

WATCHED_FRACTION = 0.9  # a video counts as watched once this much of it has played
MIN_DURATION_SECONDS = 120  # ignore trailers and clips
DEDUPE_SECONDS = 3 * 3600  # the same file is logged at most once per window (90% mark, then end)
_EPISODE = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})|\b(\d{1,2})x(\d{2,3})\b")
_YEAR = re.compile(r"[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]")


def default_file() -> Path:
    base = Path(__file__).resolve().parent.parent
    return Path(os.environ.get("CLOCKWORK_WATCH_LOG_FILE", base / "config" / "watch-log.jsonl"))


def normalize_title(name: str) -> str:
    """A comparison key: no year, no punctuation, lower case."""
    return re.sub(r"[^a-z0-9]+", " ", _YEAR.sub("", name).lower()).strip()


def parse_episode(filename: str) -> tuple[int, int] | None:
    match = _EPISODE.search(filename)
    if not match:
        return None
    season, episode = (
        (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    )
    return int(season), int(episode)


def read(path: Path | None = None) -> list[dict[str, Any]]:
    """Every logged event, oldest first; unreadable lines are skipped."""
    path = path or default_file()
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def record(
    path: Path,
    *,
    rel: str,
    title: str,
    filename: str,
    position: float,
    duration: float,
    season: int | None = None,
    episode: int | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any] | None:
    """Log a finished view; None when it does not count (too short, not far enough, or a repeat)."""
    now = now or datetime.datetime.now()
    if not (duration >= MIN_DURATION_SECONDS) or position < 0:
        return None
    fraction = min(position / duration, 1.0)
    if fraction < WATCHED_FRACTION:
        return None
    for earlier in reversed(read(path)):
        if earlier.get("rel") != rel:
            continue
        try:
            when = datetime.datetime.fromisoformat(str(earlier["ts"]))
        except (KeyError, ValueError):
            break
        if (now - when).total_seconds() < DEDUPE_SECONDS:
            return None
        break
    if season is None or episode is None:
        parsed = parse_episode(filename)
        season, episode = parsed if parsed else (None, None)
    event: dict[str, Any] = {
        "ts": now.isoformat(timespec="seconds"),
        "event": "watched",
        "kind": "episode" if season is not None and episode is not None else "movie",
        "title": title,
        "rel": rel,
        "fraction": round(fraction, 3),
        "duration": round(duration),
        "source": "viewer",
    }
    if event["kind"] == "episode":
        event["season"], event["episode"] = season, episode
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    return event


def stats_by_title(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per normalised title: how many views, the latest one, and the distinct episodes seen."""
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "watched":
            continue
        key = normalize_title(str(event.get("title", "")))
        if not key:
            continue
        row = out.setdefault(key, {"views": 0, "episodes": set(), "last": None})
        row["views"] += 1
        if event.get("kind") == "episode":
            row["episodes"].add((event.get("season"), event.get("episode")))
        if row["last"] is None or str(event.get("ts")) >= str(row["last"]["ts"]):
            row["last"] = event
    return out


def badge(row: dict[str, Any], now: datetime.datetime | None = None) -> str:
    """One line for the To Watch page: ``▶ 3 episodes · last S01E03 · 2 days ago``."""
    now = now or datetime.datetime.now()
    last = row["last"]
    if last.get("kind") == "episode":
        count = len(row["episodes"])
        what = f"{count} episode{'s' if count != 1 else ''} · last S{last['season']:02d}E{last['episode']:02d}"
    else:
        what = f"watched {row['views']}×" if row["views"] > 1 else "watched"
    try:
        delta = now - datetime.datetime.fromisoformat(str(last["ts"]))
    except ValueError:
        return f"▶ {what}"
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 3600:
        ago = "just now" if seconds < 120 else f"{seconds // 60} min ago"
    elif seconds < 86400:
        ago = f"{seconds // 3600} h ago"
    else:
        days = seconds // 86400
        ago = f"{days} day{'s' if days != 1 else ''} ago"
    return f"▶ {what} · {ago}"
