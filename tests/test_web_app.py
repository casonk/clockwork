import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import tomlkit

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_APP_PATH = _WEB_DIR / "app.py"
_SPEC = spec_from_file_location("clockwork_web_app", _APP_PATH)
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
_SPEC.loader.exec_module(web_app)


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

    assert cron_calls == ["cron-only", "dual-cron"]
    assert unit_calls == ["dual-systemd.timer"]
    assert statuses["archility/archility-daily.toml:cron-only"]["active_state"] == "cron"
    assert statuses["archility/archility-daily.toml:dual-cron"]["next_run_text"] == "next dual-cron"


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
