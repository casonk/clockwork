"""Core scheduler data structures."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

VALID_SCOPES = {"user", "system"}
VALID_SERVICE_TYPES = {"oneshot", "simple"}
VALID_TIMER_KINDS = {"calendar", "interval"}


@dataclass(frozen=True)
class TimerSpec:
    """A concrete systemd timer definition."""

    kind: str
    on_calendar: str | None = None
    on_boot_sec: str | None = None
    on_unit_active_sec: str | None = None
    unit: str | None = None
    persistent: bool = False
    accuracy_sec: str | None = None
    randomized_delay_sec: str | None = None
    install_wanted_by: str = "timers.target"

    def validate(self) -> None:
        if self.kind not in VALID_TIMER_KINDS:
            raise ValueError(f"Unsupported timer kind: {self.kind!r}")
        if self.kind == "calendar" and not self.on_calendar:
            raise ValueError("calendar timers require on_calendar")
        if self.kind == "interval" and not (self.on_boot_sec or self.on_unit_active_sec):
            raise ValueError("interval timers require on_boot_sec and/or on_unit_active_sec")


@dataclass(frozen=True)
class CronSpec:
    """A cron rendering companion for a job."""

    expression: str
    command: str
    timezone: str | None = None
    comments: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.expression.strip():
            raise ValueError("cron expressions must be non-empty")
        if not self.command.strip():
            raise ValueError("cron commands must be non-empty")


@dataclass(frozen=True)
class TargetOverrides:
    """Field replacements applied only when a job is rendered for one target.

    systemd and launchd are different schedulers, not two spellings of one.
    Ordering dependencies, timer jitter, and timer accuracy have no launchd
    equivalent, and clockwork refuses to render a job that declares them rather
    than dropping them silently and quietly changing what the schedule means.
    That refusal is right, but it left a portable manifest with no way to say
    "this setting is for systemd" -- so a manifest carrying ``after`` could not
    be installed on macOS at all, however Linux-specific that one line was.

    A ``[jobs.launchd]`` table says it explicitly. Keys present there replace
    the job's values when, and only when, the launchd target is rendered; the
    systemd and cron renderings never see them. Empty values clear a field:
    ``after = []`` drops the ordering dependency and
    ``randomized_delay_sec = ""`` drops the jitter, leaving the systemd
    rendering untouched.

    ``exec_start`` is overridable for the same reason in reverse: interpreter
    and shell paths differ between the platforms, and ``/usr/bin/bash`` does
    not exist on macOS at all. Windows differs the most -- its paths are not
    even POSIX -- so ``[jobs.windows]`` works identically and is usually
    required rather than optional.
    """

    exec_start: str | None = None
    working_directory: str | None = None
    after: tuple[str, ...] | None = None
    wants: tuple[str, ...] | None = None
    environment: dict[str, str] | None = None
    randomized_delay_sec: str | None = None
    accuracy_sec: str | None = None

    def validate(self, *, job_name: str, target: str = "launchd") -> None:
        if self.exec_start is not None and not self.exec_start.strip():
            raise ValueError(f"job {job_name!r} has an empty {target} exec_start override")
        if self.working_directory is not None and not self.working_directory.strip():
            raise ValueError(f"job {job_name!r} has an empty {target} working_directory override")


@dataclass(frozen=True)
class JobSpec:
    """A scheduled job with optional timer and cron render targets."""

    name: str
    description: str
    exec_start: str
    scope: str = "user"
    service_type: str = "oneshot"
    working_directory: str | None = None
    after: tuple[str, ...] = ()
    wants: tuple[str, ...] = ()
    start_limit_interval_sec: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    environment_files: tuple[str, ...] = ()
    user: str | None = None
    group: str | None = None
    restart: str | None = None
    restart_sec: str | None = None
    standard_output: str | None = None
    standard_error: str | None = None
    service_install_wanted_by: tuple[str, ...] = ()
    service_name: str | None = None
    timer_name: str | None = None
    timer_description: str | None = None
    poll_interval: str | None = None
    launchd_label: str | None = None
    launchd_run_at_load: bool | None = None
    launchd_overrides: TargetOverrides | None = None
    windows_overrides: TargetOverrides | None = None
    timer: TimerSpec | None = None
    cron: CronSpec | None = None

    def for_target(self, target: str) -> JobSpec:
        """Return this job as one target should see it.

        Applied before validation, so a field that the target cannot express is
        gone by the time its validator looks for it. Returns the job unchanged
        when no overrides are declared for that target, so every existing
        manifest renders exactly as it did before.
        """
        overrides = {
            "launchd": self.launchd_overrides,
            "windows": self.windows_overrides,
        }.get(target)
        if overrides is None:
            return self

        timer = self.timer
        if timer is not None:
            timer_changes: dict[str, str | None] = {}
            # An empty string clears the field; absent leaves it inherited.
            if overrides.randomized_delay_sec is not None:
                timer_changes["randomized_delay_sec"] = overrides.randomized_delay_sec or None
            if overrides.accuracy_sec is not None:
                timer_changes["accuracy_sec"] = overrides.accuracy_sec or None
            if timer_changes:
                timer = replace(timer, **timer_changes)

        changes: dict[str, object] = {
            "timer": timer,
            "launchd_overrides": None,
            "windows_overrides": None,
        }
        if overrides.exec_start is not None:
            changes["exec_start"] = overrides.exec_start
        if overrides.working_directory is not None:
            changes["working_directory"] = overrides.working_directory
        if overrides.after is not None:
            changes["after"] = overrides.after
        if overrides.wants is not None:
            changes["wants"] = overrides.wants
        if overrides.environment is not None:
            changes["environment"] = dict(overrides.environment)
        return replace(self, **changes)

    def for_launchd(self) -> JobSpec:
        return self.for_target("launchd")

    def for_windows(self) -> JobSpec:
        return self.for_target("windows")

    def service_unit_name(self) -> str:
        return self.service_name or f"{self.name}.service"

    def timer_unit_name(self) -> str:
        return self.timer_name or f"{self.name}.timer"

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("job names must be non-empty")
        if not self.description.strip():
            raise ValueError(f"job {self.name!r} is missing description")
        if not self.exec_start.strip():
            raise ValueError(f"job {self.name!r} is missing exec_start")
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"job {self.name!r} has unsupported scope {self.scope!r}")
        if self.service_type not in VALID_SERVICE_TYPES:
            raise ValueError(
                f"job {self.name!r} has unsupported service_type {self.service_type!r}"
            )
        if self.timer is not None:
            self.timer.validate()
        if self.cron is not None:
            self.cron.validate()
        if self.launchd_overrides is not None:
            self.launchd_overrides.validate(job_name=self.name, target="launchd")
        if self.windows_overrides is not None:
            self.windows_overrides.validate(job_name=self.name, target="windows")


@dataclass(frozen=True)
class Manifest:
    """A parsed manifest file."""

    path: str
    jobs: tuple[JobSpec, ...]

    def validate(self) -> None:
        if not self.jobs:
            raise ValueError(f"Manifest {self.path!r} contains no jobs")
        for job in self.jobs:
            job.validate()
