"""System-unit Restart and per-repo Deploy on the Grandmaster Clock page."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import test_grandmaster
from web import app as web_app
from web import grandmaster, system_units

client = test_grandmaster.client  # the shared Grandmaster fixture

UNITS = [
    {"name": "gate", "unit": "gate.service", "repo": "wiring", "description": "", "critical": True},
    {"name": "plain", "unit": "plain.service", "repo": "", "description": "", "critical": False},
]


class FakeGit:
    """A scripted runner: answers by git subcommand, records every call."""

    def __init__(self, **answers):
        self.answers = {
            "rev-parse --abbrev-ref HEAD": (0, "main"),
            "status --porcelain": (0, ""),
            "fetch --quiet origin main": (0, ""),
            "merge --ff-only origin/main": (0, ""),
        }
        self.answers.update(answers)
        self.heads = ["aaa1111", "bbb2222"]
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd, timeout):
        self.calls.append(args)
        key = " ".join(args[1:])
        if key == "rev-parse --short HEAD":
            return 0, self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
        return self.answers[key]


def test_load_units_skips_malformed_entries(tmp_path):
    path = tmp_path / "u.toml"
    path.write_text(
        '[[units]]\nname = "ok"\nunit = "ok.service"\n[[units]]\nname = "bad"\nunit = "rm -rf /"\n'
        '[[units]]\nunit = "noname.service"\n'
    )
    assert [u["name"] for u in system_units.load_units(path)] == ["ok"]
    assert system_units.load_units(path)[0]["critical"] is True  # critical unless said otherwise


def test_the_shipped_units_match_the_sudoers_rules():
    sudoers = (
        Path(__file__).resolve().parents[1] / "config" / "sudoers" / "clockwork-web"
    ).read_text()
    for entry in system_units.load_units():
        assert f"/usr/bin/systemctl restart {entry['unit']}\n" in sudoers


def test_restart_command_is_exactly_the_sudoers_command():
    assert system_units.restart_command("gate.service") == [
        "sudo",
        "-n",
        "/usr/bin/systemctl",
        "restart",
        "gate.service",
    ]


def test_find_checkout_never_treats_the_name_as_a_path(tmp_path):
    (tmp_path / "util-repos" / "wiring" / ".git").mkdir(parents=True)
    assert system_units.find_checkout("wiring", tmp_path) == tmp_path / "util-repos" / "wiring"
    assert system_units.find_checkout("../etc", tmp_path) is None
    assert system_units.find_checkout("nope", tmp_path) is None


def test_deploy_fast_forwards_a_clean_checkout_on_main(tmp_path):
    git = FakeGit()
    result = system_units.deploy(tmp_path, git)
    assert result == {
        "ok": True,
        "changed": True,
        "before": "aaa1111",
        "after": "bbb2222",
        "detail": "",
    }
    assert ["git", "merge", "--ff-only", "origin/main"] in git.calls
    assert not any("reset" in c or "checkout" in c or "--hard" in " ".join(c) for c in git.calls)


def test_deploy_reports_no_change_when_already_current(tmp_path):
    git = FakeGit()
    git.heads = ["aaa1111"]
    assert system_units.deploy(tmp_path, git)["changed"] is False


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        ({"rev-parse --abbrev-ref HEAD": (0, "feat/x")}, "not 'main'"),
        ({"status --porcelain": (0, " M file.py")}, "uncommitted changes"),
        ({"fetch --quiet origin main": (1, "Permission denied")}, "git fetch failed"),
        (
            {"merge --ff-only origin/main": (128, "Not possible to fast-forward")},
            "cannot fast-forward",
        ),
    ],
)
def test_deploy_refuses_anything_that_could_lose_work(tmp_path, answers, reason):
    git = FakeGit(**answers)
    result = system_units.deploy(tmp_path, git)
    assert result["ok"] is False and reason in result["detail"]
    if "merge" not in " ".join(answers):
        assert ["git", "merge", "--ff-only", "origin/main"] not in git.calls


@pytest.fixture()
def units(monkeypatch):
    monkeypatch.setattr(system_units, "load_units", lambda path=system_units.CONFIG_PATH: UNITS)


def test_system_restart_needs_an_allowlisted_name_and_the_typed_confirmation(
    client, units, monkeypatch
):
    ran = []
    monkeypatch.setattr(web_app, "_run", lambda *args, **kw: ran.append(args) or (0, ""))
    assert client.post("/api/grandmaster/system/restart", json={"name": "ssh"}).status_code == 404
    assert client.post("/api/grandmaster/system/restart", json={"name": "gate"}).status_code == 409
    assert ran == []
    ok = client.post("/api/grandmaster/system/restart", json={"name": "gate", "confirm": "gate"})
    assert ok.status_code == 200 and ok.get_json()["ok"]
    assert ran == [("sudo", "-n", "/usr/bin/systemctl", "restart", "gate.service")]
    assert client.post("/api/grandmaster/system/restart", json={"name": "plain"}).status_code == 200


def test_system_restart_failure_is_a_502_and_audited(client, units, monkeypatch):
    monkeypatch.setattr(web_app, "_run", lambda *args, **kw: (1, "sudo: a password is required"))
    response = client.post("/api/grandmaster/system/restart", json={"name": "plain"})
    assert response.status_code == 502
    entry = grandmaster.audit_tail(1)[-1]
    assert entry["action"] == "system-restart" and entry["ok"] is False


def test_deploy_endpoint_restarts_user_apps_but_not_the_ui_or_system_units(
    client, units, monkeypatch
):
    monkeypatch.setattr(
        system_units, "find_checkout", lambda repo, root=None: Path("/x") if repo == "r" else None
    )
    monkeypatch.setattr(
        system_units,
        "deploy",
        lambda checkout: {"ok": True, "changed": True, "before": "a", "after": "b", "detail": ""},
    )
    result = client.post("/api/grandmaster/deploy", json={"repo": "r"}).get_json()
    assert result["restarted"] == ["other"]  # "me" is the running UI
    assert "me" in result["restart_needed"]
    assert client.calls == [("restart", "other")]
    assert client.post("/api/grandmaster/deploy", json={"repo": "../x"}).status_code == 404


def test_deploy_endpoint_surfaces_a_refusal_and_restarts_nothing(client, units, monkeypatch):
    monkeypatch.setattr(system_units, "find_checkout", lambda repo, root=None: Path("/x"))
    monkeypatch.setattr(
        system_units,
        "deploy",
        lambda checkout: {
            "ok": False,
            "changed": False,
            "before": "a",
            "after": "a",
            "detail": "on branch 'feat/x', not 'main'",
        },
    )
    response = client.post("/api/grandmaster/deploy", json={"repo": "r"})
    assert response.status_code == 409 and "feat/x" in response.get_json()["detail"]
    assert client.calls == []


def test_page_lists_system_units_with_buttons(client, units, monkeypatch):
    monkeypatch.setattr(system_units, "find_checkout", lambda repo, root=None: Path("/x"))
    body = client.get("/grandmaster").get_data(as_text=True)
    assert "System units" in body and "data-sys-restart" in body and "data-deploy" in body
    rows = client.get("/api/grandmaster").get_json()["system_units"]
    assert {r["name"] for r in rows} == {"gate", "plain"}
