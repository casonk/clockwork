from pathlib import Path

from clockwork.cli import main


def test_install_writes_user_units(tmp_path):
    manifest_path = (
        Path(__file__).resolve().parent.parent
        / "examples"
        / "archility"
        / "archility-weekly.toml"
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
        / "example-scheduler"
        / "monthly-controller.toml"
    )
    output_path = tmp_path / "example-scheduler.crontab"

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
        Path(__file__).resolve().parent.parent
        / "examples"
        / "archility"
        / "archility-weekly.toml"
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
