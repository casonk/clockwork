import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from clockwork.cli import default_target, main
from clockwork.manifest import load_manifest
from clockwork.model import JobSpec, Manifest, TimerSpec
from clockwork.render import (
    launchd_label,
    render_launchd_plist,
    render_target,
    write_launchd_files,
)


def _job(**overrides) -> JobSpec:
    values = {
        "name": "clockwork-web-macos",
        "description": "Clockwork macOS web service",
        "scope": "user",
        "service_type": "simple",
        "working_directory": "%h/dev/util-repos/clockwork",
        "exec_start": "/bin/echo ready",
        "environment": {"CLOCKWORK_WEB_HOST": "127.0.0.1", "PORTFOLIO_ROOT": "%h/dev"},
        "restart": "on-failure",
        "restart_sec": "5",
        "standard_output": "journal",
        "standard_error": "journal",
        "service_install_wanted_by": ("default.target",),
    }
    values.update(overrides)
    return JobSpec(**values)


def test_launchd_plist_is_deterministic_and_uses_safe_label_and_paths():
    job = _job()

    first = render_launchd_plist(job, home=Path("/Users/tester"))
    second = render_launchd_plist(job, home=Path("/Users/tester"))
    payload = plistlib.loads(first.encode("utf-8"))

    assert first == second
    assert launchd_label(job) == "io.github.casonk.clockwork.clockwork-web-macos"
    assert payload["Label"] == "io.github.casonk.clockwork.clockwork-web-macos"
    assert payload["ProgramArguments"] == ["/bin/echo", "ready"]
    assert payload["WorkingDirectory"] == "/Users/tester/dev/util-repos/clockwork"
    assert payload["EnvironmentVariables"]["PORTFOLIO_ROOT"] == "/Users/tester/dev"
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["RunAtLoad"] is True
    assert payload["ThrottleInterval"] == 5
    assert payload["StandardOutPath"].endswith("clockwork-web-macos.stdout.log")


def test_launchd_explicit_downstream_label_is_preserved_in_filename_and_plist():
    job = _job(launchd_label="io.github.casonk.traction-control.clockwork-web-macos")
    manifest = Manifest(path="test.toml", jobs=(job,))

    rendered = render_target(manifest, "launchd-user")

    assert list(rendered) == ["io.github.casonk.traction-control.clockwork-web-macos.plist"]
    payload = plistlib.loads(next(iter(rendered.values())).encode("utf-8"))
    assert payload["Label"] == "io.github.casonk.traction-control.clockwork-web-macos"


@pytest.mark.parametrize(
    "label",
    [
        "",
        "not-qualified",
        "io.github.casonk.traction-control.other-job",
        "../unsafe",
        f"{'a' * 230}.clockwork-web-macos",
    ],
)
def test_launchd_rejects_invalid_or_mismatched_explicit_labels(label):
    with pytest.raises(ValueError, match="launchd_label|launchd label"):
        render_launchd_plist(_job(launchd_label=label), home=Path("/Users/tester"))


def test_launchd_calendar_maps_weekday_without_systemd_unit_artifacts():
    job = _job(
        service_type="oneshot",
        restart=None,
        restart_sec=None,
        service_install_wanted_by=(),
        timer=TimerSpec(kind="calendar", on_calendar="Sun *-*-* 02:15:00", persistent=True),
    )

    payload = plistlib.loads(render_launchd_plist(job, home=Path("/Users/tester")).encode("utf-8"))

    assert payload["StartCalendarInterval"] == {
        "Hour": 2,
        "Minute": 15,
        "Second": 0,
        "Weekday": 0,
    }
    assert "RunAtLoad" not in payload


def test_launchd_stepped_calendar_maps_each_hour():
    job = _job(
        service_type="oneshot",
        restart=None,
        restart_sec=None,
        service_install_wanted_by=(),
        timer=TimerSpec(kind="calendar", on_calendar="*-*-* 02/6:00"),
    )

    payload = plistlib.loads(render_launchd_plist(job, home=Path("/Users/tester")).encode("utf-8"))

    assert [entry["Hour"] for entry in payload["StartCalendarInterval"]] == [2, 8, 14, 20]


