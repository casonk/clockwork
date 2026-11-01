"""Average entry, order history and open orders recorded per Tradility ticker."""

import json

import pytest

from tests.test_web_lists import client, list_files, web_app  # noqa: F401
from web import tradility_orders as t

HOLDINGS = [
    {"ticker": "AAPL", "total_quantity": 10.0, "total_cost_basis": 1500.0},
    {"ticker": "BTC", "total_quantity": 2.0, "total_cost_basis": 100000.0},
    {"ticker": "MSFT", "total_quantity": 4.0, "total_cost_basis": None},
    {"ticker": "PART", "total_quantity": 3.0, "total_cost_basis": 300.0},
    {"ticker": "PART", "total_quantity": 1.0, "total_cost_basis": None},
]


def test_average_entry_is_cost_basis_over_quantity_and_crypto_matches_its_usd_pair():
    assert t.position_from_holdings("AAPL", HOLDINGS) == {
        "quantity": 10.0,
        "avg_entry": 150.0,
        "source": "holdings",
    }
    assert t.position_from_holdings("BTC-USD", HOLDINGS)["avg_entry"] == 50000.0
    assert t.position_from_holdings("NOPE", HOLDINGS) is None


def test_a_missing_or_partial_cost_basis_gives_no_average():
    assert t.position_from_holdings("MSFT", HOLDINGS)["avg_entry"] is None
    assert t.position_from_holdings("PART", HOLDINGS)["avg_entry"] is None  # one lot unpriced


def test_filled_buys_are_the_fallback_average_and_open_orders_are_not_counted():
    orders = {
        "MSFT": [
            {"side": "buy", "status": "filled", "quantity": 2, "price": 100, "date": "2026-01-01"},
            {"side": "buy", "status": "filled", "quantity": 2, "price": 200, "date": "2026-02-01"},
            {"side": "buy", "status": "open", "quantity": 100, "price": 1, "date": "2026-03-01"},
            {"side": "sell", "status": "filled", "quantity": 1, "price": 999, "date": "2026-03-02"},
        ]
    }
    got = t.summary("MSFT", orders, HOLDINGS)
    assert got["position"]["avg_entry"] == 150.0 and got["position"]["source"] == "orders"
    assert got["position"]["quantity"] == 4.0
    assert [o["status"] for o in got["orders"]][0] == "open"  # open orders first


@pytest.mark.parametrize(
    "bad",
    [
        {"side": "hold", "quantity": 1, "price": 1},
        {"side": "buy", "quantity": 0, "price": 1},
        {"side": "buy", "quantity": "x", "price": 1},
        {"side": "buy", "quantity": 1},  # limit needs a price
        {"side": "buy", "kind": "market", "status": "filled", "quantity": 1},
        {"side": "buy", "quantity": 1, "price": 1, "date": "2026-13-40"},
        {"side": "buy", "quantity": 1, "price": 1, "note": "x" * 121},
        {"side": "buy", "quantity": float("inf"), "price": 1},
        "not an object",
    ],
)
def test_bad_orders_are_refused(bad):
    with pytest.raises(ValueError):
        t.clean_order(bad)


def test_an_open_market_order_needs_no_price():
    assert t.clean_order({"side": "buy", "kind": "market", "quantity": 1})["price"] is None


@pytest.fixture
def api(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_FILE", str(tmp_path / "orders.json"))
    held = tmp_path / "holdings.json"
    held.write_text(json.dumps({"holdings": HOLDINGS}))
    monkeypatch.setattr(web_app, "HOLDINGS_AGGREGATE_FILE", held)
    with client.session_transaction() as session:
        session["_csrf_token"] = "test-token"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "test-token"
    return client, tmp_path / "orders.json"


def test_orders_are_added_changed_and_deleted_over_http_and_stored_owner_only(api):
    c, store = api
    first = c.get("/api/tradility-orders/aapl").get_json()
    assert first["position"]["avg_entry"] == 150.0 and first["orders"] == []

    made = c.post(
        "/api/tradility-orders/AAPL",
        json={"side": "buy", "kind": "limit", "quantity": 5, "price": 140},
    )
    assert made.status_code == 201
    order = made.get_json()["orders"][0]
    assert order["status"] == "open" and len(order["id"]) == 12
    assert (store.stat().st_mode & 0o777) == 0o600

    full = {
        **{k: order[k] for k in ("side", "kind", "quantity", "price", "date", "note")},
        "status": "filled",
    }
    done = c.put(f"/api/tradility-orders/AAPL/{order['id']}", json=full).get_json()
    assert done["orders"][0]["status"] == "filled"

    gone = c.delete(f"/api/tradility-orders/AAPL/{order['id']}").get_json()
    assert gone["orders"] == [] and "AAPL" not in json.loads(store.read_text())["orders"]
    assert c.delete(f"/api/tradility-orders/AAPL/{order['id']}").status_code == 404


def test_bad_input_and_bad_tickers_are_refused_over_http(api):
    c, store = api
    assert c.post("/api/tradility-orders/AAPL", json={"side": "buy"}).status_code == 400
    assert (
        c.post("/api/tradility-orders/AAPL", data="nope", content_type="text/plain").status_code
        == 400
    )
    assert c.get("/api/tradility-orders/not a ticker!").status_code == 404
    assert not store.exists()


def test_a_missing_holdings_file_still_serves_orders(api, monkeypatch, tmp_path):
    c, _ = api
    monkeypatch.setattr(web_app, "HOLDINGS_AGGREGATE_FILE", tmp_path / "absent.json")
    got = c.get("/api/tradility-orders/AAPL").get_json()
    assert got["ok"] and got["position"]["avg_entry"] is None


def test_the_detail_view_draws_the_average_entry_as_a_horizontal_line_and_lists_orders():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "function chartLevels",
        "Avg entry",
        "setLineDash",
        "/api/tradility-orders/",
        "detail-orders-card",
    ):
        assert needle in html, needle
