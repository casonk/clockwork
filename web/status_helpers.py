"""Helpers for normalising systemd status data for the web UI."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ENABLED_STATES = {"enabled", "static", "alias"}
_EMPTY_SYSTEMD_VALUES = {"", "0", "n/a", "[not set]"}
_SYSTEMD_TIMESTAMP_RE = re.compile(
    r"^\S+\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:\s+([^\s]+))?$"
)


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