def test_launchd_calendar_maps_multiple_weekdays_without_duplicate_labels():
    job = _job(
        service_type="oneshot",
        restart=None,
        restart_sec=None,
        service_install_wanted_by=(),
        timer=TimerSpec(kind="calendar", on_calendar="Wed,Sun *-*-* 03:00:00"),
    )

    payload = plistlib.loads(render_launchd_plist(job, home=Path("/Users/tester")).encode("utf-8"))

    assert payload["StartCalendarInterval"] == [
        {"Hour": 3, "Minute": 0, "Second": 0, "Weekday": 3},
        {"Hour": 3, "Minute": 0, "Second": 0, "Weekday": 0},
    ]


def test_launchd_run_at_load_is_an_explicit_interval_adapter_contract():
    job = _job(
        service_type="oneshot",
        restart=None,
        restart_sec=None,
        service_install_wanted_by=(),
        launchd_run_at_load=True,
        timer=TimerSpec(kind="interval", on_unit_active_sec="1d"),
    )

    payload = plistlib.loads(render_launchd_plist(job, home=Path("/Users/tester")).encode("utf-8"))

    assert payload["StartInterval"] == 86400
    assert payload["RunAtLoad"] is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": "../../bad"}, "names must match"),
        ({"scope": "system"}, "refuses system scope"),
        ({"after": ("network.target",)}, "ordering dependencies"),
        (
            {"timer": TimerSpec(kind="interval", on_boot_sec="5m", on_unit_active_sec="1h")},
            "no delayed-login equivalent",
        ),
        (
            {
                "timer": TimerSpec(
                    kind="calendar",
                    on_calendar="*-*-* 02:00:00",
                    randomized_delay_sec="10m",
                )
            },
            "randomized_delay_sec",
        ),
        ({"restart_sec": "0"}, "greater than zero"),
    ],
)
def test_launchd_rejects_lossy_or_unsafe_mappings(overrides, message):
    with pytest.raises(ValueError, match=message):
        render_launchd_plist(_job(**overrides), home=Path("/Users/tester"))


def test_launchd_environment_file_is_loaded_without_embedding_secret(tmp_path):
    env_file = tmp_path / "clockwork-web.env"
    env_file.write_text('CLOCKWORK_WEB_SECRET="local secret value"\n', encoding="utf-8")
    env_file.chmod(0o600)
    job = _job(
        exec_start="/usr/bin/env",
        environment_files=(str(env_file),),
    )

    text = render_launchd_plist(job, home=tmp_path)
    payload = plistlib.loads(text.encode("utf-8"))

    assert "local secret value" not in text
    assert str(env_file) in text
    command = [sys.executable, *payload["ProgramArguments"][1:]]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "CLOCKWORK_WEB_SECRET=local secret value" in proc.stdout


@pytest.mark.parametrize("unsafe_kind", ["readable", "writable", "symlink"])
def test_launchd_environment_loader_rejects_unsafe_secret_files(tmp_path, unsafe_kind):
    source = tmp_path / "source.env"
    source.write_text("CLOCKWORK_WEB_SECRET=not-a-real-secret\n", encoding="utf-8")
    source.chmod(0o600)
    env_file = source
    if unsafe_kind == "readable":
        source.chmod(0o644)
    elif unsafe_kind == "writable":
        source.chmod(0o622)
    else:
        env_file = tmp_path / "linked.env"
        env_file.symlink_to(source)
    job = _job(exec_start="/usr/bin/env", environment_files=(str(env_file),))
    payload = plistlib.loads(render_launchd_plist(job, home=tmp_path).encode("utf-8"))

    command = [sys.executable, *payload["ProgramArguments"][1:]]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)

    assert proc.returncode != 0
    assert "refusing" in proc.stderr


def test_launchd_target_renders_only_user_jobs():
    manifest = Manifest(
        path="test.toml",
        jobs=(
            _job(),
            _job(
                name="system-job",
                scope="system",
                restart=None,
                restart_sec=None,
                service_install_wanted_by=(),
            ),
        ),
    )

    rendered = render_target(manifest, "launchd-user")

    assert list(rendered) == ["io.github.casonk.clockwork.clockwork-web-macos.plist"]


