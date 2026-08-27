import json
import os
import plistlib
import sys
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import tomlkit

from clockwork.manifest import load_manifest
from clockwork.model import TimerSpec
from clockwork.render import _LAUNCHD_ENV_LOADER, render_launchd_plist

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_APP_PATH = _WEB_DIR / "app.py"
_SPEC = spec_from_file_location("clockwork_web_app", _APP_PATH)
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
os.environ["CLOCKWORK_SCHEDULER_BACKEND"] = "systemd"
web_app = module_from_spec(_SPEC)
_SPEC.loader.exec_module(web_app)


def test_air_webterm_portal_url_uses_home_not_raw_ttyd():
    service = {
        "name": "pit-box-webterm",
        "port": 7681,
        "macos_edge_role": "webterm",
        "macos_edge_listen_port": 8445,
    }

    assert web_app._svc_url(service, air_wireguard_ip="10.99.0.254") == "https://10.99.0.254:8445/"
    assert (
        web_app._svc_url(service, air_wireguard_ip="10.99.0.254", local_air=True)
        == "http://127.0.0.1:7680/"
    )


def test_magneto_target_selection_uses_local_for_current_and_mtls_for_remote(tmp_path, monkeypatch):
    hosts_file = tmp_path / "torrent-hosts.json"
    hosts_file.write_text(
        '{"hosts":[{"id":"air","label":"MacBook Air","url":"https://10.99.0.254:8446/","current":true},{"id":"home","label":"Home server","url":"https://torrents.home.internal/","current":false}]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "MAGNETO_HOSTS_FILE", str(hosts_file))
    monkeypatch.setattr(web_app, "MAGNETO_URL", "http://127.0.0.1:5400")

    assert web_app._magneto_target_url("air") == ("http://127.0.0.1:5400", "")
    assert web_app._magneto_target_url("home") == ("https://torrents.home.internal", "")


def test_torrent_add_passes_explicit_host_to_magneto(monkeypatch):
    captured = {}

    def fake_proxy(magnet, host):
        captured["magnet"] = magnet
        captured["host"] = host
        return True, ""

    monkeypatch.setattr(web_app, "_proxy_to_magneto", fake_proxy)

    response = web_app.app.test_client().post(
        "/api/torrent-add",
        json={"magnet": "magnet:?xt=urn:btih:abcd", "host": "home"},
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 200
    assert captured == {"magnet": "magnet:?xt=urn:btih:abcd", "host": "home"}


def _write_bug_sweep_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """[[jobs]]
name = "bug-sweep-agentic"
description = "Daily agentic review of clean code repos for potential bugs and regressions"
scope = "user"
service_type = "oneshot"
working_directory = "%h/git/util-repos/traction-control"
exec_start = "%h/git/util-repos/traction-control/scripts/bug_sweep_agentic.sh"
environment_files = ["-%h/.config/traction-control/bug-sweep-agentic.env"]

[jobs.environment]
PORTFOLIO_ROOT = "%h/git"
BUG_SWEEP_AGENTIC_PROVIDER = "auto"
BUG_SWEEP_AGENTIC_MODEL = ""
""",
        encoding="utf-8",
    )


def _write_interval_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """[[jobs]]
name = "shock-relay-gmail-digest"
description = "Send queued Gmail notification digest emails"
scope = "user"
service_type = "oneshot"
working_directory = "%h/git/util-repos/shock-relay"
exec_start = "/usr/bin/env python3 %h/git/util-repos/shock-relay/services/gmail-imap/send_digest.py"

[jobs.timer]
kind = "interval"
on_boot_sec = "5m"
on_unit_active_sec = "1h"
persistent = true
""",
        encoding="utf-8",
    )


def _write_launchd_manifest(
    path: Path,
    *,
    name: str = "clockwork-web-macos",
    command: str = "/bin/echo ready",
    label: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    label_line = f'launchd_label = "{label}"\n' if label else ""
    path.write_text(
        f"""[[jobs]]
name = "{name}"
{label_line}description = "Test launchd job"
scope = "user"
service_type = "oneshot"
exec_start = "{command}"
""",
        encoding="utf-8",
    )


def _write_launchd_plist(
    path: Path,
    *,
    label: str,
    job_name: str,
    arguments: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": label,
        "ProgramArguments": arguments or ["/bin/echo", "ready"],
        "StandardOutPath": str(path.parents[1] / "Logs" / f"{job_name}.stdout.log"),
        "StandardErrorPath": str(path.parents[1] / "Logs" / f"{job_name}.stderr.log"),
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))
    path.chmod(0o600)


def _clockwork_loader_arguments(runtime_arguments: list[str]) -> list[str]:
    config = {
        "argv": runtime_arguments,
        "environment_files": ["-/Users/tester/.config/traction-control/test.env"],
    }
    return [
        "/usr/bin/python3",
        "-c",
        _LAUNCHD_ENV_LOADER,
        json.dumps(config, sort_keys=True, separators=(",", ":")),
    ]


def _configure_target_switch_test(tmp_path: Path, monkeypatch) -> Path:
    examples_dir = tmp_path / "examples"
    manifest_path = examples_dir / "sample" / "dual.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        """[[jobs]]
name = "dual-job"
description = "Dual scheduler target test"
scope = "user"
service_type = "oneshot"
exec_start = "/bin/echo ready"

[jobs.timer]
kind = "calendar"
on_calendar = "*-*-* 02:00:00"

