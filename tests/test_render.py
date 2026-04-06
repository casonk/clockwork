from pathlib import Path

from clockwork.manifest import load_manifest
from clockwork.render import render_target


def test_render_user_systemd_units_for_archility_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent / "examples" / "archility" / "archility-weekly.toml"
    )

    rendered = render_target(manifest, "systemd-user")

    assert "archility-weekly.service" in rendered
    assert "archility-weekly.timer" in rendered
    assert 'Environment="PORTFOLIO_ROOT=/path/to/portfolio"' in rendered["archility-weekly.service"]
    assert "RandomizedDelaySec=600" in rendered["archility-weekly.timer"]


def test_render_intake_daemon_preserves_start_limit_override():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent / "examples" / "intake" / "report-and-daemon.toml"
    )

    rendered = render_target(manifest, "systemd-user")

    assert "StartLimitIntervalSec=0" in rendered["intake-daemon.service"]


def test_render_system_scope_interval_timer_for_snowbridge_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "snowbridge"
        / "wireguard-endpoint-monitor.toml"
    )

    rendered = render_target(manifest, "systemd-system")

    assert "snowbridge-wireguard-endpoint-monitor.service" in rendered
    assert "User=service-user" in rendered["snowbridge-wireguard-endpoint-monitor.service"]
    assert "Group=service-group" in rendered["snowbridge-wireguard-endpoint-monitor.service"]
    assert "OnBootSec=5m" in rendered["snowbridge-wireguard-endpoint-monitor.timer"]
    assert "OnUnitActiveSec=15m" in rendered["snowbridge-wireguard-endpoint-monitor.timer"]


def test_render_crontab_for_personal_finance_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "example-scheduler"
        / "monthly-controller.toml"
    )

    rendered = render_target(manifest, "cron")
    output = next(iter(rendered.values()))

    assert "CRON_TZ=America/New_York" in output
    assert "0 8 2 * *" in output
    assert "scripts/all/monthly_controller.py" in output


def test_render_personal_finance_intraday_systemd_and_cron_examples():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "example-scheduler"
        / "intraday-snapshots.toml"
    )

    systemd_rendered = render_target(manifest, "systemd-user")
    cron_rendered = render_target(manifest, "cron")
    cron_output = next(iter(cron_rendered.values()))

    assert "pf-intraday-snapshot@market-open.service" in systemd_rendered
    assert "pf-intraday-snapshot-market-close.timer" in systemd_rendered
    assert (
        "ExecStart=/usr/bin/env bash /path/to/portfolio/example-scheduler/scripts/all/"
        "scheduled_intraday_snapshot.sh market-open"
    ) in systemd_rendered["pf-intraday-snapshot@market-open.service"]
    assert "10 16 * * *" in cron_output
    assert "scheduled_intraday_snapshot.sh market-close" in cron_output
