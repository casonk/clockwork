"""Entry dates and a staleness rank for the To Code backlog.

New items are stamped with ``added`` when they are created. Older items have no
stamp, so a date is derived without rewriting the backlog: the ``add`` event in
the history log, then the ``[manual:YYYY-MM-DD]`` prefix some tools put in the
title. An item with neither is undated and ranks as neither new nor stale.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

STALE_DAYS = 30
SORTS = ("priority", "newest", "oldest")

_PREFIX = re.compile(r"^\[manual:(\d{4}-\d{2}-\d{2})\]")


def parse_day(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def history_dates(events: list[dict[str, Any]]) -> dict[tuple[str, str], dt.date]:
    """The first ``add`` date per (category, title) from the history log."""
    found: dict[tuple[str, str], dt.date] = {}
    for event in events:
        if event.get("action") != "add":
            continue
        day = parse_day(event.get("ts"))
        key = (str(event.get("category", "")), str(event.get("item", "")))
        if day and key not in found:
            found[key] = day
    return found


def added_date(
    item: dict[str, Any], category: str, history: dict[tuple[str, str], dt.date]
) -> dt.date | None:
    stamped = parse_day(item.get("added"))
    if stamped:
        return stamped
    title = str(item.get("title", ""))
    if (category, title) in history:
        return history[(category, title)]
    prefix = _PREFIX.match(title)
    return parse_day(prefix.group(1)) if prefix else None


def age_days(added: dt.date | None, today: dt.date) -> int | None:
    return None if added is None else max((today - added).days, 0)


def label(days: int | None) -> str:
    if days is None:
        return "undated"
    if days == 0:
        return "today"
    if days < 14:
        return f"{days}d"
    if days < 90:
        return f"{days // 7}w"
    return f"{days // 30}mo"


def is_stale(days: int | None) -> bool:
    return days is not None and days >= STALE_DAYS


def sort_key(sort: str, item: dict[str, Any]) -> tuple:
    """Open items first, then the chosen order; undated items always last."""
    age = item.get("age_days")
    undated = age is None
    base = (bool(item.get("done")),)
    if sort == "newest":
        return (*base, undated, age or 0, item.get("priority") or "P9")
    if sort == "oldest":
        return (*base, undated, -(age or 0), item.get("priority") or "P9")
    return (*base, item.get("priority") or "P9")