def test_launchd_install_writes_owner_only_plist_without_activation(tmp_path, capsys, monkeypatch):
    manifest_path = (
        Path(__file__).resolve().parent.parent / "config" / "macos" / ("clockwork-web.toml.example")
    )
    output_dir = tmp_path / "LaunchAgents"
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code = main(
        [
            "install",
            "--manifest",
            str(manifest_path),
            "--target",
            "launchd-user",
            "--unit-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    plist = output_dir / "io.github.casonk.clockwork.clockwork-web-macos.plist"
    assert exit_code == 0
    assert plist.exists()
    assert plist.stat().st_mode & 0o777 == 0o600
    payload = plistlib.loads(plist.read_bytes())
    assert payload["EnvironmentVariables"]["CLOCKWORK_WEB_AUTOGENERATE_CRON"] == "0"
    assert "launchctl bootstrap" in captured.out
    assert "review before running" in captured.out
    assert os.path.exists(tmp_path / "LaunchAgents")


def test_launchd_atomic_write_replaces_symlink_without_touching_its_target(tmp_path):
    output_dir = tmp_path / "LaunchAgents"
    output_dir.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("leave me alone", encoding="utf-8")
    target = output_dir / "io.github.casonk.clockwork.safe.plist"
    target.symlink_to(victim)

    written = write_launchd_files(output_dir, {target.name: "safe plist\n"})

    assert written == [target]
    assert target.is_file()
    assert not target.is_symlink()
    assert target.read_text(encoding="utf-8") == "safe plist\n"
    assert victim.read_text(encoding="utf-8") == "leave me alone"


def _portable_manifest_path() -> Path:
    return Path(__file__).resolve().parent.parent / "examples" / "portable" / "weekly-drift.toml"


def test_launchd_overrides_withdraw_systemd_only_fields():
    """A job with `after` and jitter is renderable for launchd via overrides.

    Both of these previously made a manifest unrenderable for launchd-user, so
    a job wanting either could not be installed on macOS at all.
    """
    manifest = load_manifest(_portable_manifest_path())
    job = manifest.jobs[0]

    # The job still declares them: the overrides withdraw, they do not delete.
    assert job.after == ("network.target",)
    assert job.timer is not None and job.timer.randomized_delay_sec == "600"

    plist = plistlib.loads(
        render_launchd_plist(job, home=Path("/Users/tester")).encode("utf-8")
    )
    assert plist["StartCalendarInterval"] == {"Hour": 4, "Minute": 0, "Second": 0, "Weekday": 0}
    # /usr/bin/bash does not exist on macOS; the override supplies /bin/bash.
    assert plist["ProgramArguments"][0] == "/bin/bash"


def test_launchd_overrides_do_not_leak_into_systemd_or_cron():
    manifest = load_manifest(_portable_manifest_path())

    units = render_target(manifest, "systemd-user")
    service = units["portable-drift.service"]
    timer = units["portable-drift.timer"]

    assert "After=network.target" in service
    # Match the whole line: "/bin/bash ..." is a substring of "/usr/bin/bash ...",
    # so a naive containment check passes even when the override has leaked.
    assert "ExecStart=/usr/bin/bash scripts/report_drift.sh" in service.splitlines()
    assert "ExecStart=/bin/bash scripts/report_drift.sh" not in service.splitlines()
    assert "RandomizedDelaySec=600" in timer


def test_launchd_override_rejects_unknown_keys(tmp_path):
    manifest = tmp_path / "bad.toml"
    manifest.write_text(
        """
[[jobs]]
name = "x"
description = "x"
scope = "user"
exec_start = "/bin/echo hi"

[jobs.launchd]
exec_startt = "/bin/echo typo"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported launchd override keys: exec_startt"):
        load_manifest(manifest)


def test_default_target_follows_the_platform():
    assert default_target("darwin") == "launchd-user"
    assert default_target("linux") == "systemd-user"
