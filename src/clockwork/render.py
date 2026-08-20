"""Render clockwork manifests into scheduler artifacts."""

from __future__ import annotations

import json
import ntpath
import os
import plistlib
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

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


def _expand_home_path(value: str, *, home: Path) -> str:
    expanded = value.replace("%h", str(home))
    if expanded == "~":
        expanded = str(home)
    elif expanded.startswith("~/"):
        expanded = str(home / expanded[2:])
    return expanded


def _duration_seconds(
    value: str, *, field: str, allow_zero: bool = False, scheduler: str = "launchd"
) -> int:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(
            f"{scheduler} requires {field} to be one integer duration such as 30s, 5m, 2h, or 1d"
        )
    amount = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = amount * multiplier
    if seconds == 0 and not allow_zero:
        raise ValueError(f"{scheduler} requires {field} to be greater than zero")
    return seconds


def _calendar_intervals(value: str) -> dict[str, int] | list[dict[str, int]]:
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
    command = _expand_home_path(job.exec_start, home=home)
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
        path = _expand_home_path(path, home=home)
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
    job = job.for_target("launchd")
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
        working_directory = _expand_home_path(job.working_directory, home=resolved_home)
        if not os.path.isabs(working_directory):
            raise ValueError(
                f"job {job.name!r} launchd working_directory must be absolute: {working_directory!r}"
            )
        plist["WorkingDirectory"] = working_directory
    if job.environment:
        plist["EnvironmentVariables"] = {
            key: _expand_home_path(job.environment[key], home=resolved_home)
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
        plist["StartCalendarInterval"] = _calendar_intervals(job.timer.on_calendar)
    else:
        assert job.timer.on_unit_active_sec is not None
        plist["StartInterval"] = _duration_seconds(
            job.timer.on_unit_active_sec, field="on_unit_active_sec"
        )
    if job.launchd_run_at_load is not None:
        plist["RunAtLoad"] = job.launchd_run_at_load

    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


# --- Windows Task Scheduler --------------------------------------------------
#
# Windows is the third native target, not an emulation layer. clockwork is a
# renderer, so supporting Windows means emitting Task Scheduler XML the same way
# it emits plists and units -- requiring users to install a container runtime
# merely to schedule a job would be a far heavier dependency than the scheduler
# itself.

# Task Scheduler names days rather than numbering them. _calendar_intervals
# yields the launchd numbering (0 = Sunday), so map back out of it here.
_WINDOWS_WEEKDAYS = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
}

# Task Scheduler expands %VAR% in Command and WorkingDirectory at run time, so
# %h becomes %USERPROFILE% rather than this host's home directory. Rendering
# therefore does not need to know the target user's home, and the output stays
# identical no matter which machine produced it.
_WINDOWS_HOME = "%USERPROFILE%"

# A fixed anchor. Task Scheduler needs a StartBoundary, but only its time of day
# matters for a recurring schedule; using today's date would make rendering
# non-deterministic, which every other target here avoids.
_WINDOWS_START_DATE = "2000-01-01"


def _expand_windows_path(value: str) -> str:
    expanded = value.replace("%h", _WINDOWS_HOME)
    if expanded == "~":
        expanded = _WINDOWS_HOME
    elif expanded.startswith("~/"):
        expanded = _WINDOWS_HOME + "\\" + expanded[2:]
    return expanded


def _windows_is_absolute(path: str) -> bool:
    r"""Windows-absolute, judged with Windows rules on any host.

    os.path is the *host's* flavour, so os.path.isabs(r"C:\tools\x.exe") is
    False when rendering from macOS or Linux -- which would reject correct
    Windows paths. Use ntpath explicitly. A path rooted at an environment
    variable counts as absolute because Task Scheduler expands it before use.
    """
    return path.startswith("%") or ntpath.isabs(path)


def _iso8601_duration(seconds: int) -> str:
    if seconds <= 0:
        return "PT0S"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    out = "PT"
    if hours:
        out += f"{hours}H"
    if minutes:
        out += f"{minutes}M"
    if secs:
        out += f"{secs}S"
    return out


