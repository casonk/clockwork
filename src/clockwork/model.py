"""Core scheduler data structures."""

from __future__ import annotations

from dataclasses import dataclass, field

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
    timer: TimerSpec | None = None
    cron: CronSpec | None = None

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
