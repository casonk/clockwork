from pathlib import Path

import pytest

from clockwork.manifest import load_manifest
from clockwork.render import render_target

GUARD = "/usr/bin/python3 /opt/guard.py check --unit %n"

MANIFEST = """
[[jobs]]
name = "guarded"
description = "Guarded job"
service_type = "{service_type}"
exec_start = "/usr/bin/true"
start_condition = {condition}
time_limit = {limit}
{extra}

[jobs.timer]
kind = "calendar"
on_calendar = "*-*-* 04:00:00"
"""


def _load(
    tmp_path: Path, *, service_type="oneshot", condition=f'"{GUARD}"', limit='"2h"', extra=""
):
    path = tmp_path / "jobs.toml"
    path.write_text(
        MANIFEST.format(service_type=service_type, condition=condition, limit=limit, extra=extra),
        encoding="utf-8",
    )
    return load_manifest(path)


def _service(manifest, target="systemd-user"):
    return render_target(manifest, target)["guarded.service"].splitlines()


def test_oneshot_unit_carries_the_condition_and_a_start_timeout(tmp_path):
    service = _service(_load(tmp_path))

    assert f"ExecCondition={GUARD}" in service
    assert "TimeoutStartSec=2h" in service and "RuntimeMaxSec=2h" not in service
    # The condition must be evaluated before the job itself starts.
    assert service.index(f"ExecCondition={GUARD}") < service.index("ExecStart=/usr/bin/true")


def test_other_service_types_use_runtime_max_and_a_list_gives_several_conditions(tmp_path):
    manifest = _load(tmp_path, service_type="simple", condition='["/bin/a", "/bin/b"]')
    service = _service(manifest)

    assert "RuntimeMaxSec=2h" in service and "TimeoutStartSec=2h" not in service
    assert service.count("ExecCondition=/bin/a") == service.count("ExecCondition=/bin/b") == 1


def test_a_job_without_the_fields_renders_exactly_as_before(tmp_path):
    path = tmp_path / "jobs.toml"
    path.write_text(
        '[[jobs]]\nname = "guarded"\ndescription = "d"\nexec_start = "/usr/bin/true"\n',
        encoding="utf-8",
    )

    service = render_target(load_manifest(path), "systemd-user")["guarded.service"]

    assert "ExecCondition" not in service
    assert "TimeoutStartSec" not in service and "RuntimeMaxSec" not in service


WINDOWS_EXEC = 'exec_start = "C:\\\\Windows\\\\System32\\\\cmd.exe /c x"'


@pytest.mark.parametrize(
    ("target", "table"), [("launchd-user", "launchd"), ("windows-user", "windows")]
)
def test_targets_that_cannot_express_a_guard_refuse_it_until_withdrawn(tmp_path, target, table):
    # Windows also needs its own executable path; that is unrelated to the guard.
    refused = _load(tmp_path, extra=f"[jobs.windows]\n{WINDOWS_EXEC}\n")
    with pytest.raises(ValueError, match="start_condition/time_limit"):
        render_target(refused, target)

    withdrawn = _load(
        tmp_path,
        extra=(
            f"[jobs.windows]\n{WINDOWS_EXEC}\n"
            f'[jobs.{table}]\nstart_condition = []\ntime_limit = ""\n'
            if table == "launchd"
            else f'[jobs.windows]\n{WINDOWS_EXEC}\nstart_condition = []\ntime_limit = ""\n'
        ),
    )
    render_target(withdrawn, target)  # no longer refused
    # Withdrawing for one target leaves the systemd rendering guarded.
    assert f"ExecCondition={GUARD}" in _service(withdrawn)


def test_empty_condition_or_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="empty start_condition"):
        _load(tmp_path, condition='[""]')
    with pytest.raises(ValueError, match="empty time_limit"):
        _load(tmp_path, limit='" "')