def _validate_windows_job(job: JobSpec) -> None:
    """Refuse anything Task Scheduler cannot express honestly.

    Same contract as the launchd validator: a setting that has no Windows
    equivalent is refused rather than dropped, and [jobs.windows] is how a
    portable manifest withdraws it for this target alone.
    """
    if job.scope != "user":
        raise ValueError(
            f"job {job.name!r} has scope {job.scope!r}; windows-user refuses system scope"
        )
    if job.user or job.group:
        raise ValueError(f"job {job.name!r} sets user/group; a user task cannot change identity")
    if job.after or job.wants:
        raise ValueError(
            f"job {job.name!r} uses systemd ordering dependencies; Task Scheduler has no equivalent"
        )
    if job.environment:
        raise ValueError(
            f"job {job.name!r} sets environment variables; a Task Scheduler Exec action "
            "cannot carry them -- set them inside the script it runs, or clear them "
            "for this target with [jobs.windows]"
        )
    if job.environment_files:
        raise ValueError(
            f"job {job.name!r} uses environment_files, which Task Scheduler cannot read"
        )
    if job.start_limit_interval_sec:
        raise ValueError(
            f"job {job.name!r} uses start_limit_interval_sec, which windows-user cannot map"
        )
    if job.service_install_wanted_by and job.service_install_wanted_by != ("default.target",):
        raise ValueError(
            f"job {job.name!r} uses unsupported service_install_wanted_by values for windows-user"
        )
    if job.restart not in {None, "on-failure"}:
        raise ValueError(
            f"job {job.name!r} uses restart={job.restart!r}; Task Scheduler restarts a task "
            "on failure but does not supervise a daemon -- that is a Windows service"
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

    timer = job.timer
    if timer is None:
        return
    if timer.unit and timer.unit != job.service_unit_name():
        raise ValueError(
            f"job {job.name!r} schedules another unit; windows-user only supports self schedules"
        )
    if timer.install_wanted_by != "timers.target":
        raise ValueError(
            f"job {job.name!r} uses unsupported timer install target {timer.install_wanted_by!r}"
        )
    if timer.accuracy_sec:
        raise ValueError(
            f"job {job.name!r} uses accuracy_sec, which Task Scheduler cannot guarantee"
        )
    if timer.kind == "interval":
        if timer.on_boot_sec:
            raise ValueError(
                f"job {job.name!r} uses on_boot_sec; windows-user has no delayed-boot equivalent"
            )
        if not timer.on_unit_active_sec:
            raise ValueError(f"job {job.name!r} windows interval requires on_unit_active_sec")


def windows_task_name(job: JobSpec) -> str:
    """Task Scheduler path for a job, namespaced so tasks are easy to find."""
    return f"\\Clockwork\\{job.name}"


def _windows_command_and_arguments(job: JobSpec) -> tuple[str, str]:
    command = _expand_windows_path(job.exec_start)
    try:
        # posix=False keeps Windows backslashes intact; shlex would otherwise
        # read them as escapes and quietly mangle every path.
        argv = shlex.split(command, posix=False)
    except ValueError as exc:
        raise ValueError(f"job {job.name!r} has invalid exec_start quoting: {exc}") from exc
    if not argv:
        raise ValueError(f"job {job.name!r} has an empty windows command")
    executable = argv[0].strip('"')
    if not _windows_is_absolute(executable):
        raise ValueError(
            f"job {job.name!r} windows executable must be absolute: {executable!r}. "
            "Give a Windows path with [jobs.windows] exec_start."
        )
    return executable, " ".join(argv[1:])


def _windows_triggers(job: JobSpec) -> list[str]:
    timer = job.timer
    if timer is None:
        # No timer means a long-running job; start it when the user logs on,
        # which is the closest Windows equivalent to launchd's RunAtLoad.
        return ["    <LogonTrigger>\n      <Enabled>true</Enabled>\n    </LogonTrigger>"]

    random_delay = ""
    if timer.randomized_delay_sec:
        # Unlike launchd, Task Scheduler *can* express jitter.
        seconds = _duration_seconds(
            timer.randomized_delay_sec, field="randomized_delay_sec", scheduler="windows"
        )
        random_delay = f"\n      <RandomDelay>{_iso8601_duration(seconds)}</RandomDelay>"

    if timer.kind == "interval":
        assert timer.on_unit_active_sec is not None
        seconds = _duration_seconds(
            timer.on_unit_active_sec, field="on_unit_active_sec", scheduler="windows"
        )
        return [
            "    <TimeTrigger>\n"
            f"      <StartBoundary>{_WINDOWS_START_DATE}T00:00:00</StartBoundary>\n"
            "      <Enabled>true</Enabled>\n"
            "      <Repetition>\n"
            f"        <Interval>{_iso8601_duration(seconds)}</Interval>\n"
            "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
            "      </Repetition>"
            f"{random_delay}\n"
            "    </TimeTrigger>"
        ]

    assert timer.on_calendar is not None
    intervals = _calendar_intervals(timer.on_calendar)
    if isinstance(intervals, dict):
        intervals = [intervals]

    triggers = []
    for interval in intervals:
        start = (
            f"{_WINDOWS_START_DATE}T"
            f"{interval['Hour']:02d}:{interval['Minute']:02d}:{interval['Second']:02d}"
        )
        if "Weekday" in interval:
            day = _WINDOWS_WEEKDAYS[interval["Weekday"]]
            schedule = (
                "      <ScheduleByWeek>\n"
                f"        <DaysOfWeek><{day} /></DaysOfWeek>\n"
                "        <WeeksInterval>1</WeeksInterval>\n"
                "      </ScheduleByWeek>"
            )
        else:
            schedule = (
                "      <ScheduleByDay>\n"
                "        <DaysInterval>1</DaysInterval>\n"
                "      </ScheduleByDay>"
            )
        triggers.append(
            "    <CalendarTrigger>\n"
            f"      <StartBoundary>{start}</StartBoundary>\n"
            "      <Enabled>true</Enabled>"
            f"{random_delay}\n"
            f"{schedule}\n"
            "    </CalendarTrigger>"
        )
    return triggers


def render_windows_task(job: JobSpec) -> str:
    """Render one user-scope job as deterministic Task Scheduler XML."""

    job = job.for_target("windows")
    _validate_windows_job(job)

    command, arguments = _windows_command_and_arguments(job)

    settings = [
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
        "    <AllowHardTerminate>true</AllowHardTerminate>",
        "    <StartWhenAvailable>"
        f"{'true' if job.timer is not None and job.timer.persistent else 'false'}"
        "</StartWhenAvailable>",
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>",
        "    <Enabled>true</Enabled>",
        "    <Hidden>false</Hidden>",
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>",
        "    <WakeToRun>false</WakeToRun>",
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        "    <Priority>7</Priority>",
    ]
    if job.restart == "on-failure":
        seconds = (
            _duration_seconds(job.restart_sec, field="restart_sec", scheduler="windows")
            if job.restart_sec
            else 60
        )
        settings.append(
            "    <RestartOnFailure>\n"
            f"      <Interval>{_iso8601_duration(seconds)}</Interval>\n"
            "      <Count>3</Count>\n"
            "    </RestartOnFailure>"
        )

    action = ["      <Command>" + xml_escape(command) + "</Command>"]
    if arguments:
        action.append("      <Arguments>" + xml_escape(arguments) + "</Arguments>")
    if job.working_directory:
        working_directory = _expand_windows_path(job.working_directory)
        if not _windows_is_absolute(working_directory):
            raise ValueError(
                f"job {job.name!r} windows working_directory must be absolute: "
                f"{working_directory!r}"
            )
        action.append(
            "      <WorkingDirectory>" + xml_escape(working_directory) + "</WorkingDirectory>"
        )

    lines = [
        '<?xml version="1.0" encoding="UTF-16"?>',
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
        "  <RegistrationInfo>",
        f"    <Description>{xml_escape(job.description)}</Description>",
        f"    <URI>{xml_escape(windows_task_name(job))}</URI>",
        "  </RegistrationInfo>",
        "  <Triggers>",
        *_windows_triggers(job),
        "  </Triggers>",
        "  <Principals>",
        '    <Principal id="Author">',
        "      <LogonType>InteractiveToken</LogonType>",
        "      <RunLevel>LeastPrivilege</RunLevel>",
        "    </Principal>",
        "  </Principals>",
        "  <Settings>",
        *settings,
        "  </Settings>",
        '  <Actions Context="Author">',
        "    <Exec>",
        *action,
        "    </Exec>",
        "  </Actions>",
        "</Task>",
        "",
    ]
    return "\n".join(lines)


def write_windows_files(output_dir: str | Path, files: dict[str, str]) -> list[Path]:
    """Write Task Scheduler XML as UTF-16.

    schtasks /Create /XML rejects UTF-8 task files with a malformed-XML error,
    so the declaration says UTF-16 and the bytes have to match it. Python's
    "utf-16" codec emits the BOM schtasks expects.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in sorted(files.items()):
        path = directory / name
        path.write_text(content, encoding="utf-16")
        written.append(path)
    return written


def render_target(manifest: Manifest, target: str) -> dict[str, str]:
    if target not in {"systemd-user", "systemd-system", "launchd-user", "windows-user", "cron"}:
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

    if target == "windows-user":
        rendered = {}
        for job in manifest.jobs:
            if job.scope != "user":
                continue
            filename = f"{job.name}.xml"
            if filename in rendered:
                raise ValueError(f"duplicate windows task name generated for job {job.name!r}")
            rendered[filename] = render_windows_task(job)
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
