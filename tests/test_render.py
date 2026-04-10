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
    assert 'Environment="PORTFOLIO_ROOT=%h/git"' in rendered["archility-weekly.service"]
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


def test_render_fedora_debugg_interval_timer_and_cron_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "fedora-debugg"
        / "crash-snapshot.toml"
    )

    systemd_rendered = render_target(manifest, "systemd-user")
    cron_rendered = render_target(manifest, "cron")
    cron_output = next(iter(cron_rendered.values()))

    assert "fedora-debugg-workflow.service" in systemd_rendered
    assert "fedora-debugg-workflow.timer" in systemd_rendered
    assert (
        "ExecStart=%h/git/util-repos/fedora-debugg/scripts/run_workflow.sh"
        in systemd_rendered["fedora-debugg-workflow.service"]
    )
    assert "OnBootSec=20m" in systemd_rendered["fedora-debugg-workflow.timer"]
    assert "OnUnitActiveSec=6h" in systemd_rendered["fedora-debugg-workflow.timer"]
    assert "20 */6 * * *" in cron_output
    assert "artifacts/clockwork-cron.log" in cron_output


def test_render_crontab_for_personal_finance_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "personal-finance"
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
        / "personal-finance"
        / "intraday-snapshots.toml"
    )

    systemd_rendered = render_target(manifest, "systemd-user")
    cron_rendered = render_target(manifest, "cron")
    cron_output = next(iter(cron_rendered.values()))

    assert "pf-intraday-snapshot@market-open.service" in systemd_rendered
    assert "pf-intraday-snapshot-market-close.timer" in systemd_rendered
    assert (
        "ExecStart=/usr/bin/env bash %h/git/personal-finance/scripts/all/"
        "scheduled_intraday_snapshot.sh market-open"
    ) in systemd_rendered["pf-intraday-snapshot@market-open.service"]
    assert "10 16 * * *" in cron_output
    assert "scheduled_intraday_snapshot.sh market-close" in cron_output
