from pathlib import Path

from clockwork.manifest import load_manifest


def test_load_intake_manifest_parses_daemon_and_timer_examples():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent / "examples" / "intake" / "report-and-daemon.toml"
    )

    assert len(manifest.jobs) == 2
    assert manifest.jobs[0].name == "intake-daemon"
    assert manifest.jobs[0].start_limit_interval_sec == "0"
    assert manifest.jobs[0].service_install_wanted_by == ("default.target",)
    assert manifest.jobs[1].timer is not None
    assert manifest.jobs[1].timer.on_calendar == "*-*-* 12:00:00"


def test_load_personal_finance_manifest_parses_cron_example():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "personal-finance"
        / "monthly-controller.toml"
    )

    assert len(manifest.jobs) == 1
    assert manifest.jobs[0].cron is not None
    assert manifest.jobs[0].cron.timezone == "America/New_York"


def test_load_doseido_manifest_parses_poll_interval():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent / "examples" / "doseido" / "orchestration.toml"
    )

    assert len(manifest.jobs) == 1
    assert manifest.jobs[0].poll_interval == "5m"


def test_load_fedora_debugg_manifest_parses_interval_timer():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "fedora-debugg"
        / "crash-snapshot.toml"
    )

    assert len(manifest.jobs) == 1
    assert manifest.jobs[0].name == "fedora-debugg-workflow"
    assert manifest.jobs[0].timer is not None
    assert manifest.jobs[0].timer.kind == "interval"
    assert manifest.jobs[0].timer.on_boot_sec == "20m"
    assert manifest.jobs[0].timer.on_unit_active_sec == "6h"


def test_load_personal_finance_intraday_manifest_parses_three_jobs():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "personal-finance"
        / "intraday-snapshots.toml"
    )

    assert len(manifest.jobs) == 3
    assert manifest.jobs[0].service_unit_name() == "pf-intraday-snapshot@market-open.service"
    assert manifest.jobs[0].timer is not None
    assert manifest.jobs[0].timer.on_calendar == "*-*-* 09:35"
    assert manifest.jobs[0].cron is not None
    assert manifest.jobs[0].cron.expression == "35 9 * * *"
