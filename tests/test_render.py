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


def test_render_shock_relay_gmail_digest_interval_timer_and_cron_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent / "examples" / "shock-relay" / "gmail-digest.toml"
    )

    systemd_rendered = render_target(manifest, "systemd-user")
    cron_rendered = render_target(manifest, "cron")
    cron_output = next(iter(cron_rendered.values()))

    assert "shock-relay-gmail-digest.service" in systemd_rendered
    assert "shock-relay-gmail-digest.timer" in systemd_rendered
    assert (
        "ExecStart=/usr/bin/env python3 %h/git/util-repos/shock-relay/services/gmail-imap/send_digest.py"
        in systemd_rendered["shock-relay-gmail-digest.service"]
    )
    assert "OnBootSec=5m" in systemd_rendered["shock-relay-gmail-digest.timer"]
    assert "OnUnitActiveSec=1h" in systemd_rendered["shock-relay-gmail-digest.timer"]
    assert "RandomizedDelaySec=60" in systemd_rendered["shock-relay-gmail-digest.timer"]
    assert "0 * * * *" in cron_output
    assert "services/gmail-imap/send_digest.py" in cron_output


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
        "ExecStart=/usr/bin/env bash %h/git/example-scheduler/scripts/all/"
        "scheduled_intraday_snapshot.sh market-open"
    ) in systemd_rendered["pf-intraday-snapshot@market-open.service"]
    assert "10 16 * * *" in cron_output
    assert "scheduled_intraday_snapshot.sh market-close" in cron_output


def test_render_traction_control_template_consolidation_interval_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "traction-control"
        / "template-consolidation-agentic.toml"
    )

    systemd_rendered = render_target(manifest, "systemd-user")

    assert "template-consolidation-agentic.service" in systemd_rendered
    assert "template-consolidation-agentic.timer" in systemd_rendered
    assert (
        "ExecStart=%h/git/util-repos/traction-control/scripts/template_consolidation_agentic.sh"
        in systemd_rendered["template-consolidation-agentic.service"]
    )
    assert (
        "EnvironmentFile=-%h/.config/traction-control/template-consolidation-agentic.env"
        in systemd_rendered["template-consolidation-agentic.service"]
    )
    assert (
        'Environment="TEMPLATE_CONSOLIDATION_PROVIDER=auto"'
        in systemd_rendered["template-consolidation-agentic.service"]
    )
    assert (
        'Environment="TEMPLATE_CONSOLIDATION_MODEL="'
        in systemd_rendered["template-consolidation-agentic.service"]
    )
    assert "OnBootSec=30m" in systemd_rendered["template-consolidation-agentic.timer"]
    assert "OnUnitActiveSec=2d" in systemd_rendered["template-consolidation-agentic.timer"]


def test_render_traction_control_bug_sweep_interval_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "traction-control"
        / "bug-sweep-agentic.toml"
    )

    systemd_rendered = render_target(manifest, "systemd-user")

    assert "bug-sweep-agentic.service" in systemd_rendered
    assert "bug-sweep-agentic.timer" in systemd_rendered
    assert (
        "ExecStart=%h/git/util-repos/traction-control/scripts/bug_sweep_agentic.sh"
        in systemd_rendered["bug-sweep-agentic.service"]
    )
    assert (
        "EnvironmentFile=-%h/.config/traction-control/bug-sweep-agentic.env"
        in systemd_rendered["bug-sweep-agentic.service"]
    )
    assert (
        'Environment="BUG_SWEEP_AGENTIC_PROVIDER=auto"'
        in systemd_rendered["bug-sweep-agentic.service"]
    )
    assert 'Environment="BUG_SWEEP_AGENTIC_MODEL="' in systemd_rendered["bug-sweep-agentic.service"]
    assert "OnBootSec=90m" in systemd_rendered["bug-sweep-agentic.timer"]
    assert "OnUnitActiveSec=1d" in systemd_rendered["bug-sweep-agentic.timer"]


def test_render_traction_control_ci_repair_interval_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "traction-control"
        / "ci-repair-agentic.toml"
    )

    systemd_rendered = render_target(manifest, "systemd-user")

    assert "ci-repair-agentic.service" in systemd_rendered
    assert "ci-repair-agentic.timer" in systemd_rendered
    assert (
        "ExecStart=%h/git/util-repos/traction-control/scripts/ci_repair_agentic.sh"
        in systemd_rendered["ci-repair-agentic.service"]
    )
    assert (
        "EnvironmentFile=-%h/.config/traction-control/ci-repair-agentic.env"
        in systemd_rendered["ci-repair-agentic.service"]
    )
    assert (
        'Environment="CI_REPAIR_AGENTIC_PROVIDER=auto"'
        in systemd_rendered["ci-repair-agentic.service"]
    )
    assert 'Environment="CI_REPAIR_AGENTIC_MODEL="' in systemd_rendered["ci-repair-agentic.service"]
    assert "OnBootSec=1h" in systemd_rendered["ci-repair-agentic.timer"]
    assert "OnUnitActiveSec=2d" in systemd_rendered["ci-repair-agentic.timer"]
