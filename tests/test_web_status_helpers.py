from datetime import timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from zoneinfo import ZoneInfo

_HELPERS_PATH = Path(__file__).resolve().parent.parent / "web" / "status_helpers.py"
_SPEC = spec_from_file_location("clockwork_web_status_helpers", _HELPERS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
status_helpers = module_from_spec(_SPEC)
_SPEC.loader.exec_module(status_helpers)


def test_build_unit_status_includes_next_run_iso(monkeypatch):
    monkeypatch.setattr(status_helpers, "_local_tzinfo", lambda: ZoneInfo("America/New_York"))

    status = status_helpers.build_unit_status(
        "\n".join(
            [
                "ActiveState=active",
                "UnitFileState=enabled",
                "NextElapseUSecRealtime=Sun 2026-04-12 02:07:29 EDT",
            ]
        )
    )

    assert status["active"] is True
    assert status["enabled"] is True
    assert status["next_run_text"] == "Sun 2026-04-12 02:07:29 EDT"
    assert status["next_run_iso"] == "2026-04-12T02:07:29-04:00"


def test_parse_systemd_timestamp_handles_utc():
    parsed = status_helpers.parse_systemd_timestamp("Thu 2026-04-09 12:00:00 UTC")

    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-04-09T12:00:00+00:00"


def test_build_unit_status_ignores_unscheduled_timers():
    status = status_helpers.build_unit_status(
        "\n".join(
            [
                "ActiveState=inactive",
                "UnitFileState=disabled",
                "NextElapseUSecRealtime=n/a",
            ]
        )
    )

    assert status["active"] is False
    assert status["enabled"] is False
    assert status["next_run_text"] == ""
    assert status["next_run_iso"] == ""


def test_select_next_run_picks_earliest_job():
    next_run = status_helpers.select_next_run(
        [
            {
                "job_name": "later-job",
                "next_run_iso": "2026-04-12T02:07:29-04:00",
                "next_run_text": "Sun 2026-04-12 02:07:29 EDT",
                "cadence_text": "",
            },
            {
                "job_name": "earlier-job",
                "next_run_iso": "2026-04-09T09:35:00-04:00",
                "next_run_text": "Thu 2026-04-09 09:35:00 EDT",
                "cadence_text": "every 5m",
            },
            {
                "job_name": "unscheduled-job",
                "next_run_iso": "",
                "next_run_text": "",
                "cadence_text": "",
            },
        ]
    )

    assert next_run == {
        "job_name": "earlier-job",
        "next_run_iso": "2026-04-09T09:35:00-04:00",
        "next_run_text": "Thu 2026-04-09 09:35:00 EDT",
        "cadence_text": "every 5m",
    }


def test_build_repo_next_run_candidate_falls_back_to_boot_login_for_user_service_job():
    candidate = status_helpers.build_repo_next_run_candidate(
        {
            "name": "clockwork-web",
            "enabled": True,
            "scope": "user",
            "poll_interval": "30s",
            "timer": None,
            "cron": None,
        },
        {},
    )

    assert candidate == {
        "job_name": "clockwork-web",
        "next_run_iso": "",
        "next_run_text": "boot/login",
        "cadence_text": "every 30s",
    }


def test_build_repo_next_run_candidate_uses_reboot_delay_for_system_interval_timer():
    candidate = status_helpers.build_repo_next_run_candidate(
        {
            "name": "snowbridge-wireguard-endpoint-monitor",
            "enabled": True,
            "scope": "system",
            "cron": {"expression": "*/15 * * * *"},
            "timer": {"kind": "interval", "on_boot_sec": "5m", "on_unit_active_sec": "15m"},
        },
        {},
    )

    assert candidate == {
        "job_name": "snowbridge-wireguard-endpoint-monitor",
        "next_run_iso": "",
        "next_run_text": "reboot (+5m)",
        "cadence_text": "every 15m",
    }


def test_build_repo_next_run_candidate_uses_boot_login_delay_for_user_interval_timer():
    candidate = status_helpers.build_repo_next_run_candidate(
        {
            "name": "archility-daily",
            "enabled": True,
            "scope": "user",
            "poll_interval": "30s",
            "cron": {"expression": "0 2 * * *"},
            "timer": {"kind": "interval", "on_boot_sec": "1m", "on_unit_active_sec": "1d"},
        },
        {},
    )

    assert candidate == {
        "job_name": "archility-daily",
        "next_run_iso": "",
        "next_run_text": "boot/login (+1m)",
        "cadence_text": "every 30s",
    }


def test_job_cadence_text_prefers_explicit_poll_interval():
    cadence = status_helpers.job_cadence_text(
        {"poll_interval": "30s", "timer": {"kind": "interval", "on_unit_active_sec": "15m"}}
    )

    assert cadence == "every 30s"


def test_select_next_run_uses_fallback_when_no_concrete_schedule_exists():
    next_run = status_helpers.select_next_run(
        [
            {
                "job_name": "clockwork-web",
                "next_run_iso": "",
                "next_run_text": "boot/login",
                "cadence_text": "every 30s",
            },
            {
                "job_name": "snowbridge-wireguard-endpoint-monitor",
                "next_run_iso": "",
                "next_run_text": "reboot (+5m)",
                "cadence_text": "every 15m",
            },
        ]
    )

    assert next_run == {
        "job_name": "clockwork-web",
        "next_run_iso": "",
        "next_run_text": "boot/login",
        "cadence_text": "every 30s",
    }
