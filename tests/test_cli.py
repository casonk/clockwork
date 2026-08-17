import os
import subprocess
import sys
from pathlib import Path

import pytest

from clockwork.cli import main


def test_install_writes_user_units(tmp_path):
    manifest_path = (
        Path(__file__).resolve().parent.parent / "examples" / "archility" / "archility-weekly.toml"
    )
    unit_dir = tmp_path / "systemd-user"

    exit_code = main(
        [
            "install",
            "--manifest",
            str(manifest_path),
            "--target",
            "systemd-user",
            "--unit-dir",
            str(unit_dir),
        ]
    )

    assert exit_code == 0
    assert (unit_dir / "archility-weekly.service").exists()
    assert (unit_dir / "archility-weekly.timer").exists()


def test_install_writes_cron_output(tmp_path):
    manifest_path = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "personal-finance"
        / "monthly-controller.toml"
    )
    output_path = tmp_path / "personal-finance.crontab"

    exit_code = main(
        [
            "install",
            "--manifest",
            str(manifest_path),
            "--target",
            "cron",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    assert "CRON_TZ=America/New_York" in output_path.read_text(encoding="utf-8")


def test_install_prints_timer_unit_name_with_suffix(tmp_path, capsys):
    manifest_path = (
        Path(__file__).resolve().parent.parent / "examples" / "archility" / "archility-weekly.toml"
    )
    unit_dir = tmp_path / "systemd-user"

    exit_code = main(
        [
            "install",
            "--manifest",
            str(manifest_path),
            "--target",
            "systemd-user",
            "--unit-dir",
            str(unit_dir),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "systemctl --user enable --now archility-weekly.timer" in captured.out


def test_python_module_entrypoint_invokes_main(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "examples" / "fedora-debugg" / "crash-snapshot.toml"
    unit_dir = tmp_path / "systemd-user"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "clockwork.cli",
            "install",
            "--manifest",
            str(manifest_path),
            "--target",
            "systemd-user",
            "--unit-dir",
            str(unit_dir),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert (unit_dir / "fedora-debugg-workflow.service").exists()
    assert (unit_dir / "fedora-debugg-workflow.timer").exists()


def test_install_job_filter_writes_only_the_exact_selected_job(tmp_path):
    manifest_path = tmp_path / "multi-job.toml"
    manifest_path.write_text(
        """[[jobs]]
name = "first-job"
description = "First test job"
exec_start = "/bin/echo first"

[[jobs]]
name = "second-job"
description = "Second test job"
exec_start = "/bin/echo second"
""",
        encoding="utf-8",
    )
    unit_dir = tmp_path / "systemd-user"

    exit_code = main(
        [
            "install",
            "--manifest",
            str(manifest_path),
            "--target",
            "systemd-user",
            "--unit-dir",
            str(unit_dir),
            "--job",
            "second-job",
        ]
    )

    assert exit_code == 0
    assert not (unit_dir / "first-job.service").exists()
    assert (unit_dir / "second-job.service").exists()


@pytest.mark.parametrize(
    ("jobs", "selected", "found"),
    [
        (("first-job",), "missing-job", 0),
        (("duplicate-job", "duplicate-job"), "duplicate-job", 2),
    ],
)
def test_install_job_filter_requires_exactly_one_manifest_match(tmp_path, jobs, selected, found):
    manifest_path = tmp_path / "invalid-selection.toml"
    job_blocks = []
    for name in jobs:
        job_blocks.append(
            "\n".join(
                (
                    "[[jobs]]",
                    f'name = "{name}"',
                    'description = "Selection test"',
                    'exec_start = "/bin/echo ready"',
                    "",
                )
            )
        )
    manifest_path.write_text(
        "\n".join(job_blocks),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"exactly one job named .*; found {found}"):
        main(
            [
                "install",
                "--manifest",
                str(manifest_path),
                "--target",
                "systemd-user",
                "--unit-dir",
                str(tmp_path / "units"),
                "--job",
                selected,
            ]
        )
