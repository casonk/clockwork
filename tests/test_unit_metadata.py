"""exec_start_then, documentation and syslog_identifier: what renders and who refuses it."""

from pathlib import Path

import pytest

from clockwork.manifest import load_manifest
from clockwork.render import render_target

MANIFEST = """
[[jobs]]
name = "multi"
description = "Two commands"
service_type = "{service_type}"
exec_start = "/usr/bin/first --a"
{extra}

[jobs.timer]
kind = "calendar"
on_calendar = "*-*-* 04:00:00"
"""


def _load(tmp_path: Path, *, service_type="oneshot", extra=""):
    path = tmp_path / "jobs.toml"
    path.write_text(MANIFEST.format(service_type=service_type, extra=extra), encoding="utf-8")
    return load_manifest(path)


def _lines(manifest, unit="multi.service", target="systemd-user"):
    return render_target(manifest, target)[unit].splitlines()


def test_further_commands_render_as_ordered_execstart_lines(tmp_path):
    manifest = _load(tmp_path, extra='exec_start_then = ["/usr/bin/second", "/usr/bin/third"]')
    execs = [line for line in _lines(manifest) if line.startswith("ExecStart=")]
    assert execs == [
        "ExecStart=/usr/bin/first --a",
        "ExecStart=/usr/bin/second",
        "ExecStart=/usr/bin/third",
    ]


def test_one_command_may_be_a_plain_string(tmp_path):
    manifest = _load(tmp_path, extra='exec_start_then = "/usr/bin/second"')
    assert "ExecStart=/usr/bin/second" in _lines(manifest)


def test_without_the_new_fields_nothing_changes(tmp_path):
    lines = _lines(_load(tmp_path))
    assert [line for line in lines if line.startswith("ExecStart=")] == [
        "ExecStart=/usr/bin/first --a"
    ]
    assert not any(line.startswith(("Documentation=", "SyslogIdentifier=")) for line in lines)
    assert not any(
        line.startswith("Documentation=") for line in _lines(_load(tmp_path), "multi.timer")
    )


def test_documentation_goes_on_both_units_and_the_identifier_on_the_service(tmp_path):
    manifest = _load(
        tmp_path,
        extra='documentation = ["https://example.org/a", "https://example.org/b"]\nsyslog_identifier = "multi-tag"',
    )
    for unit in ("multi.service", "multi.timer"):
        lines = _lines(manifest, unit)
        assert lines.count("Documentation=https://example.org/a") == 1
        assert lines.count("Documentation=https://example.org/b") == 1
        assert lines.index("Documentation=https://example.org/a") < lines.index(
            "Documentation=https://example.org/b"
        )
    assert "SyslogIdentifier=multi-tag" in _lines(manifest)
    assert not any(line.startswith("SyslogIdentifier=") for line in _lines(manifest, "multi.timer"))


def test_only_a_oneshot_service_may_run_several_commands(tmp_path):
    with pytest.raises(ValueError, match="oneshot"):
        _load(tmp_path, service_type="simple", extra='exec_start_then = ["/usr/bin/second"]')


def test_empty_values_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="empty exec_start_then"):
        _load(tmp_path, extra='exec_start_then = [""]')
    with pytest.raises(ValueError, match="empty documentation"):
        _load(tmp_path, extra='documentation = [" "]')
    with pytest.raises(ValueError, match="empty syslog_identifier"):
        _load(tmp_path, extra='syslog_identifier = ""')


WINDOWS_EXEC = 'exec_start = "C:\\\\Windows\\\\System32\\\\cmd.exe /c x"'
THEN = 'exec_start_then = ["/usr/bin/second"]\n'


def test_launchd_refuses_a_sequence_until_one_command_replaces_it(tmp_path):
    with pytest.raises(ValueError, match="exec_start_then"):
        render_target(_load(tmp_path, extra=THEN), "launchd-user")

    replaced = _load(tmp_path, extra=THEN + '[jobs.launchd]\nexec_start = "/usr/bin/only"\n')
    render_target(replaced, "launchd-user")  # no longer refused
    # The systemd rendering still runs both commands.
    execs = [line for line in _lines(replaced) if line.startswith("ExecStart=")]
    assert execs == ["ExecStart=/usr/bin/first --a", "ExecStart=/usr/bin/second"]


def test_windows_override_replaces_the_whole_command(tmp_path):
    # Windows always needs its own executable, which replaces the sequence.
    manifest = _load(tmp_path, extra=THEN + f"[jobs.windows]\n{WINDOWS_EXEC}\n")
    render_target(manifest, "windows-user")
    assert manifest.jobs[0].for_windows().exec_start_then == ()
    assert manifest.jobs[0].exec_start_then == ("/usr/bin/second",)


def test_metadata_is_ignored_by_targets_that_have_no_such_concept(tmp_path):
    manifest = _load(
        tmp_path, extra='documentation = ["https://example.org"]\nsyslog_identifier = "t"'
    )
    rendered = render_target(manifest, "launchd-user")
    assert "https://example.org" not in "".join(rendered.values())


def test_generated_cron_runs_the_whole_sequence_and_logs_it_together(tmp_path):
    from web import app as web_app

    (tmp_path / "x").mkdir()
    manifest = tmp_path / "x" / "jobs.toml"
    manifest.write_text(
        MANIFEST.format(service_type="oneshot", extra='exec_start_then = ["/usr/bin/second"]')
    )
    assert web_app.ensure_cron_sections(tmp_path) == ["x/jobs.toml"]
    command = load_manifest(manifest).jobs[0].cron.command
    assert "( /usr/bin/first --a && /usr/bin/second ) >> " in command