[jobs.cron]
expression = "0 2 * * *"
command = "/bin/echo ready"
""",
        encoding="utf-8",
    )
    state_file = tmp_path / "web-state.json"
    state_file.write_text(
        json.dumps(
            {
                "repos": {"sample": {"enabled": True}},
                "jobs": {
                    "sample/dual.toml:dual-job": {
                        "enabled": True,
                        "target": "systemd",
                    }
                },
                "global_target": "systemd",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "systemd")
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    monkeypatch.setattr(web_app, "STATE_FILE", state_file)
    return state_file


def test_scan_repos_includes_agent_provider_model_and_env_file(tmp_path, monkeypatch):
    manifest_path = tmp_path / "traction-control" / "bug-sweep-agentic.toml"
    _write_bug_sweep_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    repos = web_app.scan_repos()

    job = repos["traction-control"]["manifests"][0]["jobs"][0]
    assert job["provider_env_key"] == "BUG_SWEEP_AGENTIC_PROVIDER"
    assert job["provider_value"] == "auto"
    assert job["model_env_key"] == "BUG_SWEEP_AGENTIC_MODEL"
    assert job["model_value"] == ""
    assert job["environment_files"] == ["-%h/.config/traction-control/bug-sweep-agentic.env"]


def test_edit_job_updates_agent_provider_and_model_defaults(tmp_path, monkeypatch):
    manifest_path = tmp_path / "traction-control" / "bug-sweep-agentic.toml"
    _write_bug_sweep_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    client = web_app.app.test_client()
    response = client.post(
        "/edit/job",
        data={
            "manifest_path": "traction-control/bug-sweep-agentic.toml",
            "job_name": "bug-sweep-agentic",
            "description": "Daily bug review",
            "exec_start": "%h/git/util-repos/traction-control/scripts/bug_sweep_agentic.sh",
            "working_directory": "%h/git/util-repos/traction-control",
            "provider_env_key": "BUG_SWEEP_AGENTIC_PROVIDER",
            "provider_value": "claude",
            "model_env_key": "BUG_SWEEP_AGENTIC_MODEL",
            "model_value": "claude-sonnet-4-6",
        },
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302

    doc = tomlkit.loads(manifest_path.read_text(encoding="utf-8"))
    job = doc["jobs"][0]
    assert job["description"] == "Daily bug review"
    assert job["environment"]["BUG_SWEEP_AGENTIC_PROVIDER"] == "claude"
    assert job["environment"]["BUG_SWEEP_AGENTIC_MODEL"] == "claude-sonnet-4-6"


def test_edit_job_updates_interval_timer_cadence(tmp_path, monkeypatch):
    manifest_path = tmp_path / "shock-relay" / "gmail-digest.toml"
    _write_interval_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    client = web_app.app.test_client()
    response = client.post(
        "/edit/job",
        data={
            "manifest_path": "shock-relay/gmail-digest.toml",
            "job_name": "shock-relay-gmail-digest",
            "description": "Send queued digest emails",
            "exec_start": "/usr/bin/env python3 %h/git/util-repos/shock-relay/services/gmail-imap/send_digest.py",
            "working_directory": "%h/git/util-repos/shock-relay",
            "on_boot_sec": "10m",
            "on_unit_active_sec": "30m",
        },
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302

    doc = tomlkit.loads(manifest_path.read_text(encoding="utf-8"))
    timer = doc["jobs"][0]["timer"]
    assert timer["on_boot_sec"] == "10m"
    assert timer["on_unit_active_sec"] == "30m"


def test_state_changing_post_allows_forwarded_same_origin(tmp_path, monkeypatch):
    manifest_path = tmp_path / "shock-relay" / "gmail-digest.toml"
    _write_interval_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    client = web_app.app.test_client()
    response = client.post(
        "/edit/job",
        data={
            "manifest_path": "shock-relay/gmail-digest.toml",
            "job_name": "shock-relay-gmail-digest",
            "description": "Send queued digest emails",
            "exec_start": "/usr/bin/env python3 %h/git/util-repos/shock-relay/services/gmail-imap/send_digest.py",
            "working_directory": "%h/git/util-repos/shock-relay",
        },
        headers={
            "Host": "127.0.0.1:5000",
            "Origin": "https://clockwork.example",
            "X-Forwarded-Host": "clockwork.example",
        },
    )

    assert response.status_code == 302


def test_state_changing_post_allows_same_origin_fetch_metadata_without_origin(
    tmp_path, monkeypatch
):
    manifest_path = tmp_path / "shock-relay" / "gmail-digest.toml"
    _write_interval_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    client = web_app.app.test_client()
    response = client.post(
        "/edit/job",
        data={
            "manifest_path": "shock-relay/gmail-digest.toml",
            "job_name": "shock-relay-gmail-digest",
            "description": "Send queued digest emails",
            "exec_start": "/usr/bin/env python3 %h/git/util-repos/shock-relay/services/gmail-imap/send_digest.py",
            "working_directory": "%h/git/util-repos/shock-relay",
        },
        headers={
            "Host": "127.0.0.1:5000",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert response.status_code == 302


def test_state_changing_post_allows_valid_csrf_token_without_origin(tmp_path, monkeypatch):
    manifest_path = tmp_path / "shock-relay" / "gmail-digest.toml"
    _write_interval_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    client = web_app.app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-csrf-token"

    response = client.post(
        "/edit/job",
        data={
            "manifest_path": "shock-relay/gmail-digest.toml",
            "job_name": "shock-relay-gmail-digest",
            "description": "Send queued digest emails",
            "exec_start": "/usr/bin/env python3 %h/git/util-repos/shock-relay/services/gmail-imap/send_digest.py",
            "working_directory": "%h/git/util-repos/shock-relay",
            "csrf_token": "test-csrf-token",
        },
    )

    assert response.status_code == 302


def test_state_changing_post_rejects_missing_csrf_and_origin(tmp_path, monkeypatch):
    manifest_path = tmp_path / "shock-relay" / "gmail-digest.toml"
    _write_interval_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    client = web_app.app.test_client()
    response = client.post(
        "/edit/job",
        data={
            "manifest_path": "shock-relay/gmail-digest.toml",
            "job_name": "shock-relay-gmail-digest",
            "description": "Send queued digest emails",
            "exec_start": "/usr/bin/env python3 %h/git/util-repos/shock-relay/services/gmail-imap/send_digest.py",
            "working_directory": "%h/git/util-repos/shock-relay",
        },
    )

    assert response.status_code == 403


def test_fetch_all_statuses_uses_cron_status_for_cron_only_and_cron_selected_jobs(monkeypatch):
    cron_calls: list[str] = []
    unit_calls: list[str] = []

    def fake_cron_status(job, now=None):
        cron_calls.append(job["name"])
        return {
            "active": None,
            "enabled": None,
            "active_state": "cron",
            "enabled_state": "cron",
            "next_run_text": f"next {job['name']}",
            "next_run_iso": "",
        }

    def fake_unit_status(unit, scope="user"):
        unit_calls.append(unit)
        return {
            "active": False,
            "enabled": True,
            "active_state": "inactive",
            "enabled_state": "enabled",
            "next_run_text": "",
            "next_run_iso": "",
        }

    monkeypatch.setattr(web_app, "build_cron_status", fake_cron_status)
    monkeypatch.setattr(web_app, "unit_status", fake_unit_status)

    statuses = web_app.fetch_all_statuses(
        {
            "archility": {
                "manifests": [
                    {
                        "path": "archility/archility-daily.toml",
                        "jobs": [
                            {
                                "name": "cron-only",
                                "scope": "user",
                                "target": "cron",
                                "timer": None,
                                "cron": {"expression": "0 2 * * *"},
                            },
                            {
                                "name": "dual-cron",
                                "scope": "user",
                                "target": "cron",
                                "timer": {"kind": "calendar", "on_calendar": "*-*-* 02:00:00"},
                                "timer_name": "dual-cron.timer",
                                "cron": {"expression": "0 2 * * *"},
                            },
                            {
                                "name": "dual-systemd",
                                "scope": "user",
                                "target": "systemd",
                                "timer": {"kind": "calendar", "on_calendar": "*-*-* 03:00:00"},
                                "timer_name": "dual-systemd.timer",
                                "cron": {"expression": "0 3 * * *"},
                            },
                        ],
                    }
                ]
            }
        }
    )

    assert cron_calls == ["cron-only", "dual-cron", "dual-systemd"]
    assert unit_calls == ["dual-systemd.timer"]
    assert statuses["archility/archility-daily.toml:cron-only"]["active_state"] == "cron"
    assert statuses["archility/archility-daily.toml:dual-cron"]["next_run_text"] == "next dual-cron"
    # systemd returned no next_run_iso, so cron expression is used as fallback
    assert (
        statuses["archility/archility-daily.toml:dual-systemd"]["next_run_text"]
        == "next dual-systemd"
    )
    assert statuses["archility/archility-daily.toml:dual-systemd"]["active_state"] == "inactive"


def test_launchd_status_uses_launchctl_and_never_systemctl(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    calls = []

    def fake_launchctl(*args, scope="user"):
        calls.append((args, scope))
        return 0, "state = running\nlast exit code = 0"

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        web_app,
        "_systemctl",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("systemctl called")),
    )

    status = web_app.unit_status("io.github.casonk.clockwork.clockwork-web-macos", scope="user")

    assert status["active"] is True
    assert status["enabled"] is True
    assert status["active_state"] == "running"
    assert calls == [
        (
            (
                "print",
                f"gui/{os.getuid()}/io.github.casonk.clockwork.clockwork-web-macos",
            ),
            "user",
        )
    ]


def test_launchd_ui_uses_explicit_downstream_label_for_status_and_control(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def fake_launchctl(*args, scope="user"):
        calls.append((args, scope))
        return 0, "state = waiting"

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)
    job = {
        "name": "bug-sweep-agentic",
        "scope": "user",
        "launchd_label": "io.github.casonk.traction-control.bug-sweep-agentic",
        "timer": {"kind": "interval", "on_unit_active_sec": "1d"},
        "cron": None,
    }

    label = web_app.primary_unit(job)
    _write_launchd_plist(
        tmp_path / "Library" / "LaunchAgents" / f"{label}.plist",
        label=label,
        job_name=job["name"],
        arguments=[
            "/bin/bash",
            "/test/scripts/run_traction_control_job.sh",
            "--job",
            job["name"],
        ],
    )
    status = web_app.unit_status(label)
    disabled = web_app.do_disable(job)

    service = f"gui/{os.getuid()}/{label}"
    assert label == "io.github.casonk.traction-control.bug-sweep-agentic"
    assert status["enabled"] is True
    assert calls == [
        (("print", service), "user"),
        (("print", service), "user"),
        (("bootout", service), "user"),
        (("disable", service), "user"),
    ]
    assert all(result[1] == 0 for result in disabled)


def test_actual_traction_manifests_compose_ui_and_renderer_labels(monkeypatch):
    examples_dir = Path(__file__).resolve().parent.parent / "examples"
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)

    repos = web_app.scan_repos()
    ui_jobs = {
        job["name"]: job
        for manifest in repos["traction-control"]["manifests"]
        for job in manifest["jobs"]
    }
    expected = {
        "archility-daily",
        "archility-weekly",
        "bug-sweep-agentic",
        "ci-repair-agentic",
        "ci-repair-agentic-discovery",
        "ci-repair-agentic-repair",
        "portfolio-audit-daily",
        "refs-audit-agentic",
        "tachometer-disk-pressure-agentic",
        "template-consolidation-agentic",
    }
    assert set(ui_jobs) == expected

    rendered_labels = {}
    for manifest_path in sorted((examples_dir / "traction-control").glob("*.toml")):
        manifest = load_manifest(manifest_path)
        for job in manifest.jobs:
            timer = job.timer
            if timer is not None:
                timer = TimerSpec(
                    kind=timer.kind,
                    on_calendar=timer.on_calendar,
                    on_unit_active_sec=timer.on_unit_active_sec,
                    persistent=timer.persistent,
                )
            label_only_adapter = replace(job, after=(), wants=(), timer=timer)
            payload = plistlib.loads(
                render_launchd_plist(label_only_adapter, home=Path("/Users/tester")).encode()
            )
            rendered_labels[job.name] = payload["Label"]

    ui_labels = {name: web_app.primary_unit(job) for name, job in ui_jobs.items()}
    assert ui_labels == rendered_labels
    assert len(set(ui_labels.values())) == len(ui_labels)


def test_actual_traction_manifests_keep_lossy_launchd_semantics_blocked():
    examples_dir = Path(__file__).resolve().parent.parent / "examples" / "traction-control"

    for manifest_path in sorted(examples_dir.glob("*.toml")):
        for job in load_manifest(manifest_path).jobs:
            with pytest.raises(ValueError, match="ordering dependencies|randomized_delay_sec"):
                render_launchd_plist(job, home=Path("/Users/tester"))


def test_launchd_system_scope_fails_closed_without_commands(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setattr(
        web_app,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("command executed")),
    )
    job = {
        "name": "system-job",
        "scope": "system",
        "timer": None,
        "cron": None,
    }

    result = web_app.do_enable(Path("/tmp/system-job.toml"), job)
    status = web_app.unit_status("system-job.service", scope="system")

    assert result[0][1] == 78
    assert "system-scope" in result[0][2]
    assert status["active"] is None
    assert status["active_state"] == "unsupported-system-scope"


def test_launchd_disable_then_enable_clears_disabled_state_before_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    commands = []
    install_commands = []
    manifest_path = tmp_path / "clockwork-web.local.toml"
    _write_launchd_manifest(manifest_path)

    def fake_launchctl(*args, scope="user"):
        commands.append(args)
        if args[0] == "print":
            return 113, "not loaded"
        return 0, ""

    def fake_run(*args, **kwargs):
        install_commands.append(args)
        label = "io.github.casonk.clockwork.clockwork-web-macos"
        plist_path = tmp_path / "Library" / "LaunchAgents" / f"{label}.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(
            render_launchd_plist(load_manifest(manifest_path).jobs[0]), encoding="utf-8"
        )
        plist_path.chmod(0o600)
        return 0, "installed"

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)
    monkeypatch.setattr(web_app, "_run", fake_run)
    job = {
        "name": "clockwork-web-macos",
        "scope": "user",
        "service_name": "",
        "timer_name": "",
        "timer": None,
        "cron": None,
    }

    disable_results = web_app.do_disable(job)
    enable_results = web_app.do_enable(manifest_path, job)

    service = f"gui/{os.getuid()}/io.github.casonk.clockwork.clockwork-web-macos"
    assert commands == [
        ("print", service),
        ("disable", service),
        ("print", service),
        ("enable", service),
        (
            "bootstrap",
            f"gui/{os.getuid()}",
            str(
                tmp_path
                / "Library"
                / "LaunchAgents"
                / "io.github.casonk.clockwork.clockwork-web-macos.plist"
            ),
        ),
    ]
    assert all(result[1] == 0 for result in disable_results)
    assert all(result[1] == 0 for result in enable_results)
    assert install_commands == [
        (
            sys.executable,
            "-m",
            "clockwork.cli",
            "install",
            "--manifest",
            str(manifest_path),
            "--target",
            "launchd-user",
            "--job",
            "clockwork-web-macos",
        )
    ]
    assert "clockwork" not in install_commands[0]


def test_launchd_systemd_rich_manifest_requires_an_installed_reviewed_adapter(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    examples_dir = Path(__file__).resolve().parent.parent / "examples"
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    job = web_app.scan_repos()["traction-control"]["manifests"][0]["jobs"][0]
    manifest_path = examples_dir / "traction-control" / "archility-daily.toml"
    assert job["name"] == "archility-daily"
    monkeypatch.setattr(
        web_app,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("command executed")),
    )

    results = web_app.do_enable(manifest_path, job)

    assert not web_app._results_succeeded(results)
    assert results[0][0] == "reuse downstream LaunchAgent adapter"
    assert "install the reviewed adapter" in results[0][2]


def test_launchd_systemd_rich_manifest_reuses_only_matching_traction_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    examples_dir = Path(__file__).resolve().parent.parent / "examples"
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    manifest = next(
        manifest
        for manifest in web_app.scan_repos()["traction-control"]["manifests"]
        if manifest["path"] == "traction-control/archility-daily.toml"
    )
    job = manifest["jobs"][0]
    label = job["launchd_label"]
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{label}.plist"
    _write_launchd_plist(
        plist_path,
        label=label,
        job_name=job["name"],
        arguments=[
            "/bin/bash",
            "/portfolio/traction-control/scripts/run_traction_control_job.sh",
            "--job",
            job["name"],
            "--delay-seconds",
            "300",
        ],
    )
    commands = []

    def fake_launchctl(*args, scope="user"):
        commands.append(args)
        if args[0] == "print":
            return 113, "Could not find service"
        return 0, ""

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        web_app,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("install executed")),
    )

    results = web_app.do_enable(examples_dir / "traction-control" / "archility-daily.toml", job)

    service = f"gui/{os.getuid()}/{label}"
    assert web_app._results_succeeded(results)
    assert commands == [
        ("print", service),
        ("enable", service),
        ("bootstrap", f"gui/{os.getuid()}", str(plist_path)),
    ]


def test_launchd_systemd_rich_manifest_accepts_exact_clockwork_loader_adapter(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    examples_dir = Path(__file__).resolve().parent.parent / "examples"
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    manifest = next(
        manifest
        for manifest in web_app.scan_repos()["traction-control"]["manifests"]
        if manifest["path"] == "traction-control/bug-sweep-agentic.toml"
    )
    job = manifest["jobs"][0]
    label = job["launchd_label"]
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{label}.plist"
    _write_launchd_plist(
        plist_path,
        label=label,
        job_name=job["name"],
        arguments=_clockwork_loader_arguments(
            [
                "/bin/bash",
                "/portfolio/traction-control/scripts/run_traction_control_job.sh",
                "--job",
                job["name"],
                "--schedule-kind",
                "interval",
            ]
        ),
    )

    def fake_launchctl(*args, scope="user"):
        if args[0] == "print":
            return 113, "Could not find service"
        return 0, ""

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        web_app,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("install executed")),
    )

    results = web_app.do_enable(examples_dir / "traction-control" / "bug-sweep-agentic.toml", job)

    assert web_app._results_succeeded(results)


def test_launchd_rejects_adapter_whose_embedded_job_identity_is_wrong(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    examples_dir = Path(__file__).resolve().parent.parent / "examples"
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    manifest = next(
        manifest
        for manifest in web_app.scan_repos()["traction-control"]["manifests"]
        if manifest["path"] == "traction-control/archility-daily.toml"
    )
    job = manifest["jobs"][0]
    label = job["launchd_label"]
    _write_launchd_plist(
        tmp_path / "Library" / "LaunchAgents" / f"{label}.plist",
        label=label,
        job_name=job["name"],
        arguments=_clockwork_loader_arguments(
            [
                "/bin/bash",
                "/portfolio/traction-control/scripts/run_traction_control_job.sh",
                "--job",
                "different-job",
            ]
        ),
    )
    monkeypatch.setattr(
        web_app,
        "_launchctl",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("launchctl executed")),
    )

    results = web_app.do_enable(examples_dir / "traction-control" / "archility-daily.toml", job)

    assert not web_app._results_succeeded(results)
    assert "--job identity does not match" in results[0][2]


def test_launchd_rejects_clockwork_loader_with_wrong_nested_traction_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    examples_dir = Path(__file__).resolve().parent.parent / "examples"
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    manifest = next(
        manifest
        for manifest in web_app.scan_repos()["traction-control"]["manifests"]
        if manifest["path"] == "traction-control/bug-sweep-agentic.toml"
    )
    job = manifest["jobs"][0]
    label = job["launchd_label"]
    _write_launchd_plist(
        tmp_path / "Library" / "LaunchAgents" / f"{label}.plist",
        label=label,
        job_name=job["name"],
        arguments=_clockwork_loader_arguments(
            [
                "/bin/bash",
                "/portfolio/traction-control/scripts/not_the_reviewed_wrapper.sh",
                "--job",
                job["name"],
            ]
        ),
    )
    monkeypatch.setattr(
        web_app,
        "_launchctl",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("launchctl executed")),
    )

    results = web_app.do_enable(examples_dir / "traction-control" / "bug-sweep-agentic.toml", job)

    assert not web_app._results_succeeded(results)
    assert "exact absolute runtime wrapper" in results[0][2]


def test_traction_adapter_rejects_malformed_or_extended_clockwork_loader_shapes():
    runtime_arguments = [
        "/bin/bash",
        "/portfolio/traction-control/scripts/run_traction_control_job.sh",
        "--job",
        "bug-sweep-agentic",
    ]
    valid = _clockwork_loader_arguments(runtime_arguments)
    extended_config = {
        "argv": runtime_arguments,
        "environment_files": ["-/Users/tester/.config/traction-control/test.env"],
        "extra": True,
    }
    malformed = [
        [*valid, "extra-outer-argument"],
        ["/usr/bin/python3", "-c", "print('not Clockwork')", valid[-1]],
        [
            "/usr/bin/python3",
            "-c",
            _LAUNCHD_ENV_LOADER,
            json.dumps(extended_config, sort_keys=True, separators=(",", ":")),
        ],
        ["/usr/bin/python3", "-c", _LAUNCHD_ENV_LOADER, "{not-json"],
    ]

    for arguments in malformed:
        nested, error = web_app._traction_runtime_arguments(arguments)
        assert nested is None
        assert error


def test_launchd_loaded_plist_change_is_refused_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    manifest_path = tmp_path / "generic.toml"
    _write_launchd_manifest(manifest_path, command="/bin/echo desired")
    job = {
        "name": "clockwork-web-macos",
        "scope": "user",
        "launchd_label": None,
        "timer": None,
        "cron": None,
    }
    label = web_app.primary_unit(job)
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{label}.plist"
    _write_launchd_plist(
        plist_path,
        label=label,
        job_name=job["name"],
        arguments=["/bin/echo", "stale"],
    )
    before = plist_path.read_bytes()
    calls = []

    def fake_launchctl(*args, scope="user"):
        calls.append(args)
        return 0, "state = waiting"

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        web_app,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("install executed")),
    )

    results = web_app.do_enable(manifest_path, job)

    assert not web_app._results_succeeded(results)
    assert "already loaded; disable it before enabling" in results[-1][2]
    assert plist_path.read_bytes() == before
    assert calls == [("print", f"gui/{os.getuid()}/{label}")]


def test_launchd_loaded_matching_plist_still_requires_explicit_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    manifest_path = tmp_path / "generic.toml"
    _write_launchd_manifest(manifest_path)
    job = {
        "name": "clockwork-web-macos",
        "scope": "user",
        "launchd_label": None,
        "timer": None,
        "cron": None,
    }
    label = web_app.primary_unit(job)
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        render_launchd_plist(load_manifest(manifest_path).jobs[0]), encoding="utf-8"
    )
    plist_path.chmod(0o600)
    calls = []

    def fake_launchctl(*args, scope="user"):
        calls.append(args)
        return 0, "state = waiting"

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)
    monkeypatch.setattr(
        web_app,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("install executed")),
    )

    results = web_app.do_enable(manifest_path, job)

    service = f"gui/{os.getuid()}/{label}"
    assert not web_app._results_succeeded(results)
    assert "already loaded; disable it before enabling" in results[-1][2]
    assert calls == [("print", service)]


def test_launchd_disable_can_remove_loaded_job_even_when_plist_is_untrusted(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    job = {
        "name": "sample-job",
        "scope": "user",
        "launchd_label": None,
        "timer": None,
        "cron": None,
    }
    label = web_app.primary_unit(job)
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{label}.plist"
    _write_launchd_plist(plist_path, label=label, job_name=job["name"])
    plist_path.chmod(0o644)
    calls = []

    def fake_launchctl(*args, scope="user"):
        calls.append(args)
        return 0, "state = waiting"

    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)

    results = web_app.do_disable(job)

    service = f"gui/{os.getuid()}/{label}"
    assert web_app._results_succeeded(results)
    assert calls == [
        ("print", service),
        ("bootout", service),
        ("disable", service),
    ]


def test_systemd_ui_install_selects_only_the_requested_job(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "systemd")
    commands = []

    def fake_run(*args, **kwargs):
        commands.append(args)
        return 0, "installed"

    monkeypatch.setattr(web_app, "_run", fake_run)
    monkeypatch.setattr(web_app, "_systemctl", lambda *args, **kwargs: (0, "ok"))
    job = {
        "name": "selected-job",
        "scope": "user",
        "service_name": "",
        "timer_name": "",
        "timer": None,
        "cron": None,
    }

    results = web_app.do_enable(Path("/tmp/multi-job.toml"), job)

    assert web_app._results_succeeded(results)
    assert commands == [
        (
            "clockwork",
            "install",
            "--manifest",
            "/tmp/multi-job.toml",
            "--target",
            "systemd-user",
            "--job",
            "selected-job",
        )
    ]


def test_launchd_failed_bootstrap_restores_previous_inactive_plist(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    manifest_path = tmp_path / "generic.toml"
    _write_launchd_manifest(manifest_path, command="/bin/echo desired")
    job = {
        "name": "clockwork-web-macos",
        "scope": "user",
        "launchd_label": None,
        "timer": None,
        "cron": None,
    }
    label = web_app.primary_unit(job)
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{label}.plist"
    _write_launchd_plist(
        plist_path,
        label=label,
        job_name=job["name"],
        arguments=["/bin/echo", "previous"],
    )
    previous = plist_path.read_bytes()

    def fake_run(*args, **kwargs):
        plist_path.write_text(
            render_launchd_plist(load_manifest(manifest_path).jobs[0]), encoding="utf-8"
        )
        plist_path.chmod(0o600)
        return 0, "installed"

    def fake_launchctl(*args, scope="user"):
        if args[0] == "print":
            return 113, "not loaded"
        if args[0] == "bootstrap":
            return 5, "bootstrap failed"
        return 0, ""

    monkeypatch.setattr(web_app, "_run", fake_run)
    monkeypatch.setattr(web_app, "_launchctl", fake_launchctl)

    results = web_app.do_enable(manifest_path, job)

    assert not web_app._results_succeeded(results)
    assert any(command.startswith("launchctl bootstrap") and rc == 5 for command, rc, _ in results)
    assert plist_path.read_bytes() == previous


def test_toggle_job_failure_keeps_enabled_state(tmp_path, monkeypatch):
    examples_dir = tmp_path / "examples"
    manifest_path = examples_dir / "sample" / "job.toml"
    _write_launchd_manifest(manifest_path, name="sample-job")
    state_file = tmp_path / "web-state.json"
    state_file.write_text(
        json.dumps(
            {
                "repos": {"sample": {"enabled": True}},
                "jobs": {"sample/job.toml:sample-job": {"enabled": True}},
                "global_target": "systemd",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "systemd")
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    monkeypatch.setattr(web_app, "STATE_FILE", state_file)
    monkeypatch.setattr(web_app, "do_disable", lambda job: [("disable", 1, "failed")])

    response = web_app.app.test_client().post(
        "/toggle/job",
        data={"manifest_path": "sample/job.toml", "job_name": "sample-job"},
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302
    assert web_app.load_state()["jobs"]["sample/job.toml:sample-job"]["enabled"] is True


def test_toggle_repo_partial_failure_updates_only_successful_jobs(tmp_path, monkeypatch):
    examples_dir = tmp_path / "examples"
    manifest_path = examples_dir / "sample" / "jobs.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        """[[jobs]]
