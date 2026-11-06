# ruff: noqa: F811
"""Saved order templates on the Clockwork Tradility page: the same rules and file as the standalone app."""

import json
import stat

import pytest

from tests.test_tradility_orders import api  # noqa: F401
from tests.test_web_lists import client, list_files, web_app  # noqa: F401
from web import order_templates as ot
from web import tradility_orders as t


def ladder(**kw):
    return {
        "name": "Dip ladder",
        "side": "buy",
        "tif": "gtc",
        "session": "normal",
        "rungs": [
            {"quantity": 1, "offset_pct": -1},
            {"quantity": 2, "offset_pct": -2},
            {"quantity": 3, "offset_pct": -3},
        ],
        **kw,
    }


@pytest.fixture
def templates_file(tmp_path, monkeypatch):
    path = tmp_path / "state" / "order-templates.json"
    monkeypatch.setenv("CLOCKWORK_TRADILITY_TEMPLATES_FILE", str(path))
    return path


def test_a_template_is_validated_and_cleaned_like_the_standalone_apps():
    cleaned = ot.clean(ladder(name="  Dip   ladder  "))
    assert cleaned["name"] == "Dip ladder" and cleaned["rungs"][1] == {
        "quantity": 2.0,
        "offset_pct": -2.0,
    }
    assert ot.clean(ladder(side="sell", rungs=[{"quantity": 1, "offset_pct": 2}]))["side"] == "sell"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": ""}, "name is required"),
        ({"side": "hold"}, "side must be buy, sell or both"),
        ({"tif": "forever"}, "duration must be day or gtc"),
        ({"session": "night"}, "hours must be normal or extended"),
        ({"rungs": []}, "at least one rung"),
        ({"rungs": [{"quantity": 1, "offset_pct": -1}] * 21}, "at most 20 rungs"),
        ({"rungs": [{"quantity": 0, "offset_pct": -1}]}, "rung 1: quantity must be positive"),
        ({"rungs": [{"quantity": 1, "offset_pct": -60}]}, "within 50%"),
        ({"rungs": [{"quantity": 1, "offset_pct": 1}]}, "rung 1: a buy rung sits at or below"),
        ({"side": "sell", "rungs": [{"quantity": 1, "offset_pct": -1}]}, "a sell rung at or above"),
    ],
)
def test_bad_templates_are_refused_with_the_reason(change, message):
    with pytest.raises(ValueError, match=message):
        ot.clean(ladder(**change))


def test_the_default_file_is_the_standalone_apps_unless_overridden(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOCKWORK_TRADILITY_TEMPLATES_FILE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert ot.default_file() == tmp_path / "tradility" / "order-templates.json"
    monkeypatch.setenv("CLOCKWORK_TRADILITY_TEMPLATES_FILE", str(tmp_path / "x.json"))
    assert ot.default_file() == tmp_path / "x.json"


def test_storage_is_owner_only_and_names_are_unique(tmp_path):
    path = tmp_path / "state" / "order-templates.json"
    templates = ot.load(path)
    first = ot.add(templates, ladder())
    ot.save(path, templates)
    assert (
        stat.S_IMODE(path.stat().st_mode) == 0o600
        and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    )
    with pytest.raises(ValueError, match="already exists"):
        ot.add(templates, ladder(name="dip LADDER"))
    assert ot.delete(templates, first["id"]) is True and ot.load(tmp_path / "missing.json") == []


def test_the_routes_create_list_update_and_delete_templates(api, templates_file):
    http, _ = api
    assert http.get("/api/tradility-templates").get_json() == {"ok": True, "templates": []}
    created = http.post("/api/tradility-templates", json=ladder())
    assert created.status_code == 201
    template = created.get_json()["template"]
    assert http.get("/api/tradility-templates").get_json()["templates"] == [template]
    changed = http.put(f"/api/tradility-templates/{template['id']}", json=ladder(name="Renamed"))
    assert changed.status_code == 200 and changed.get_json()["template"]["name"] == "Renamed"
    assert http.put("/api/tradility-templates/missing", json=ladder()).status_code == 404
    assert http.delete(f"/api/tradility-templates/{template['id']}").status_code == 200
    assert http.delete(f"/api/tradility-templates/{template['id']}").status_code == 404
    assert json.loads(templates_file.read_text())["templates"] == []


def test_the_routes_refuse_bad_templates_and_a_missing_csrf_token(api, templates_file):
    http, _ = api
    bad = http.post("/api/tradility-templates", json=ladder(rungs=[]))
    assert bad.status_code == 400 and "at least one rung" in bad.get_json()["error"]
    assert (
        http.post("/api/tradility-templates", data="x", content_type="text/plain").status_code
        == 400
    )
    token = http.environ_base.pop("HTTP_X_CSRF_TOKEN")
    assert http.post("/api/tradility-templates", json=ladder()).status_code == 403
    assert http.delete("/api/tradility-templates/x").status_code == 403
    http.environ_base["HTTP_X_CSRF_TOKEN"] = token
    assert http.get("/api/tradility-templates").get_json()["templates"] == []


def test_ladder_steps_are_forwarded_to_the_trader_and_only_those_steps(api, monkeypatch):
    http, _ = api
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path, body))
        return 200, {"ok": True, "phrase": "BUY 1 AAPL"}

    monkeypatch.setattr(t, "call_trader", fake)
    body = {"ticker": "AAPL", "side": "buy", "rungs": [{"quantity": 1, "offset_pct": -1}]}
    assert http.post("/api/tradility-trade/batch/preview", json=body).status_code == 200
    confirm = {"preview_id": "x", "phrase": "BUY 1 AAPL"}
    assert http.post("/api/tradility-trade/batch/confirm", json=confirm).status_code == 200
    assert seen == [("POST", "/batch/preview", body), ("POST", "/batch/confirm", confirm)]
    assert http.post("/api/tradility-trade/batch/place", json=body).status_code == 404
    assert (
        http.post(
            "/api/tradility-trade/batch/preview", data="x", content_type="text/plain"
        ).status_code
        == 400
    )
    assert len(seen) == 2


