"""The /grandmaster page enables, disables and restarts long-running apps only."""

from __future__ import annotations

import pytest

from web import app as web_app
from web import grandmaster

SERVICE = {"name": "svc", "service_type": "simple", "timer": None, "cron": None}


def test_only_long_running_services_are_apps():
    assert grandmaster.is_app(SERVICE)
    assert not grandmaster.is_app({**SERVICE, "service_type": "oneshot"})
    assert not grandmaster.is_app({**SERVICE, "timer": {"on_calendar": "daily"}})
    assert not grandmaster.is_app({**SERVICE, "cron": {"schedule": "* * * * *"}})


@pytest.mark.parametrize(
    ("status", "enabled", "label"),
    [
        ({"active": True, "active_state": "active"}, True, "running"),
        ({"active": False, "active_state": "failed"}, True, "failed"),
        ({"active": False, "active_state": "activating"}, True, "restarting"),
        ({"active": False, "active_state": "inactive"}, True, "stopped"),
        ({"active": False, "active_state": "inactive"}, False, "disabled"),
        ({"active": None, "active_state": "unsupported-backend"}, True, "unknown"),
    ],
)
def test_state_label(status, enabled, label):
    assert grandmaster.state_label(status, enabled) == label


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(grandmaster, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(web_app, "_GRANDMASTER_GATE", grandmaster.ActionGate(cooldown=0))
    monkeypatch.setattr(web_app, "_self_unit", lambda: "me.service")
    monkeypatch.setattr(
        web_app,
        "scan_repos",
        lambda: {
            "r": {
                "name": "r",
                "manifests": [
                    {
                        "path": "r/m.toml",
                        "name": "m",
                        "jobs": [
                            {**SERVICE, "name": "me", "service_name": "me.service"},
                            {**SERVICE, "name": "other", "service_name": "other.service"},
                            {**SERVICE, "name": "tick", "timer": {"x": "y"}},
                        ],
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(web_app, "_scheduler_backend", lambda: "systemd")
    monkeypatch.setattr(
        web_app,
        "unit_status",
        lambda unit, scope="user": {"active": True, "active_state": "active"},
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        web_app, "do_restart", lambda job: calls.append(("restart", job["name"])) or [("r", 0, "")]
    )
    monkeypatch.setattr(
        web_app, "do_disable", lambda job: calls.append(("disable", job["name"])) or [("d", 0, "")]
    )
    monkeypatch.setattr(
        web_app,
        "do_enable",
        lambda path, job, target: calls.append(("enable", job["name"])) or [("e", 0, "")],
    )
    web_app.app.config["TESTING"] = True
    test_client = web_app.app.test_client()
    test_client.environ_base["HTTP_SEC_FETCH_SITE"] = "same-origin"
    test_client.calls = calls
    return test_client


def test_page_and_api_list_apps_and_protect_the_running_ui(client):
    apps = {a["name"]: a for a in client.get("/api/grandmaster").get_json()["apps"]}
    assert set(apps) == {"me", "other"}
    assert apps["me"]["protected"] and not apps["other"]["protected"]
    assert apps["other"]["state"] == "running"
    body = client.get("/grandmaster").get_data(as_text=True)
    assert "Grandmaster Clock" in body and "this web UI" in body


def test_disable_then_enable_records_state(client):
    payload = {"manifest_path": "r/m.toml", "name": "other"}
    assert client.post("/api/grandmaster/disable", json=payload).get_json()["ok"]
    rows = {a["name"]: a for a in client.get("/api/grandmaster").get_json()["apps"]}
    assert rows["other"]["enabled"] is False
    assert client.post("/api/grandmaster/enable", json=payload).get_json()["ok"]
    assert client.calls == [("disable", "other"), ("enable", "other")]


def test_running_ui_and_non_apps_are_refused(client):
    for name in ("me", "tick"):
        response = client.post(
            "/api/grandmaster/restart", json={"manifest_path": "r/m.toml", "name": name}
        )
        assert response.status_code == 502 and not response.get_json()["ok"]
    assert client.calls == []


def test_bulk_skips_the_running_ui_and_unknown_actions_are_rejected(client):
    result = client.post("/api/grandmaster/restart", json={"all": True}).get_json()
    assert [r["name"] for r in result["results"]] == ["other"]
    assert client.calls == [("restart", "other")]
    assert client.post("/api/grandmaster/explode", json={}).status_code == 400
    assert client.post("/api/grandmaster/restart", json={}).status_code == 400


# ---- Home page cards ------------------------------------------------------

from web import home_cards  # noqa: E402


@pytest.fixture()
def cards(client, monkeypatch, tmp_path):
    monkeypatch.setattr(home_cards, "FILE", tmp_path / "home-cards.json")
    monkeypatch.setattr(web_app.placeholders, "FILE", tmp_path / "placeholders.json")
    monkeypatch.setenv("CLOCKWORK_HOME_HIDE_SERVICES", "tradility-web")
    monkeypatch.setattr(
        web_app,
        "_load_portal_groups",
        lambda apply_labels=True: (
            [
                (
                    "Infrastructure",
                    [
                        {
                            "name": "magneto-web",
                            "display": "Magneto",
                            "url": "https://m.example",
                            "icon": "🧲",
                        },
                        {
                            "name": "tradility-web",
                            "display": "Tradility",
                            "url": "https://t.example",
                            "icon": "📊",
                        },
                    ],
                )
            ]
            + [
                (
                    link["group"],
                    [
                        {
                            "name": "link-" + link["id"],
                            "display": link["name"],
                            "url": link["url"],
                            "icon": link["icon"],
                            "link": True,
                        }
                    ],
                )
                for link in home_cards.load()["links"]
            ]
        ),
    )
    return client


def _by_name(client):
    return {c["name"]: c for c in client.get("/api/grandmaster/home").get_json()["cards"]}


def test_hidden_defaults_to_the_env_list_then_follows_what_was_saved(cards):
    assert _by_name(cards)["tradility-web"]["hidden"] is True
    assert _by_name(cards)["magneto-web"]["hidden"] is False
    assert (
        cards.post(
            "/api/grandmaster/home/hide", json={"name": "magneto-web", "hidden": True}
        ).status_code
        == 200
    )
    state = _by_name(cards)
    assert state["magneto-web"]["hidden"] and state["tradility-web"]["hidden"]
    cards.post("/api/grandmaster/home/hide", json={"name": "tradility-web", "hidden": False})
    state = _by_name(cards)
    assert not state["tradility-web"]["hidden"] and state["magneto-web"]["hidden"]
    home = cards.get("/home").get_data(as_text=True)
    assert 'class="card-name">Magneto' not in home and 'class="card-name">Tradility' in home
    assert "Magneto" in home  # still in the Services menu


def test_hide_rejects_bad_input(cards):
    assert cards.post("/api/grandmaster/home/hide", json={"name": "x"}).status_code == 400
    assert (
        cards.post("/api/grandmaster/home/hide", json={"name": "a b<", "hidden": True}).status_code
        == 400
    )


def test_link_cards_are_added_shown_on_home_and_removed(cards):
    response = cards.post(
        "/api/grandmaster/home/links",
        json={
            "name": "Router",
            "url": "https://192.168.0.1/",
            "icon": "📡",
            "group": "Infrastructure",
        },
    )
    assert response.status_code == 200 and response.get_json()["card"]["id"] == "router"
    assert "link-router" in _by_name(cards) and _by_name(cards)["link-router"]["removable"]
    assert 'class="card-name">Router' in cards.get("/home").get_data(as_text=True)
    removed = cards.post("/api/grandmaster/home/remove", json={"name": "link-router"})
    assert removed.status_code == 200 and "link-router" not in _by_name(cards)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "//evil.example",
        "ftp://x.example",
        "https://u:p@x.example",
        "/a b",
        "",
        "data:text/html,x",
    ],
)
def test_link_cards_refuse_unsafe_urls(cards, url):
    response = cards.post("/api/grandmaster/home/links", json={"name": "Bad", "url": url})
    assert response.status_code == 400
    assert home_cards.load()["links"] == []


def test_real_services_cannot_be_removed_only_hidden(cards):
    response = cards.post("/api/grandmaster/home/remove", json={"name": "magneto-web"})
    assert response.status_code == 400 and "hide a service" in response.get_json()["error"]


def test_placeholder_cards_can_be_removed_here(cards):
    web_app.placeholders.add(
        {
            "id": "/soon",
            "name": "Soon",
            "icon": "🧩",
            "group": "Clockwork",
            "repo_group": "g",
            "repo": "r",
        }
    )
    assert web_app.placeholders.load()
    response = cards.post("/api/grandmaster/home/remove", json={"name": "placeholder-soon"})
    assert response.status_code == 200 and web_app.placeholders.load() == []


def test_the_grandmaster_card_can_never_be_hidden(cards, monkeypatch):
    response = cards.post(
        "/api/grandmaster/home/hide", json={"name": "clockwork-grandmaster", "hidden": True}
    )
    assert response.status_code == 400 and "always shown" in response.get_json()["error"]
    # even a hand-edited file or the env default cannot hide it
    home_cards.save({"hidden": ["clockwork-grandmaster", "magneto-web"], "links": []})
    assert home_cards.hidden_names(set()) == {"magneto-web"}
    monkeypatch.setenv("CLOCKWORK_HOME_HIDE_SERVICES", "clockwork-grandmaster")
    home_cards.FILE.unlink()
    assert web_app._home_hidden_services() == set()


def test_app_rows_carry_their_home_card_and_toggle_it(cards, monkeypatch):
    monkeypatch.setattr(
        web_app,
        "scan_repos",
        lambda: {
            "r": {
                "name": "r",
                "manifests": [
                    {
                        "path": "r/m.toml",
                        "name": "m",
                        "jobs": [
                            {
                                **SERVICE,
                                "name": "magneto-web",
                                "service_name": "magneto-web.service",
                            },
                            {**SERVICE, "name": "no-card", "service_name": "no-card.service"},
                        ],
                    }
                ],
            }
        },
    )
    apps = {a["name"]: a for a in cards.get("/api/grandmaster").get_json()["apps"]}
    assert (
        apps["magneto-web"]["home_card"] == "magneto-web" and not apps["magneto-web"]["home_hidden"]
    )
    assert apps["no-card"]["home_card"] is None
    cards.post("/api/grandmaster/home/hide", json={"name": "magneto-web", "hidden": True})
    apps = {a["name"]: a for a in cards.get("/api/grandmaster").get_json()["apps"]}
    assert apps["magneto-web"]["home_hidden"] is True
    body = cards.get("/grandmaster").get_data(as_text=True)
    assert "Show on Home" in body and body.count('<button type="button" data-home-toggle') == 1


def test_home_card_aliases():
    assert (
        grandmaster.home_card_name("pit-box-guacamole", {"pit-box-remote-desktop"})
        == "pit-box-remote-desktop"
    )
    assert grandmaster.home_card_name("x", {"y"}) is None


# ---- Guardrails -------------------------------------------------------------


def test_critical_apps_default_and_env_override(monkeypatch):
    monkeypatch.delenv("CLOCKWORK_GRANDMASTER_CRITICAL", raising=False)
    assert grandmaster.critical_apps() == grandmaster.CRITICAL_DEFAULT
    monkeypatch.setenv("CLOCKWORK_GRANDMASTER_CRITICAL", "a, b ,")
    assert grandmaster.critical_apps() == {"a", "b"}
    monkeypatch.setenv("CLOCKWORK_GRANDMASTER_CRITICAL", "")
    assert grandmaster.critical_apps() == frozenset()


def test_confirmation_is_needed_only_to_disable_or_restart_a_critical_app():
    critical = frozenset({"x"})
    assert grandmaster.confirmation_problem("disable", "x", "", critical)
    assert grandmaster.confirmation_problem("restart", "x", "nope", critical)
    assert not grandmaster.confirmation_problem("restart", "x", "x", critical)
    assert not grandmaster.confirmation_problem("enable", "x", "", critical)
    assert not grandmaster.confirmation_problem("disable", "y", "", critical)


def test_a_critical_app_is_not_changed_without_its_name_and_the_attempt_is_not_logged_as_a_change(
    client, monkeypatch
):
    monkeypatch.setenv("CLOCKWORK_GRANDMASTER_CRITICAL", "other")
    payload = {"manifest_path": "r/m.toml", "name": "other"}
    refused = client.post("/api/grandmaster/disable", json=payload)
    assert refused.status_code == 409 and refused.get_json()["needs_confirm"]
    assert (
        client.post("/api/grandmaster/restart", json={**payload, "confirm": "wrong"}).status_code
        == 409
    )
    assert client.calls == []
    assert client.post("/api/grandmaster/enable", json=payload).status_code == 200
    assert (
        client.post("/api/grandmaster/disable", json={**payload, "confirm": "other"}).status_code
        == 200
    )
    assert client.calls == [("enable", "other"), ("disable", "other")]
    rows = {a["name"]: a for a in client.get("/api/grandmaster").get_json()["apps"]}
    assert rows["other"]["critical"] and not rows["me"]["critical"]


def test_bulk_changes_skip_critical_apps_and_dry_run_changes_nothing(client, monkeypatch):
    monkeypatch.setenv("CLOCKWORK_GRANDMASTER_CRITICAL", "other")
    plan = client.post("/api/grandmaster/disable", json={"all": True, "dry_run": True}).get_json()
    assert plan["dry_run"] and plan["targets"] == []
    assert plan["skipped"] == [
        {"name": "other", "reason": "critical app: change it on its own row"}
    ]
    done = client.post("/api/grandmaster/disable", json={"all": True}).get_json()
    assert done["results"] == [] and client.calls == []
    # enabling is harmless, so a bulk enable includes it
    plan = client.post("/api/grandmaster/enable", json={"all": True, "dry_run": True}).get_json()
    assert plan["targets"] == ["other"] and plan["skipped"] == []


def test_action_gate_allows_one_change_at_a_time_and_spaces_bulk_changes():
    now = [100.0]
    gate = grandmaster.ActionGate(cooldown=10, clock=lambda: now[0])
    assert gate.try_enter(bulk=False) == (True, "")
    busy, why = gate.try_enter(bulk=False)
    assert not busy and "in progress" in why
    gate.leave()
    assert gate.try_enter(bulk=True) == (True, "")
    gate.leave()
    now[0] = 103.0
    refused, why = gate.try_enter(bulk=True)
    assert not refused and "wait 7s" in why
    assert gate.try_enter(bulk=False) == (True, "")  # a single change is not rate limited
    gate.leave()
    now[0] = 111.0
    assert gate.try_enter(bulk=True) == (True, "")
    gate.leave()


def test_bulk_changes_are_rate_limited_over_http(client, monkeypatch):
    monkeypatch.setattr(web_app, "_GRANDMASTER_GATE", grandmaster.ActionGate(cooldown=60))
    assert client.post("/api/grandmaster/restart", json={"all": True}).status_code == 200
    again = client.post("/api/grandmaster/restart", json={"all": True})
    assert again.status_code == 429 and "wait" in again.get_json()["error"]


def test_every_change_is_written_to_the_audit_log(cards):
    payload = {"manifest_path": "r/m.toml", "name": "other"}
    cards.post("/api/grandmaster/restart", json=payload)
    cards.post("/api/grandmaster/home/hide", json={"name": "magneto-web", "hidden": True})
    cards.post("/api/grandmaster/restart", json={"all": True})
    entries = cards.get("/api/grandmaster/audit").get_json()["entries"]
    assert [(e["action"], e["target"], e["ok"]) for e in entries][:4] == [
        ("bulk-restart", "all", True),
        ("restart", "other", True),
        ("home-hide", "magneto-web", True),
        ("restart", "other", True),
    ]
    assert all("ts" in e and "from" in e for e in entries)


def test_audit_log_is_owner_only_bounded_and_tolerates_junk(tmp_path, monkeypatch):
    path = tmp_path / "log" / "audit.jsonl"
    monkeypatch.setattr(grandmaster, "AUDIT_MAX_BYTES", 400)
    monkeypatch.setattr(grandmaster, "AUDIT_KEEP_LINES", 3)
    for number in range(20):
        grandmaster.audit({"action": "restart", "target": f"app-{number}", "ok": True}, path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert len(path.read_text().splitlines()) <= 4
    assert grandmaster.audit_tail(10, path)[0]["target"] == "app-19"
    path.write_text("not json\n[1]\n" + path.read_text())
    assert [e["target"] for e in grandmaster.audit_tail(2, path)] == ["app-19", "app-18"]
    assert grandmaster.audit_tail(5, tmp_path / "missing.jsonl") == []


def test_audit_never_raises_when_the_log_cannot_be_written(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    grandmaster.audit({"action": "restart"}, blocker / "audit.jsonl")  # parent is a file
