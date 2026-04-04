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
        / "example-scheduler"
        / "monthly-controller.toml"
    )

    assert len(manifest.jobs) == 1
    assert manifest.jobs[0].cron is not None
    assert manifest.jobs[0].cron.timezone == "America/New_York"


def test_load_personal_finance_intraday_manifest_parses_three_jobs():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "example-scheduler"
        / "intraday-snapshots.toml"
    )

    assert len(manifest.jobs) == 3
    assert manifest.jobs[0].service_unit_name() == "pf-intraday-snapshot@market-open.service"
    assert manifest.jobs[0].timer is not None
    assert manifest.jobs[0].timer.on_calendar == "*-*-* 09:35"
    assert manifest.jobs[0].cron is not None
    assert manifest.jobs[0].cron.expression == "35 9 * * *"