def test_the_clockwork_page_has_the_order_templates_tab_and_the_ladder_flow(api):
    http, _ = api
    page = http.get("/to-tradility").get_data(as_text=True)
    for hook in (
        'id="tab-templates"',
        'id="templates-view"',
        "function showView",
        "function previewLadder",
        "function confirmLadder",
        "function importPolicyTemplates",
        "/api/tradility-trade/batch/preview",
        "/api/tradility-templates",
    ):
        assert hook in page, hook
    assert "Type <strong>${esc(p.phrase)}</strong> to confirm" in page and "Stopped after" in page


def straddle(**kw):
    rungs = [
        {"side": "buy", "quantity": 1, "offset_pct": -1},
        {"side": "buy", "quantity": 2, "offset_pct": -2},
        {"side": "sell", "quantity": 1, "offset_pct": 1},
    ]
    return {**ladder(side="both"), "rungs": rungs, **kw}


def test_a_two_sided_template_keeps_each_rungs_side_and_a_single_side_one_stores_none():
    cleaned = ot.clean(straddle())
    assert cleaned["side"] == "both"
    assert [(r["side"], r["offset_pct"]) for r in cleaned["rungs"]] == [
        ("buy", -1.0),
        ("buy", -2.0),
        ("sell", 1.0),
    ]
    one = ot.clean(ladder(rungs=[{"side": "buy", "quantity": 1, "offset_pct": -1}]))
    assert "side" not in one["rungs"][0]


@pytest.mark.parametrize(
    ("rungs", "message"),
    [
        (
            [{"quantity": 1, "offset_pct": -1}, {"side": "sell", "quantity": 1, "offset_pct": 1}],
            "rung 1: a rung in a two-sided template needs a side",
        ),
        (
            [
                {"side": "buy", "quantity": 1, "offset_pct": 1},
                {"side": "sell", "quantity": 1, "offset_pct": 1},
            ],
            "rung 1: a buy rung sits at or below",
        ),
        (
            [
                {"side": "buy", "quantity": 1, "offset_pct": -1},
                {"side": "sell", "quantity": 1, "offset_pct": -1},
            ],
            "rung 2: a buy rung sits at or below",
        ),
        (
            [{"side": "buy", "quantity": 1, "offset_pct": -1}],
            "at least one buy rung and one sell rung",
        ),
    ],
)
def test_bad_two_sided_templates_are_refused_with_the_reason(rungs, message):
    with pytest.raises(ValueError, match=message):
        ot.clean(straddle(rungs=rungs))


def test_a_single_side_template_refuses_a_rung_of_the_other_side():
    with pytest.raises(ValueError, match="every rung of a buy template is a buy"):
        ot.clean(ladder(rungs=[{"side": "sell", "quantity": 1, "offset_pct": 1}]))


def test_two_sided_templates_round_trip_through_the_routes(api, templates_file):
    http, _ = api
    created = http.post("/api/tradility-templates", json=straddle())
    assert created.status_code == 201 and created.get_json()["template"]["side"] == "both"
    stored = http.get("/api/tradility-templates").get_json()["templates"][0]
    assert [r["side"] for r in stored["rungs"]] == ["buy", "buy", "sell"]


def test_the_clockwork_page_offers_two_sided_ladders(api):
    http, _ = api
    page = http.get("/to-tradility").get_data(as_text=True)
    for hook in (
        "buy + sell (straddle)",
        "const rungSide",
        "data-rside",
        "notional_by_side",
        "tpl-side-sell",
    ):
        assert hook in page, hook