name = "first-job"
description = "First"
exec_start = "/bin/echo first"

[[jobs]]
name = "second-job"
description = "Second"
exec_start = "/bin/echo second"
""",
        encoding="utf-8",
    )
    state_file = tmp_path / "web-state.json"
    state_file.write_text(
        json.dumps(
            {
                "repos": {"sample": {"enabled": True}},
                "jobs": {
                    "sample/jobs.toml:first-job": {"enabled": True},
                    "sample/jobs.toml:second-job": {"enabled": True},
                },
                "global_target": "systemd",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "systemd")
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    monkeypatch.setattr(web_app, "STATE_FILE", state_file)
    monkeypatch.setattr(
        web_app,
        "do_disable",
        lambda job: [("disable", 0 if job["name"] == "first-job" else 1, "")],
    )

    response = web_app.app.test_client().post(
        "/toggle/repo/sample", headers={"Origin": "http://localhost"}
    )

    assert response.status_code == 302
    state = web_app.load_state()
    assert state["jobs"]["sample/jobs.toml:first-job"]["enabled"] is False
    assert state["jobs"]["sample/jobs.toml:second-job"]["enabled"] is True
    assert state["repos"]["sample"]["enabled"] is True


def test_toggle_all_uses_local_manifest_and_success_gates_repo_state(tmp_path, monkeypatch):
    examples_dir = tmp_path / "examples"
    repo_dir = examples_dir / "sample"
    repo_dir.mkdir(parents=True)
    canonical_path = repo_dir / "jobs.toml"
    local_path = repo_dir / "jobs.local.toml"
    manifest_text = """[[jobs]]
