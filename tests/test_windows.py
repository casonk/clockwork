import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from clockwork.cli import default_target
from clockwork.manifest import load_manifest
from clockwork.model import JobSpec, Manifest, TimerSpec
from clockwork.render import render_target, render_windows_task, write_windows_files

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"


def _job(**overrides) -> JobSpec:
    values = {
        "name": "example-task",
        "description": "Example scheduled task",
        "scope": "user",
        "service_type": "oneshot",
        "working_directory": "%h\\dev\\example",
        "exec_start": "C:\\Windows\\System32\\cmd.exe /c run.cmd",
        "standard_output": "journal",
        "standard_error": "journal",
    }
    values.update(overrides)
    return JobSpec(**values)


def _portable() -> Manifest:
    return load_manifest(
        Path(__file__).resolve().parent.parent / "examples" / "portable" / "weekly-drift.toml"
    )


def test_windows_task_is_deterministic_and_well_formed():
    job = _job(timer=TimerSpec(kind="calendar", on_calendar="Sun *-*-* 04:00:00", persistent=True))

    first = render_windows_task(job)
    assert first == render_windows_task(job)

    root = ET.fromstring(first)
    assert root.tag == f"{NS}Task"
    assert [child.tag.replace(NS, "") for child in root] == [
        "RegistrationInfo",
        "Triggers",
        "Principals",
        "Settings",
        "Actions",
    ]


def test_windows_splits_command_from_arguments_and_expands_home():
    job = _job()
    root = ET.fromstring(render_windows_task(job))
    exec_action = root.find(f"{NS}Actions/{NS}Exec")

    assert exec_action.find(f"{NS}Command").text == "C:\\Windows\\System32\\cmd.exe"
    assert exec_action.find(f"{NS}Arguments").text == "/c run.cmd"
    # %h resolves to a variable Task Scheduler expands at run time, so rendering
    # never has to know the target user's home and stays host-independent.
    assert exec_action.find(f"{NS}WorkingDirectory").text == "%USERPROFILE%\\dev\\example"


def test_windows_keeps_jitter_that_launchd_cannot_express():
    """Task Scheduler has RandomDelay, so randomized_delay_sec survives here.

    This is the case for per-target overrides being opt-in rather than a blanket
    "strip everything portable": a setting only needs withdrawing on the targets
    that genuinely cannot express it.
    """
    job = _job(
        timer=TimerSpec(
            kind="calendar",
            on_calendar="Sun *-*-* 04:00:00",
            persistent=True,
            randomized_delay_sec="600",
        )
    )
    root = ET.fromstring(render_windows_task(job))
    trigger = root.find(f"{NS}Triggers/{NS}CalendarTrigger")

    assert trigger.find(f"{NS}RandomDelay").text == "PT10M"
    assert trigger.find(f"{NS}StartBoundary").text.endswith("T04:00:00")
    weekly = trigger.find(f"{NS}ScheduleByWeek/{NS}DaysOfWeek")
    assert [day.tag.replace(NS, "") for day in weekly] == ["Sunday"]


def test_windows_interval_timer_becomes_a_repetition():
    job = _job(timer=TimerSpec(kind="interval", on_unit_active_sec="15m"))
    root = ET.fromstring(render_windows_task(job))

    interval = root.find(f"{NS}Triggers/{NS}TimeTrigger/{NS}Repetition/{NS}Interval")
    assert interval.text == "PT15M"


def test_windows_absolute_paths_are_judged_by_windows_rules():
    """A POSIX host must not reject a valid Windows path.

    os.path.isabs is the host's flavour, so it calls C:\\... relative when
    rendering from macOS or Linux. The check has to use ntpath explicitly.
    """
    render_windows_task(_job(exec_start="C:\\tools\\run.exe"))
    render_windows_task(_job(exec_start="%USERPROFILE%\\bin\\run.exe"))

    with pytest.raises(ValueError, match="must be absolute"):
        render_windows_task(_job(exec_start="run.exe"))
    with pytest.raises(ValueError, match="must be absolute"):
        render_windows_task(_job(exec_start="/usr/bin/python3 run.py"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"after": ("network.target",)}, "ordering dependencies"),
        ({"environment": {"KEY": "value"}}, "environment variables"),
        ({"scope": "system"}, "refuses system scope"),
        ({"restart": "always"}, "does not supervise a daemon"),
        ({"environment_files": ("-/tmp/env",)}, "environment_files"),
    ],
)
def test_windows_refuses_what_task_scheduler_cannot_express(overrides, message):
    with pytest.raises(ValueError, match=message):
        render_windows_task(_job(**overrides))


def test_windows_overrides_withdraw_unsupported_fields():
    manifest = _portable()
    job = manifest.jobs[0]

    # The job still declares the systemd ordering dependency: the override
    # withdraws it for this target, it does not delete it.
    assert job.after == ("network.target",)
    # Without the [jobs.windows] table the same job is refused outright.
    with pytest.raises(ValueError, match="ordering dependencies"):
        render_windows_task(replace(job, windows_overrides=None))

    rendered = render_target(manifest, "windows-user")["portable-drift.xml"]
    root = ET.fromstring(rendered)
    command = root.find(f"{NS}Actions/{NS}Exec/{NS}Command").text
    assert command == "C:\\Windows\\System32\\cmd.exe"


def test_windows_overrides_do_not_leak_into_other_targets():
    manifest = _portable()

    units = render_target(manifest, "systemd-user")
    assert (
        "ExecStart=/usr/bin/bash scripts/report_drift.sh"
        in units["portable-drift.service"].splitlines()
    )

    plist = render_target(manifest, "launchd-user")
    assert "cmd.exe" not in next(iter(plist.values()))


def test_windows_files_are_written_as_utf16_for_schtasks(tmp_path):
    """schtasks /Create /XML rejects UTF-8 task files as malformed."""
    rendered = render_target(_portable(), "windows-user")
    written = write_windows_files(tmp_path, rendered)

    raw = written[0].read_bytes()
    assert raw[:2] == b"\xff\xfe"  # UTF-16 LE BOM
    assert raw.decode("utf-16").startswith('<?xml version="1.0" encoding="UTF-16"?>')


def test_default_target_detects_windows():
    assert default_target("win32") == "windows-user"
    assert default_target("cygwin") == "windows-user"
    # Still refuses to guess where there is no native scheduler.
    assert default_target("freebsd12") is None
