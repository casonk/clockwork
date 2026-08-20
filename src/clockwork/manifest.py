"""Manifest loading for clockwork."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .model import CronSpec, JobSpec, LaunchdOverrides, Manifest, TimerSpec

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Expected a list of strings, got {type(value).__name__}")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"Expected a string list item, got {type(item).__name__}")
        result.append(item)
    return tuple(result)


def _as_str_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a table of strings, got {type(value).__name__}")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str):
            raise ValueError(f"Expected string value for {key!r}, got {type(item).__name__}")
        result[str(key)] = item
    return result


def _as_optional_str(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected {field} to be a string, got {type(value).__name__}")
    return value


def _as_optional_bool(value: Any, *, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Expected {field} to be a boolean, got {type(value).__name__}")
    return value


def _parse_timer(value: Any) -> TimerSpec | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Expected timer table, got {type(value).__name__}")
    return TimerSpec(
        kind=str(value["kind"]),
        on_calendar=value.get("on_calendar"),
        on_boot_sec=value.get("on_boot_sec"),
        on_unit_active_sec=value.get("on_unit_active_sec"),
        unit=value.get("unit"),
        persistent=bool(value.get("persistent", False)),
        accuracy_sec=value.get("accuracy_sec"),
        randomized_delay_sec=value.get("randomized_delay_sec"),
        install_wanted_by=str(value.get("install_wanted_by", "timers.target")),
    )


def _parse_cron(value: Any) -> CronSpec | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Expected cron table, got {type(value).__name__}")
    return CronSpec(
        expression=str(value["expression"]),
        command=str(value["command"]),
        timezone=value.get("timezone"),
        comments=_as_str_tuple(value.get("comments")),
    )


_LAUNCHD_OVERRIDE_KEYS = frozenset(
    {
        "exec_start",
        "working_directory",
        "after",
        "wants",
        "environment",
        "timer",
    }
)

# Mirrors [jobs.timer] so the override reads the same way as the thing it
# overrides. Only the fields launchd cannot express are worth withdrawing.
_LAUNCHD_TIMER_OVERRIDE_KEYS = frozenset({"randomized_delay_sec", "accuracy_sec"})


def _parse_launchd_overrides(value: Any) -> LaunchdOverrides | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Expected launchd override table, got {type(value).__name__}")
    # Reject unknown keys rather than ignoring them. A typo here fails open in
    # the worst way: the override silently does nothing and the job goes back
    # to being unrenderable on macOS, which is the bug this table exists to fix.
    unknown = sorted(set(value) - _LAUNCHD_OVERRIDE_KEYS)
    if unknown:
        raise ValueError(
            "unsupported launchd override keys: "
            + ", ".join(unknown)
            + f" (supported: {', '.join(sorted(_LAUNCHD_OVERRIDE_KEYS))})"
        )
    timer_value = value.get("timer")
    if timer_value is None:
        timer_value = {}
    elif not isinstance(timer_value, dict):
        raise ValueError(f"Expected launchd timer override table, got {type(timer_value).__name__}")
    unknown_timer = sorted(set(timer_value) - _LAUNCHD_TIMER_OVERRIDE_KEYS)
    if unknown_timer:
        raise ValueError(
            "unsupported launchd timer override keys: "
            + ", ".join(unknown_timer)
            + f" (supported: {', '.join(sorted(_LAUNCHD_TIMER_OVERRIDE_KEYS))})"
        )

    return LaunchdOverrides(
        exec_start=_as_optional_str(value.get("exec_start"), field="launchd.exec_start"),
        working_directory=_as_optional_str(
            value.get("working_directory"), field="launchd.working_directory"
        ),
        after=_as_str_tuple(value["after"]) if "after" in value else None,
        wants=_as_str_tuple(value["wants"]) if "wants" in value else None,
        environment=_as_str_dict(value["environment"]) if "environment" in value else None,
        randomized_delay_sec=_as_optional_str(
            timer_value.get("randomized_delay_sec"), field="launchd.timer.randomized_delay_sec"
        ),
        accuracy_sec=_as_optional_str(
            timer_value.get("accuracy_sec"), field="launchd.timer.accuracy_sec"
        ),
    )


def _parse_job(value: Any) -> JobSpec:
    if not isinstance(value, dict):
        raise ValueError(f"Expected job table, got {type(value).__name__}")
    return JobSpec(
        name=str(value["name"]),
        description=str(value["description"]),
        exec_start=str(value["exec_start"]),
        scope=str(value.get("scope", "user")),
        service_type=str(value.get("service_type", "oneshot")),
        working_directory=value.get("working_directory"),
        after=_as_str_tuple(value.get("after")),
        wants=_as_str_tuple(value.get("wants")),
        start_limit_interval_sec=value.get("start_limit_interval_sec"),
        environment=_as_str_dict(value.get("environment")),
        environment_files=_as_str_tuple(value.get("environment_files")),
        user=value.get("user"),
        group=value.get("group"),
        restart=value.get("restart"),
        restart_sec=value.get("restart_sec"),
        standard_output=value.get("standard_output"),
        standard_error=value.get("standard_error"),
        service_install_wanted_by=_as_str_tuple(value.get("service_install_wanted_by")),
        service_name=value.get("service_name"),
        timer_name=value.get("timer_name"),
        timer_description=value.get("timer_description"),
        poll_interval=value.get("poll_interval"),
        launchd_label=_as_optional_str(value.get("launchd_label"), field="launchd_label"),
        launchd_run_at_load=_as_optional_bool(
            value.get("launchd_run_at_load"), field="launchd_run_at_load"
        ),
        launchd_overrides=_parse_launchd_overrides(value.get("launchd")),
        timer=_parse_timer(value.get("timer")),
        cron=_parse_cron(value.get("cron")),
    )


def load_manifest(path: str | Path) -> Manifest:
    manifest_path = Path(path)
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    jobs = tuple(_parse_job(item) for item in data.get("jobs", []))
    manifest = Manifest(path=str(manifest_path), jobs=jobs)
    manifest.validate()
    return manifest