name = "first-job"
description = "First"
exec_start = "/bin/echo first"

[[jobs]]
name = "second-job"
description = "Second"
exec_start = "/bin/echo second"
"""
    canonical_path.write_text(manifest_text, encoding="utf-8")
    local_path.write_text(manifest_text, encoding="utf-8")
    state_file = tmp_path / "web-state.json"
    state_file.write_text(
        json.dumps(
            {
                "repos": {"sample": {"enabled": False}},
                "jobs": {
                    "sample/jobs.toml:first-job": {"enabled": False},
                    "sample/jobs.toml:second-job": {"enabled": False},
                },
                "global_target": "systemd",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_enable(path, job, target):
        calls.append((path, job["name"]))
        return [("enable", 0 if job["name"] == "first-job" else 1, "")]

    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "systemd")
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", examples_dir)
    monkeypatch.setattr(web_app, "STATE_FILE", state_file)
    monkeypatch.setattr(web_app, "_self_unit", lambda: None)
    monkeypatch.setattr(web_app, "do_enable", fake_enable)

    response = web_app.app.test_client().post("/toggle/all", headers={"Origin": "http://localhost"})

    assert response.status_code == 302
    assert calls == [(local_path, "first-job"), (local_path, "second-job")]
    state = web_app.load_state()
    assert state["jobs"]["sample/jobs.toml:first-job"]["enabled"] is True
    assert state["jobs"]["sample/jobs.toml:second-job"]["enabled"] is False
    assert state["repos"]["sample"]["enabled"] is False


def test_target_switch_disable_failure_preserves_old_target_and_enabled_state(
    tmp_path, monkeypatch
):
    _configure_target_switch_test(tmp_path, monkeypatch)
    monkeypatch.setattr(web_app, "do_disable", lambda job: [("disable", 1, "failed")])
    monkeypatch.setattr(
        web_app,
        "do_enable",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("enable executed")),
    )

    response = web_app.app.test_client().post(
        "/toggle/target/job",
        data={
            "manifest_path": "sample/dual.toml",
            "job_name": "dual-job",
            "_t": "cron",
        },
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302
    job_state = web_app.load_state()["jobs"]["sample/dual.toml:dual-job"]
    assert job_state == {"enabled": True, "target": "systemd"}


def test_target_switch_enable_failure_rolls_back_old_target(tmp_path, monkeypatch):
    _configure_target_switch_test(tmp_path, monkeypatch)
    targets = []

    def fake_enable(path, job, target):
        targets.append(target)
        return [("enable", 1 if target == "cron" else 0, "")]

    monkeypatch.setattr(web_app, "do_disable", lambda job: [("disable", 0, "")])
    monkeypatch.setattr(web_app, "do_enable", fake_enable)

    response = web_app.app.test_client().post(
        "/toggle/target/job",
        data={
            "manifest_path": "sample/dual.toml",
            "job_name": "dual-job",
            "_t": "cron",
        },
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302
    assert targets == ["cron", "systemd"]
    job_state = web_app.load_state()["jobs"]["sample/dual.toml:dual-job"]
    assert job_state == {"enabled": True, "target": "systemd"}


def test_target_switch_failed_rollback_marks_job_disabled_and_keeps_old_target(
    tmp_path, monkeypatch
):
    _configure_target_switch_test(tmp_path, monkeypatch)
    state = web_app.load_state()
    del state["jobs"]["sample/dual.toml:dual-job"]["target"]
    web_app.save_state(state)
    targets = []

    def fake_enable(path, job, target):
        targets.append(target)
        return [("enable", 1, f"{target} failed")]

    monkeypatch.setattr(web_app, "do_disable", lambda job: [("disable", 0, "")])
    monkeypatch.setattr(web_app, "do_enable", fake_enable)

    response = web_app.app.test_client().post(
        "/toggle/target/job",
        data={
            "manifest_path": "sample/dual.toml",
            "job_name": "dual-job",
            "_t": "cron",
        },
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302
    assert targets == ["cron", "systemd"]
    job_state = web_app.load_state()["jobs"]["sample/dual.toml:dual-job"]
    assert job_state == {"enabled": False, "target": "systemd"}


def test_launchd_job_details_uses_full_dotted_manifest_name_for_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_SCHEDULER_BACKEND", "launchd")
    monkeypatch.setenv("HOME", str(tmp_path))
    job_name = "agent.audit.v2"
    label = f"io.github.casonk.clockwork.{job_name}"
    log_dir = tmp_path / "Library" / "Logs" / "Clockwork"
    log_dir.mkdir(parents=True)
    (log_dir / f"{job_name}.stdout.log").write_text("dotted job output\n", encoding="utf-8")
    monkeypatch.setattr(
        web_app,
        "_launchctl",
        lambda *args, scope="user": (0, "state = waiting\nlast exit code = 0"),
    )

    response = web_app.app.test_client().get(
        f"/api/job-details?unit={label}&scope=user&job_name={job_name}"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["recent_lines"] == ["dotted job output"]
    assert f"job_name={job_name}" in payload["log_url"]


def test_job_details_treats_non_fatal_log_lines_as_warnings(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_systemctl",
        lambda *args, scope="user": (
            0,
            "\n".join(
                [
                    "Result=success",
                    "ActiveEnterTimestamp=Thu 2026-04-24 11:00:00 EDT",
                    "InactiveEnterTimestamp=",
                    "ExecMainStatus=0",
                    "NRestarts=0",
                ]
            ),
        ),
    )
    logs = "\n".join(
        [
            "2026-04-24T11:50:43-0400 desk bash[1]: password-retrieval notification failed (non-fatal): shock-relay email notification failed with exit code 2",
            "2026-04-24T11:50:46-0400 desk bash[1]: [email] done: 0 attachment(s), 0 inline, 0 error(s)",
        ]
    )
    monkeypatch.setattr(web_app, "_journalctl", lambda *args, scope="user": (0, logs))

    client = web_app.app.test_client()
    response = client.get("/api/job-details?unit=intake-daemon.service&scope=user")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["last_warning"] == "2026-04-24T11:50:43-0400"
    assert payload["last_failure"] is None
    assert payload["last_success"] == "2026-04-24T11:50:46-0400"
    assert payload["recent_entries"][0]["level"] == "warn"
    assert payload["recent_entries"][1]["level"] == "ok"


def test_job_details_keeps_real_error_lines_as_failures(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_systemctl",
        lambda *args, scope="user": (
            0,
            "\n".join(
                [
                    "Result=failed",
                    "ActiveEnterTimestamp=Thu 2026-04-24 11:00:00 EDT",
                    "InactiveEnterTimestamp=Thu 2026-04-24 11:05:12 EDT",
                    "ExecMainStatus=1",
                    "NRestarts=0",
                ]
            ),
        ),
    )
    logs = "\n".join(
        [
            "2026-04-24T11:05:12-0400 desk bash[1]: [email] ERROR: Email fetch failed: command: SELECT => System Error",
        ]
    )
    monkeypatch.setattr(web_app, "_journalctl", lambda *args, scope="user": (0, logs))

    client = web_app.app.test_client()
    response = client.get("/api/job-details?unit=intake-daemon.service&scope=user")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["last_warning"] is None
    assert payload["last_failure"] == "2026-04-24T11:05:12-0400"
    assert payload["recent_entries"][0]["level"] == "fail"


def test_job_details_uses_exec_start_and_inactive_exit_for_completed_oneshot(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_systemctl",
        lambda *args, scope="user": (
            0,
            "\n".join(
                [
                    "Result=success",
                    "ActiveEnterTimestamp=",
                    "InactiveEnterTimestamp=Thu 2026-04-24 11:05:12 EDT",
                    "InactiveExitTimestamp=Thu 2026-04-24 11:05:01 EDT",
                    "ExecMainStartTimestamp=Thu 2026-04-24 11:05:02 EDT",
                    "ExecMainExitTimestamp=Thu 2026-04-24 11:05:11 EDT",
                    "ExecMainStatus=0",
                    "NRestarts=0",
                ]
            ),
        ),
    )
    monkeypatch.setattr(web_app, "_journalctl", lambda *args, scope="user": (0, ""))

    client = web_app.app.test_client()
    response = client.get("/api/job-details?unit=archility-weekly.service&scope=user")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["last_active"] == "Thu 2026-04-24 11:05:02 EDT"
    assert payload["last_inactive"] == "Thu 2026-04-24 11:05:12 EDT"


def test_job_details_uses_timer_last_trigger_when_service_start_timestamps_are_blank(monkeypatch):
    def fake_systemctl(*args, scope="user"):
        unit = args[1]
        if unit == "intake-daemon.service":
            return (
                0,
                "\n".join(
                    [
                        "Result=success",
                        "ActiveEnterTimestamp=",
                        "InactiveEnterTimestamp=",
                        "InactiveExitTimestamp=",
                        "ExecMainStartTimestamp=",
                        "ExecMainExitTimestamp=",
                        "ExecMainStatus=0",
                        "NRestarts=0",
                    ]
                ),
            )
        if unit == "intake-daemon.timer":
            return (0, "LastTriggerUSec=Thu 2026-04-24 11:10:00 EDT")
        raise AssertionError(f"unexpected unit {unit}")

    monkeypatch.setattr(web_app, "_systemctl", fake_systemctl)
    monkeypatch.setattr(web_app, "_journalctl", lambda *args, scope="user": (0, ""))

    client = web_app.app.test_client()
    response = client.get("/api/job-details?unit=intake-daemon.timer&scope=user")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["last_active"] == "Thu 2026-04-24 11:10:00 EDT"


def test_cron_autogeneration_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("CLOCKWORK_WEB_AUTOGENERATE_CRON", raising=False)
    assert web_app._autogenerate_cron_enabled() is False

    monkeypatch.setenv("CLOCKWORK_WEB_AUTOGENERATE_CRON", "true")
    assert web_app._autogenerate_cron_enabled() is True
