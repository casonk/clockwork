"""Helpers for normalising systemd status data for the web UI."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ENABLED_STATES = {"enabled", "static", "alias"}
_EMPTY_SYSTEMD_VALUES = {"", "0", "n/a", "[not set]"}
_SYSTEMD_TIMESTAMP_RE = re.compile(
    r"^\S+\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:\s+([^\s]+))?$"
)
_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_DOW_NAMES = {
    "sun": 7,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def parse_show_properties(output: str) -> dict[str, str]:
    """Parse ``systemctl show`` output into a key/value mapping."""
    props: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        props[key.strip()] = value.strip()
    return props


def parse_systemd_timestamp(value: str) -> datetime | None:
    """Parse a ``systemctl show`` wall-clock timestamp into local time."""
    text = normalize_systemd_value(value)
    if not text:
        return None

    match = _SYSTEMD_TIMESTAMP_RE.match(text)
    if not match:
        return None

    tz_token = (match.group(3) or "").upper()
    if tz_token in {"UTC", "GMT"}:
        tz = timezone.utc
    else:
        tz = _local_tzinfo()

    wall_clock = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}")
    return wall_clock.replace(tzinfo=tz)


def build_unit_status(output: str) -> dict[str, str | bool]:
    """Build the status payload rendered by the index template."""
    props = parse_show_properties(output)
    active_state = props.get("ActiveState", "")
    enabled_state = props.get("UnitFileState", "")
    next_run_text = normalize_systemd_value(props.get("NextElapseUSecRealtime", ""))
    next_run = parse_systemd_timestamp(next_run_text)

    return {
        "active": active_state == "active",
        "enabled": enabled_state in _ENABLED_STATES,
        "active_state": active_state,
        "enabled_state": enabled_state,
        "next_run_text": next_run_text,
        "next_run_iso": next_run.isoformat() if next_run else "",
    }


def build_cron_status(
    job: dict[str, object], now: datetime | None = None
) -> dict[str, str | bool | None]:
    """Build a synthetic status payload for cron-backed jobs."""
    cron = job.get("cron")
    if not isinstance(cron, dict):
        return {
            "active": None,
            "enabled": None,
            "active_state": "cron",
            "enabled_state": "cron",
            "next_run_text": "",
            "next_run_iso": "",
        }

    next_run = next_cron_occurrence(
        str(cron.get("expression", "") or ""),
        timezone_name=str(cron.get("timezone", "") or ""),
        now=now,
    )
    return {
        "active": None,
        "enabled": None,
        "active_state": "cron",
        "enabled_state": "cron",
        "next_run_text": _format_schedule_timestamp(next_run),
        "next_run_iso": next_run.isoformat() if next_run else "",
    }


def build_repo_next_run_candidate(
    job: dict[str, object], status: dict[str, str | bool] | None = None
) -> dict[str, str]:
    """Build a repo-level next-run candidate from manifest and live status data."""
    if not job.get("enabled", True):
        return {"job_name": "", "next_run_iso": "", "next_run_text": ""}

    status = status or {}
    startup_label = "boot/login" if str(job.get("scope", "") or "") == "user" else "reboot"
    cadence_text = job_cadence_text(job)
    next_run_iso = str(status.get("next_run_iso", "") or "")
    next_run_text = str(status.get("next_run_text", "") or "")
    if next_run_iso:
        return {
            "job_name": str(job.get("name", "") or ""),
            "next_run_iso": next_run_iso,
            "next_run_text": next_run_text,
            "cadence_text": cadence_text,
        }

    timer = job.get("timer")
    if isinstance(timer, dict) and timer.get("kind") == "interval":
        on_boot_sec = str(timer.get("on_boot_sec", "") or "").strip()
        if on_boot_sec:
            return {
                "job_name": str(job.get("name", "") or ""),
                "next_run_iso": "",
                "next_run_text": f"{startup_label} (+{on_boot_sec})",
                "cadence_text": cadence_text,
            }

    if not job.get("timer") and not job.get("cron"):
        return {
            "job_name": str(job.get("name", "") or ""),
            "next_run_iso": "",
            "next_run_text": startup_label,
            "cadence_text": cadence_text,
        }

    return {"job_name": "", "next_run_iso": "", "next_run_text": "", "cadence_text": cadence_text}


def job_cadence_text(job: dict[str, object]) -> str:
    """Return a human-facing cadence label for recurring jobs when available."""
    poll_interval = str(job.get("poll_interval", "") or "").strip()
    if poll_interval:
        return f"every {poll_interval}"

    timer = job.get("timer")
    if isinstance(timer, dict) and timer.get("kind") == "interval":
        on_unit_active_sec = str(timer.get("on_unit_active_sec", "") or "").strip()
        if on_unit_active_sec:
            return f"every {on_unit_active_sec}"

    return ""


def select_next_run(candidates: list[dict[str, str]]) -> dict[str, str]:
    """Return the earliest scheduled run from a set of job status candidates."""
    earliest: tuple[datetime, dict[str, str]] | None = None
    fallback: dict[str, str] | None = None

    for candidate in candidates:
        next_run_iso = candidate.get("next_run_iso", "")
        if not next_run_iso:
            if fallback is None and candidate.get("next_run_text"):
                fallback = candidate
            continue
        try:
            next_run = datetime.fromisoformat(next_run_iso)
        except ValueError:
            continue
        if earliest is None or next_run < earliest[0]:
            earliest = (next_run, candidate)

    if earliest is None:
        return fallback or {
            "next_run_iso": "",
            "next_run_text": "",
            "job_name": "",
            "cadence_text": "",
        }

    _, candidate = earliest
    return {
        "next_run_iso": candidate.get("next_run_iso", ""),
        "next_run_text": candidate.get("next_run_text", ""),
        "job_name": candidate.get("job_name", ""),
        "cadence_text": candidate.get("cadence_text", ""),
    }


def normalize_systemd_value(value: str) -> str:
    text = value.strip()
    if text.lower() in _EMPTY_SYSTEMD_VALUES:
        return ""
    return text


def next_cron_occurrence(
    expression: str, timezone_name: str = "", now: datetime | None = None
) -> datetime | None:
    """Return the next wall-clock run for a standard 5-field cron expression."""
    parts = expression.split()
    if len(parts) != 5:
        return None

    try:
        minute_values = _parse_cron_field(parts[0], 0, 59)
        hour_values = _parse_cron_field(parts[1], 0, 23)
        day_values = _parse_cron_field(parts[2], 1, 31)
        month_values = _parse_cron_field(parts[3], 1, 12, names=_MONTH_NAMES)
        dow_values = _parse_cron_field(parts[4], 0, 7, names=_DOW_NAMES, wrap_zero=True)
    except ValueError:
        return None

    tz = _cron_tzinfo(timezone_name)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    candidate = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    dom_any = parts[2] == "*"
    dow_any = parts[4] == "*"

    for _ in range(20000):
        if candidate.month not in month_values:
            month, wrapped = _next_allowed_value(month_values, candidate.month)
            year = candidate.year + (1 if wrapped else 0)
            candidate = candidate.replace(year=year, month=month, day=1, hour=0, minute=0)
            continue
        if not _cron_day_matches(candidate, day_values, dow_values, dom_any, dow_any):
            candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if candidate.hour not in hour_values:
            hour, wrapped = _next_allowed_value(hour_values, candidate.hour)
            if wrapped:
                candidate = (candidate + timedelta(days=1)).replace(hour=hour, minute=0)
            else:
                candidate = candidate.replace(hour=hour, minute=0)
            continue
        if candidate.minute not in minute_values:
            minute, wrapped = _next_allowed_value(minute_values, candidate.minute)
            if wrapped:
                candidate = (candidate + timedelta(hours=1)).replace(minute=minute)
            else:
                candidate = candidate.replace(minute=minute)
            continue
        return candidate

    return None


@lru_cache(maxsize=1)
def _local_tzinfo() -> tzinfo:
    for candidate in (
        os.environ.get("TZ", ""),
        _timezone_name_from_localtime(),
        _timezone_name_from_file(Path("/etc/timezone")),
    ):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    return datetime.now().astimezone().tzinfo or timezone.utc


def _timezone_name_from_localtime() -> str:
    target = Path("/etc/localtime")
    try:
        resolved = target.resolve()
    except OSError:
        return ""
    marker = "/zoneinfo/"
    resolved_str = str(resolved)
    if marker not in resolved_str:
        return ""
    return resolved_str.split(marker, 1)[1]


def _timezone_name_from_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _cron_tzinfo(timezone_name: str) -> tzinfo:
    name = timezone_name.strip()
    if not name:
        return _local_tzinfo()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return _local_tzinfo()


def _format_schedule_timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%a %Y-%m-%d %H:%M:%S %Z")


def _parse_cron_field(
    text: str,
    minimum: int,
    maximum: int,
    *,
    names: dict[str, int] | None = None,
    wrap_zero: bool = False,
) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        token = part.strip().lower()
        if not token:
            raise ValueError("empty cron token")

        base = token
        step = 1
        if "/" in token:
            base, step_text = token.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("cron step must be positive")

        if base == "*":
            start = minimum
            end = maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start = _parse_cron_value(start_text, minimum, maximum, names)
            end = _parse_cron_value(end_text, minimum, maximum, names)
            if start > end:
                raise ValueError("descending cron ranges are not supported")
        else:
            start = end = _parse_cron_value(base, minimum, maximum, names)

        values.update(range(start, end + 1, step))

    if wrap_zero and 7 in values:
        values.discard(7)
        values.add(0)
    return values


def _parse_cron_value(text: str, minimum: int, maximum: int, names: dict[str, int] | None) -> int:
    token = text.strip().lower()
    if names and token in names:
        value = names[token]
    else:
        value = int(token)
    if value < minimum or value > maximum:
        raise ValueError(f"cron value {value} outside range {minimum}-{maximum}")
    return value


def _cron_day_matches(
    candidate: datetime,
    day_values: set[int],
    dow_values: set[int],
    dom_any: bool,
    dow_any: bool,
) -> bool:
    day_match = candidate.day in day_values
    dow_match = ((candidate.weekday() + 1) % 7) in dow_values
    if dom_any and dow_any:
        return True
    if dom_any:
        return dow_match
    if dow_any:
        return day_match
    return day_match or dow_match


def _next_allowed_value(values: set[int], current: int) -> tuple[int, bool]:
    ordered = sorted(values)
    for value in ordered:
        if value >= current:
            return value, False
    return ordered[0], True
