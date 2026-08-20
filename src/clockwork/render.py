"""Render clockwork manifests into scheduler artifacts."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

from .model import JobSpec, Manifest

_LAUNCHD_LABEL_PREFIX = "io.github.casonk.clockwork"
_LAUNCHD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Leave room for the six-byte ``.plist`` suffix within macOS's 255-byte
# filename-component limit. The accepted alphabet is ASCII, so character and
# byte counts are identical here.
_LAUNCHD_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,248}$")
_LAUNCHD_ENVIRONMENT_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SYSTEMD_CALENDAR_RE = re.compile(
    r"^(?:((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
    r"(?:,(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun))*)\s+)?"
    r"\*-\*-\*\s+(\d{1,2})(?:/(\d{1,2}))?:(\d{2})(?::(\d{2}))?$",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"^(\d+)\s*([smhd]?)$", re.IGNORECASE)
_LAUNCHD_WEEKDAYS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}

# launchd cannot load systemd-style EnvironmentFile entries. This small,
# self-contained runner loads only KEY=VALUE records (without evaluating them
# as shell code), then replaces itself with the manifest command. Keeping the
# loader in ProgramArguments makes each plist independently reviewable and
# avoids copying secrets into the generated plist.
_LAUNCHD_ENV_LOADER = """\
import json, os, re, shlex, stat, sys
cfg = json.loads(sys.argv[1])
env = os.environ.copy()
key_re = re.compile(r\"^[A-Za-z_][A-Za-z0-9_]*$\")
for item in cfg[\"environment_files\"]:
    optional = item.startswith(\"-\")
    path = item[1:] if optional else item
    flags = os.O_RDONLY | getattr(os, \"O_NOFOLLOW\", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if optional:
            continue
        raise SystemExit(f\"required environment file not found: {path}\")
    except OSError as exc:
        raise SystemExit(f\"refusing environment file {path}: {exc}\") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise SystemExit(f\"refusing non-regular environment file: {path}\")
        if metadata.st_uid != os.getuid():
            raise SystemExit(f\"refusing environment file not owned by current user: {path}\")
        if metadata.st_mode & 0o077:
            raise SystemExit(f\"refusing environment file that is not owner-only: {path}\")
        if metadata.st_size > 1048576:
            raise SystemExit(f\"refusing environment file larger than 1 MiB: {path}\")
        with os.fdopen(fd, encoding=\"utf-8\") as stream:
            fd = -1
            lines = stream.read().splitlines()
    finally:
        if fd >= 0:
            os.close(fd)
    for number, source in enumerate(lines, 1):
        line = source.strip()
        if not line or line.startswith(\"#\"):
            continue
        if \"=\" not in line:
            raise SystemExit(f\"{path}:{number}: expected KEY=VALUE\")
        key, raw = line.split(\"=\", 1)
        key = key.strip()
        if not key_re.fullmatch(key):
            raise SystemExit(f\"{path}:{number}: invalid environment key\")
        try:
            values = shlex.split(raw, comments=False, posix=True)
        except ValueError as exc:
            raise SystemExit(f\"{path}:{number}: {exc}\") from exc
        if raw and len(values) != 1:
            raise SystemExit(f\"{path}:{number}: quote values containing whitespace\")
        env[key] = values[0] if values else \"\"
os.execvpe(cfg[\"argv\"][0], cfg[\"argv\"], env)
"""


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def _quote_systemd_environment(key: str, value: str) -> str:
    payload = f"{key}={value}".replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{payload}"'


def render_service_unit(job: JobSpec) -> str:
    lines = ["[Unit]", f"Description={job.description}"]
    if job.after:
        lines.append(f"After={' '.join(job.after)}")
    if job.wants:
        lines.append(f"Wants={' '.join(job.wants)}")
    if job.start_limit_interval_sec:
        lines.append(f"StartLimitIntervalSec={job.start_limit_interval_sec}")

    lines.extend(["", "[Service]", f"Type={job.service_type}"])
    if job.user:
        lines.append(f"User={job.user}")
    if job.group:
        lines.append(f"Group={job.group}")
    if job.working_directory:
        lines.append(f"WorkingDirectory={job.working_directory}")
    for environment_file in job.environment_files:
        lines.append(f"EnvironmentFile={environment_file}")
    for key in sorted(job.environment):
        lines.append(_quote_systemd_environment(key, job.environment[key]))
    lines.append(f"ExecStart={job.exec_start}")
    if job.restart:
        lines.append(f"Restart={job.restart}")
    if job.restart_sec:
        lines.append(f"RestartSec={job.restart_sec}")
    if job.standard_output:
        lines.append(f"StandardOutput={job.standard_output}")
    if job.standard_error:
        lines.append(f"StandardError={job.standard_error}")

    if job.service_install_wanted_by:
        lines.extend(["", "[Install]"])
        for target in job.service_install_wanted_by:
            lines.append(f"WantedBy={target}")
    return "\n".join(lines) + "\n"


def render_timer_unit(job: JobSpec) -> str:
    if job.timer is None:
        raise ValueError(f"job {job.name!r} does not define a timer")

    description = job.timer_description or f"Run {job.description} on schedule"
    unit_name = job.timer.unit or job.service_unit_name()

    lines = ["[Unit]", f"Description={description}", "", "[Timer]"]
    if job.timer.kind == "calendar":
        lines.append(f"OnCalendar={job.timer.on_calendar}")
    else:
        if job.timer.on_boot_sec:
            lines.append(f"OnBootSec={job.timer.on_boot_sec}")
        if job.timer.on_unit_active_sec:
            lines.append(f"OnUnitActiveSec={job.timer.on_unit_active_sec}")
    lines.append(f"Persistent={_format_bool(job.timer.persistent)}")
    lines.append(f"Unit={unit_name}")
    if job.timer.accuracy_sec:
        lines.append(f"AccuracySec={job.timer.accuracy_sec}")
    if job.timer.randomized_delay_sec:
        lines.append(f"RandomizedDelaySec={job.timer.randomized_delay_sec}")
    lines.extend(["", "[Install]", f"WantedBy={job.timer.install_wanted_by}"])
    return "\n".join(lines) + "\n"


def render_crontab(manifest: Manifest) -> str:
    lines = [
        f"# Generated by clockwork from {manifest.path}",
        "# Review paths and commands before installing with crontab.",
    ]
    for job in manifest.jobs:
        if job.cron is None:
            continue
        lines.extend(["", f"# {job.description}"])
        for comment in job.cron.comments:
            lines.append(f"# {comment}")
        if job.cron.timezone:
            lines.append(f"CRON_TZ={job.cron.timezone}")
        lines.append(f"{job.cron.expression} {job.cron.command}")
    return "\n".join(lines) + "\n"


def resolve_launchd_label(job_name: str, explicit_label: str | None = None) -> str:
    """Resolve the exact label shared by rendering and scheduler controls."""

    if not _LAUNCHD_NAME_RE.fullmatch(job_name):
        raise ValueError(
            f"job {job_name!r} cannot be rendered for launchd: names must match "
            "[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
        )
    label = f"{_LAUNCHD_LABEL_PREFIX}.{job_name}" if explicit_label is None else explicit_label
    if not _LAUNCHD_LABEL_RE.fullmatch(label) or "." not in label:
        raise ValueError(
            f"job {job_name!r} has invalid launchd_label {label!r}; labels must be "
            "dot-qualified and contain only letters, digits, dots, underscores, and hyphens"
        )
    if not label.endswith(f".{job_name}"):
        raise ValueError(
            f"job {job_name!r} launchd_label must end with the exact job name: {label!r}"
        )
    return label


def launchd_label(job: JobSpec) -> str:
    """Return the stable launchd label for *job*.

    Job names are rejected rather than rewritten so two different manifest
    names cannot silently collapse onto the same launchd service label.
    """

    return resolve_launchd_label(job.name, job.launchd_label)


def _expand_launchd_path(value: str, *, home: Path) -> str:
    expanded = value.replace("%h", str(home))
    if expanded == "~":
        expanded = str(home)
    elif expanded.startswith("~/"):
        expanded = str(home / expanded[2:])
    return expanded


def _duration_seconds(value: str, *, field: str, allow_zero: bool = False) -> int:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(
            f"launchd requires {field} to be one integer duration such as 30s, 5m, 2h, or 1d"
        )
    amount = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = amount * multiplier
    if seconds == 0 and not allow_zero:
        raise ValueError(f"launchd requires {field} to be greater than zero")
    return seconds


def _launchd_calendar_intervals(value: str) -> dict[str, int] | list[dict[str, int]]:
    match = _SYSTEMD_CALENDAR_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(
            "launchd calendar rendering supports only '*-*-* HH:MM[:SS]', "
            "'Day *-*-* HH:MM[:SS]', and stepped hours such as '*-*-* 00/4:00'"
        )

    weekday, hour_text, step_text, minute_text, second_text = match.groups()
    hour = int(hour_text)
    minute = int(minute_text)
    second = int(second_text or "0")
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"invalid launchd calendar time: {value!r}")

    step = int(step_text or "0")
    if step_text and (step < 1 or step > 23):
        raise ValueError(f"invalid launchd calendar hour step: {step_text!r}")
    hours = list(range(hour, 24, step)) if step else [hour]
    intervals: list[dict[str, int]] = []
    weekdays = weekday.split(",") if weekday else [None]
    for weekday_name in weekdays:
        for candidate in hours:
            interval = {"Hour": candidate, "Minute": minute, "Second": second}
            if weekday_name:
                interval["Weekday"] = _LAUNCHD_WEEKDAYS[weekday_name.lower()]
            intervals.append(interval)
    return intervals[0] if len(intervals) == 1 else intervals


def _validate_launchd_job(job: JobSpec) -> None:
    if job.scope != "user":
        raise ValueError(
            f"job {job.name!r} has scope {job.scope!r}; launchd-user refuses system scope"
        )
    if job.user or job.group:
        raise ValueError(
            f"job {job.name!r} sets user/group; a user LaunchAgent cannot change identity"
        )
    if job.after or job.wants:
        raise ValueError(
            f"job {job.name!r} uses systemd ordering dependencies; launchd-user has no exact mapping"
        )
    if job.start_limit_interval_sec:
        raise ValueError(
            f"job {job.name!r} uses start_limit_interval_sec, which launchd-user cannot map"
        )
    if job.service_install_wanted_by and job.service_install_wanted_by != ("default.target",):
        raise ValueError(
            f"job {job.name!r} uses unsupported service_install_wanted_by values for launchd-user"
        )
    if job.restart not in {None, "always", "on-failure"}:
        raise ValueError(
            f"job {job.name!r} uses unsupported launchd restart policy {job.restart!r}"
        )
    if job.restart_sec and not job.restart:
        raise ValueError(f"job {job.name!r} sets restart_sec without a restart policy")
    if job.standard_output not in {None, "journal"}:
        raise ValueError(
            f"job {job.name!r} uses unsupported StandardOutput mapping {job.standard_output!r}"
        )
    if job.standard_error not in {None, "journal"}:
        raise ValueError(
            f"job {job.name!r} uses unsupported StandardError mapping {job.standard_error!r}"
        )
    for key, value in job.environment.items():
        if not _LAUNCHD_ENVIRONMENT_KEY_RE.fullmatch(key):
            raise ValueError(f"job {job.name!r} has invalid launchd environment key {key!r}")
        if "\0" in value:
            raise ValueError(f"job {job.name!r} environment value {key!r} contains NUL")

    timer = job.timer
    if timer is None:
        return
    if timer.unit and timer.unit != job.service_unit_name():
        raise ValueError(
            f"job {job.name!r} schedules another unit; launchd-user only supports self schedules"
        )
    if timer.install_wanted_by != "timers.target":
        raise ValueError(
            f"job {job.name!r} uses unsupported timer install target {timer.install_wanted_by!r}"
        )
    if timer.accuracy_sec:
        raise ValueError(f"job {job.name!r} uses accuracy_sec, which launchd-user cannot guarantee")
    if timer.randomized_delay_sec:
        raise ValueError(
            f"job {job.name!r} uses randomized_delay_sec, which launchd-user cannot map safely"
        )
    if timer.kind == "interval":
        if timer.on_boot_sec:
            raise ValueError(
                f"job {job.name!r} uses on_boot_sec; launchd-user has no delayed-login equivalent"
            )
        if not timer.on_unit_active_sec:
            raise ValueError(f"job {job.name!r} launchd interval requires on_unit_active_sec")


def _launchd_program_arguments(job: JobSpec, *, home: Path) -> list[str]:
    command = _expand_launchd_path(job.exec_start, home=home)
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"job {job.name!r} has invalid exec_start quoting: {exc}") from exc
    if not argv:
        raise ValueError(f"job {job.name!r} has an empty launchd command")
    if not os.path.isabs(argv[0]):
        raise ValueError(
            f"job {job.name!r} launchd executable must be absolute after %h expansion: {argv[0]!r}"
        )

    environment_files = []
    for entry in job.environment_files:
        optional = entry.startswith("-")
        path = entry[1:] if optional else entry
        path = _expand_launchd_path(path, home=home)
        if not os.path.isabs(path):
            raise ValueError(
                f"job {job.name!r} launchd environment file must be absolute: {path!r}"
            )
        environment_files.append(f"-{path}" if optional else path)

    if not environment_files:
        return argv
    payload = json.dumps(
        {"argv": argv, "environment_files": environment_files},
        sort_keys=True,
        separators=(",", ":"),
    )
    return ["/usr/bin/python3", "-c", _LAUNCHD_ENV_LOADER, payload]


def render_launchd_plist(job: JobSpec, *, home: Path | None = None) -> str:
    """Render one user-scope job as a deterministic XML LaunchAgent plist."""

    # Apply the job's launchd overrides before validating, so a systemd-only
    # field the manifest has already withdrawn for this target is gone before
    # the validator objects to it.
    job = job.for_launchd()
    _validate_launchd_job(job)
    resolved_home = Path.home() if home is None else Path(home)
    label = launchd_label(job)
    log_dir = resolved_home / "Library" / "Logs" / "Clockwork"
    plist: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": _launchd_program_arguments(job, home=resolved_home),
        "StandardErrorPath": str(log_dir / f"{job.name}.stderr.log"),
        "StandardOutPath": str(log_dir / f"{job.name}.stdout.log"),
    }

    if job.working_directory:
        working_directory = _expand_launchd_path(job.working_directory, home=resolved_home)
        if not os.path.isabs(working_directory):
            raise ValueError(
                f"job {job.name!r} launchd working_directory must be absolute: {working_directory!r}"
            )
        plist["WorkingDirectory"] = working_directory
    if job.environment:
        plist["EnvironmentVariables"] = {
            key: _expand_launchd_path(job.environment[key], home=resolved_home)
            for key in sorted(job.environment)
        }
    if job.restart == "always":
        plist["KeepAlive"] = True
    elif job.restart == "on-failure":
        plist["KeepAlive"] = {"SuccessfulExit": False}
        plist["RunAtLoad"] = True
    if job.restart_sec:
        plist["ThrottleInterval"] = _duration_seconds(job.restart_sec, field="restart_sec")

    if job.timer is None:
        plist.setdefault("RunAtLoad", True)
    elif job.timer.kind == "calendar":
        assert job.timer.on_calendar is not None
        plist["StartCalendarInterval"] = _launchd_calendar_intervals(job.timer.on_calendar)
    else:
        assert job.timer.on_unit_active_sec is not None
        plist["StartInterval"] = _duration_seconds(
            job.timer.on_unit_active_sec, field="on_unit_active_sec"
        )
    if job.launchd_run_at_load is not None:
        plist["RunAtLoad"] = job.launchd_run_at_load

    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


def render_target(manifest: Manifest, target: str) -> dict[str, str]:
    if target not in {"systemd-user", "systemd-system", "launchd-user", "cron"}:
        raise ValueError(f"Unsupported render target: {target!r}")
    if target == "cron":
        return {f"{Path(manifest.path).stem}.crontab": render_crontab(manifest)}

    if target == "launchd-user":
        rendered: dict[str, str] = {}
        for job in manifest.jobs:
            if job.scope != "user":
                continue
            filename = f"{launchd_label(job)}.plist"
            if filename in rendered:
                raise ValueError(f"duplicate launchd label generated for job {job.name!r}")
            rendered[filename] = render_launchd_plist(job)
        return rendered

    expected_scope = "user" if target == "systemd-user" else "system"
    rendered: dict[str, str] = {}
    for job in manifest.jobs:
        if job.scope != expected_scope:
            continue
        rendered[job.service_unit_name()] = render_service_unit(job)
        if job.timer is not None:
            rendered[job.timer_unit_name()] = render_timer_unit(job)
    return rendered


def write_rendered_files(output_dir: str | Path, files: dict[str, str]) -> list[Path]:
    output_path = Path(output_dir)
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise PermissionError(
            f"cannot write to {output_path} — run with sudo for system-scope jobs"
        ) from None
    written: list[Path] = []
    for name, content in files.items():
        target = output_path / name
        try:
            target.write_text(content, encoding="utf-8")
        except PermissionError:
            raise PermissionError(
                f"cannot write {target} — run with sudo for system-scope jobs"
            ) from None
        written.append(target)
    return written


def write_launchd_files(output_dir: str | Path, files: dict[str, str]) -> list[Path]:
    """Atomically write owner-only LaunchAgent plists without following targets."""

    output_path = Path(output_dir)
    if output_path.is_symlink():
        raise PermissionError(f"refusing symlinked LaunchAgents directory: {output_path}")
    try:
        output_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except PermissionError:
        raise PermissionError(f"cannot write to {output_path}") from None

    written: list[Path] = []
    for name, content in files.items():
        target = output_path / name
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output_path,
                prefix=f".{name}.",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, target)
            temporary = None
        except PermissionError:
            raise PermissionError(f"cannot write {target}") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        written.append(target)
    return written
