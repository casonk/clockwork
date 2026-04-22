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


def test_load_example-orchestrator_manifest_parses_poll_interval():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent / "examples" / "example-orchestrator" / "orchestration.toml"
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
        / "example-scheduler"
        / "intraday-snapshots.toml"
    )

    assert len(manifest.jobs) == 3
    assert manifest.jobs[0].service_unit_name() == "pf-intraday-snapshot@market-open.service"
    assert manifest.jobs[0].timer is not None
    assert manifest.jobs[0].timer.on_calendar == "*-*-* 09:35"
    assert manifest.jobs[0].cron is not None
    assert manifest.jobs[0].cron.expression == "35 9 * * *"


def test_load_traction_control_template_consolidation_manifest_parses_interval_timer():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "traction-control"
        / "template-consolidation-agentic.toml"
    )

    assert len(manifest.jobs) == 1
    assert manifest.jobs[0].name == "template-consolidation-agentic"
    assert manifest.jobs[0].timer is not None
    assert manifest.jobs[0].timer.kind == "interval"
    assert manifest.jobs[0].timer.on_boot_sec == "30m"
    assert manifest.jobs[0].timer.on_unit_active_sec == "2d"
    assert manifest.jobs[0].environment_files == (
        "-%h/.config/traction-control/template-consolidation-agentic.env",
    )
    assert manifest.jobs[0].environment["TEMPLATE_CONSOLIDATION_PROVIDER"] == "auto"
    assert manifest.jobs[0].environment["TEMPLATE_CONSOLIDATION_MODEL"] == ""


def test_load_traction_control_bug_sweep_manifest_parses_interval_timer():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "traction-control"
        / "bug-sweep-agentic.toml"
    )

    assert len(manifest.jobs) == 1
    assert manifest.jobs[0].name == "bug-sweep-agentic"
    assert manifest.jobs[0].timer is not None
    assert manifest.jobs[0].timer.kind == "interval"
    assert manifest.jobs[0].timer.on_boot_sec == "90m"
    assert manifest.jobs[0].timer.on_unit_active_sec == "1d"
    assert manifest.jobs[0].environment_files == (
        "-%h/.config/traction-control/bug-sweep-agentic.env",
    )
    assert manifest.jobs[0].environment["BUG_SWEEP_AGENTIC_PROVIDER"] == "auto"
    assert manifest.jobs[0].environment["BUG_SWEEP_AGENTIC_MODEL"] == ""


def test_load_traction_control_ci_repair_manifest_parses_interval_timer():
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent
        / "examples"
        / "traction-control"
        / "ci-repair-agentic.toml"
    )

    assert len(manifest.jobs) == 1
    assert manifest.jobs[0].name == "ci-repair-agentic"
    assert manifest.jobs[0].timer is not None
    assert manifest.jobs[0].timer.kind == "interval"
    assert manifest.jobs[0].timer.on_boot_sec == "1h"
    assert manifest.jobs[0].timer.on_unit_active_sec == "2d"
    assert manifest.jobs[0].environment_files == (
        "-%h/.config/traction-control/ci-repair-agentic.env",
    )
    assert manifest.jobs[0].environment["CI_REPAIR_AGENTIC_PROVIDER"] == "auto"
    assert manifest.jobs[0].environment["CI_REPAIR_AGENTIC_MODEL"] == ""
