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
    aapl = t.position_from_holdings("AAPL", HOLDINGS)
    assert (aapl["quantity"], aapl["avg_entry"], aapl["source"]) == (10.0, 150.0, "holdings")
    assert aapl["partial"] is False and aapl["covered_quantity"] == 10.0
    assert t.position_from_holdings("BTC-USD", HOLDINGS)["avg_entry"] == 50000.0
    assert t.position_from_holdings("NOPE", HOLDINGS) is None


def test_no_cost_basis_gives_no_average_and_a_partial_one_is_flagged_not_spread_over_every_share():
    assert t.position_from_holdings("MSFT", HOLDINGS)["avg_entry"] is None
    part = t.position_from_holdings(
        "PART", HOLDINGS
    )  # 3 shares cost $300, 1 share has no cost on record
    assert (
        part["avg_entry"] == 100.0 and part["covered_quantity"] == 3.0 and part["partial"] is True
    )
    assert part["quantity"] == 4.0


VWO = [
    {
        "ticker": "VWO",
        "total_quantity": 18.635,
        "total_cost_basis": 673.88,  # only the Fidelity lot has a cost
        "accounts": [
            {"institution": "cs", "quantity": 4.0, "cost_basis": None},
            {"institution": "fid", "quantity": 14.635, "cost_basis": 673.88},
        ],
    }
]


def test_the_average_is_not_diluted_by_shares_with_no_cost_on_record():
    got = t.position_from_holdings("VWO", VWO)
    assert got["avg_entry"] == pytest.approx(
        673.88 / 14.635
    )  # $46.05, not 673.88 / 18.635 = $36.16
    assert got["partial"] is True and got["covered_quantity"] == pytest.approx(14.635)


def test_schwabs_live_average_price_completes_the_picture_and_replaces_its_statement_lot():
    live = [
        {"broker": "schwab", "quantity": 4.0, "average_price": 58.665},
        {"broker": "schwab", "quantity": 0.0},
    ]
    got = t.position_from_holdings("VWO", VWO, live)
    assert got["avg_entry"] == pytest.approx((4 * 58.665 + 673.88) / 18.635)
    assert (
        got["partial"] is False
        and got["source"] == "live+holdings"
        and got["quantity"] == pytest.approx(18.635)
    )
    # Schwab no longer holds it: the statement's Schwab lot is dropped, only the Fidelity lot remains
    sold = t.position_from_holdings("VWO", VWO, [{"broker": "schwab", "quantity": 0.0}])
    assert sold["quantity"] == pytest.approx(14.635) and sold["avg_entry"] == pytest.approx(
        673.88 / 14.635
    )


def test_the_orders_route_uses_the_live_rows_when_the_trader_service_answers(api, monkeypatch):
    c, _ = api
    web_app.HOLDINGS_AGGREGATE_FILE.write_text(
        json.dumps({"holdings": VWO})
    )  # the fixture's temp file
    monkeypatch.setattr(
        t,
        "call_trader",
        lambda m, p, b=None: (
            200,
            {
                "ok": True,
                "accounts": [{"broker": "schwab", "quantity": 4.0, "average_price": 58.665}],
            },
        ),
    )
    pos = c.get("/api/tradility-orders/VWO").get_json()["position"]
    assert (
        pos["source"] == "live+holdings"
        and pos["partial"] is False
        and round(pos["avg_entry"], 2) == 48.75
    )
    monkeypatch.setattr(t, "call_trader", lambda m, p, b=None: (503, {"ok": False}))
    pos = c.get("/api/tradility-orders/VWO").get_json()["position"]
    assert (
        pos["source"] == "holdings"
        and pos["partial"] is True
        and round(pos["avg_entry"], 2) == 46.05
    )


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
    # never reach a trader service that may be running on this machine
    monkeypatch.setenv("CLOCKWORK_TRADILITY_TRADER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_EXPORT", str(tmp_path / "broker-orders.json"))
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


EXPORT = {
    "generated_at": "2026-11-01T01:00:00+00:00",
    "sources": {"schwab": {"ok": True, "orders": 2}, "binance": {"ok": False, "error": "x"}},
    "orders": {
        "AAPL": [
            {
                "id": "schwab-1",
                "source": "schwab",
                "side": "buy",
                "kind": "limit",
                "status": "filled",
                "quantity": 1,
                "price": 10.0,
                "date": "2026-10-01",
                "note": "",
            },
            {
                "id": "schwab-2",
                "source": "schwab",
                "side": "sell",
                "kind": "limit",
                "status": "open",
                "quantity": 1,
                "price": 20.0,
                "date": "2026-10-20",
                "note": "",
            },
        ],
        "BTC": [
            {
                "id": "binance-1",
                "source": "binance",
                "side": "buy",
                "kind": "limit",
                "status": "open",
                "quantity": 1,
                "price": 5.0,
                "date": "2026-10-21",
                "note": "",
            },
        ],
    },
}


def test_broker_orders_are_listed_beside_manual_ones_marked_by_source_and_open_first():
    manual = {
        "AAPL": [
            {
                "id": "m1",
                "side": "buy",
                "kind": "limit",
                "status": "filled",
                "quantity": 1,
                "price": 1.0,
                "date": "2026-10-10",
                "note": "",
            }
        ]
    }
    got = t.summary("AAPL", manual, HOLDINGS, EXPORT)
    assert [(o["id"], o["source"]) for o in got["orders"]] == [
        ("schwab-2", "schwab"),
        ("m1", "manual"),
        ("schwab-1", "schwab"),
    ]
    assert got["synced"] == {
        "generated_at": EXPORT["generated_at"],
        "sources": {"schwab": True, "binance": False},
    }


def test_synced_fills_never_become_the_fallback_average_entry():
    got = t.summary("MSFT", {}, HOLDINGS, {**EXPORT, "orders": {"MSFT": EXPORT["orders"]["AAPL"]}})
    assert (
        got["position"]["avg_entry"] is None
    )  # a year of fills is not the cost of the whole position


def test_crypto_export_rows_match_the_usd_pair(api, tmp_path, monkeypatch):
    c, _ = api
    export = tmp_path / "export.json"
    export.write_text(json.dumps(EXPORT))
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_EXPORT", str(export))
    got = c.get("/api/tradility-orders/BTC-USD").get_json()
    assert [o["id"] for o in got["orders"]] == ["binance-1"]
    monkeypatch.setenv("CLOCKWORK_TRADILITY_ORDERS_EXPORT", str(tmp_path / "absent.json"))
    assert c.get("/api/tradility-orders/BTC-USD").get_json()["orders"] == []


def test_the_table_shows_the_source_and_leaves_broker_rows_read_only():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "function syncNote",
        "<th>Source</th>",
        "o.source && o.source !== 'manual' ? ''",
    ):
        assert needle in html, needle


# -- placing orders goes through the local trader service ---------------------------------------


def test_trade_status_says_unavailable_when_the_service_is_not_running(api, monkeypatch):
    c, _ = api
    monkeypatch.setenv("CLOCKWORK_TRADILITY_TRADER_URL", "http://127.0.0.1:9")  # nothing listens
    got = c.get("/api/tradility-trade/status").get_json()
    assert got == {"ok": True, "available": False, "enabled": False}
    code = c.post("/api/tradility-trade/preview", json={"ticker": "AAPL"})
    assert code.status_code == 503 and "not running" in code.get_json()["error"]


def test_trade_steps_are_forwarded_with_the_service_header_and_errors_pass_through(
    api, monkeypatch
):
    c, _ = api
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path, body))
        if path == "/preview":
            return 200, {"ok": True, "preview_id": "tok", "phrase": "BUY AAPL"}
        if path == "/confirm":
            return 422, {"ok": False, "error": 'type exactly "BUY AAPL" to confirm'}
        return 200, {"ok": True, "enabled": True}

    monkeypatch.setattr(t, "call_trader", fake)
    assert c.get("/api/tradility-trade/status").get_json()["available"] is True
    assert (
        c.post("/api/tradility-trade/preview", json={"ticker": "AAPL"}).get_json()["preview_id"]
        == "tok"
    )
    refused = c.post("/api/tradility-trade/confirm", json={"preview_id": "tok", "phrase": "no"})
    assert refused.status_code == 422 and "type exactly" in refused.get_json()["error"]
    assert [s[1] for s in seen] == ["/status", "/preview", "/confirm"]


def test_only_the_three_steps_exist_and_a_body_is_required_and_csrf_applies(api, monkeypatch):
    c, _ = api
    monkeypatch.setattr(t, "call_trader", lambda *a, **k: (200, {"ok": True}))
    assert c.post("/api/tradility-trade/withdraw", json={}).status_code == 404
    assert (
        c.post("/api/tradility-trade/preview", data="x", content_type="text/plain").status_code
        == 400
    )
    del c.environ_base["HTTP_X_CSRF_TOKEN"]
    c.environ_base["HTTP_ORIGIN"] = "https://evil.example"
    assert c.post("/api/tradility-trade/preview", json={"ticker": "AAPL"}).status_code == 403


def test_the_form_previews_then_requires_the_typed_phrase_and_never_auto_sends():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "startPreview(",
        'id="trade-phrase"',
        "This sends a real order.",
        "This cancels a real order.",
        "tradeStep = null;   // a preview is single use",
        "broker-cancel",
        "canTrade()",
    ):
        assert needle in html, needle
    assert html.index("tradeCall('preview'") < html.index("tradeCall('confirm'")


def test_the_quote_route_forwards_to_the_service_and_rejects_bad_tickers(api, monkeypatch):
    c, _ = api
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path))
        return 200, {"ok": True, "mark": 100.0, "buy_price": 97.5, "sell_price": 102.5}

    monkeypatch.setattr(t, "call_trader", fake)
    assert c.get("/api/tradility-trade/quote/aapl").get_json()["buy_price"] == 97.5
    assert seen == [("GET", "/quote/AAPL")]
    assert c.get("/api/tradility-trade/quote/not a ticker!").status_code == 404


def test_the_form_defaults_to_a_gtc_limit_around_the_mark_and_makes_you_pick_the_account():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        '<option value="">Account…</option>',
        "function fillDefaults",
        "default: limit ",
        "tradeForm.elements.side.addEventListener('change', fillDefaults)",
        "includes('*')",
        "account: f.account || ''",
    ):
        assert needle in html, needle


def test_the_accounts_route_forwards_and_the_panel_shows_value_cash_buying_power_and_margin(
    api, monkeypatch
):
    from pathlib import Path

    c, _ = api
    monkeypatch.setattr(
        t,
        "call_trader",
        lambda m, p, b=None: (
            (200, {"ok": True, "accounts": [], "sources": {}}) if p == "/accounts" else (500, {})
        ),
    )
    assert c.get("/api/tradility-accounts").get_json()["ok"] is True
    monkeypatch.setattr(
        t,
        "call_trader",
        lambda *a, **k: (503, {"ok": False, "error": "the trader service is not running"}),
    )
    assert c.get("/api/tradility-accounts").status_code == 503
    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        'id="accounts-panel"',
        "margin eligible",
        "Buying power",
        "Total value",
        "Key can withdraw",
        "This API key can withdraw funds",
        "function showAccountFunds",
    ):
        assert needle in html, needle


def test_a_crypto_ticker_explains_why_binance_trading_is_off():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    assert "Binance.US trading is not enabled" in html and "includes('binance')" in html


def test_transfer_history_and_withdrawal_steps_are_forwarded_and_nothing_else_exists(
    api, monkeypatch
):
    c, _ = api
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path))
        return 200, {"ok": True, "transfers": [], "sources": {}}

    monkeypatch.setattr(t, "call_trader", fake)
    assert c.get("/api/tradility-transfers").get_json()["ok"] is True
    assert (
        c.post(
            "/api/tradility-trade/withdraw/preview", json={"label": "x", "amount": 1}
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/api/tradility-trade/withdraw/confirm", json={"preview_id": "t", "phrase": "p"}
        ).status_code
        == 200
    )
    assert c.post("/api/tradility-trade/withdraw/anything", json={}).status_code == 404
    assert (
        c.post(
            "/api/tradility-trade/withdraw/preview", data="x", content_type="text/plain"
        ).status_code
        == 400
    )
    assert seen == [
        ("GET", "/transfers"),
        ("POST", "/withdraw/preview"),
        ("POST", "/withdraw/confirm"),
    ]


def test_the_page_shows_history_without_journals_and_makes_withdrawals_use_a_typed_phrase():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "Deposits and withdrawals",
        "include internal journals",
        "This sends real funds and cannot be undone.",
        "Schwab does not offer money movement",
        "moneyStep = null;   // single use",
        "Binance.US withdrawals are turned off",
    ):
        assert needle in html, needle
    assert html.index("withdrawCall('preview'") < html.index("withdrawCall('confirm'")


def test_the_form_offers_the_trading_hours_and_duration_prefilled_from_the_policy():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        'name="session" aria-label="Trading hours"',
        'name="tif" aria-label="Duration"',
        "time_in_force || 'gtc'",
        "...(f.session ? {session: f.session, tif: f.tif} : {})",
    ):
        assert needle in html, needle


def test_the_form_offers_every_schwab_duration_and_session_and_keeps_to_valid_combinations():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "['fok', 'Fill or kill']",
        "['ioc', 'Immediate or cancel']",
        "['end_of_week', 'End of week']",
        "['end_of_month', 'End of month']",
        "['next_end_of_month', 'Next end of month']",
        "['am', 'Pre-market only']",
        "['pm', 'After-hours only']",
        "['extended', 'Extended",
        "function syncOrderChoices",
        "TIF_LABEL[o.tif]",
    ):
        assert needle in html, needle


def test_the_form_offers_one_click_order_templates_priced_from_the_mark():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        'id="trade-templates"',
        "function paintTemplates",
        "function applyTemplate",
        "tradeQuote.templates",
        "form.elements.price.value = t.price",
        "template: ${t.name}",
    ):
        assert needle in html, needle
    # a template only fills the form; sending still needs a preview and the typed phrase
    assert (
        html.index("function applyTemplate")
        < html.index("tradeCall('preview'")
        < html.index("tradeCall('confirm'")
    )


def test_the_preview_shows_the_position_impact_and_a_place_order_button_when_configured():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "function impactTable",
        "All live accounts",
        "Share of total",
        "available for new stock orders after",
        "st.confirmation === 'button'",
        "'Place order'",
        "impact: pv.impact, confirmation: pv.confirmation",
        "const phrase = phraseInput ? phraseInput.value : ''",
    ):
        assert needle in html, needle
    # cancellations never use the button-only path
    assert "st.kind === 'place' && st.confirmation === 'button'" in html


def test_the_form_warns_before_the_preview_when_an_account_cannot_afford_the_buy_and_labels_what_all_means():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "this account can spend on new stock orders, so Schwab is likely to reject it",
        "' available'",
        "All live accounts (Schwab + Binance.US)",
        "Plus ${im.everything.institutions",
        "from statements",
        "Not read live.",
    ):
        assert needle in html, needle


def test_the_holdings_route_forwards_and_the_page_shows_where_a_stock_is_held_per_account(
    api, monkeypatch
):
    from pathlib import Path

    c, _ = api
    seen = []
    monkeypatch.setattr(
        t,
        "call_trader",
        lambda m, p, b=None: seen.append((m, p))
        or (200, {"ok": True, "symbol": "CAT", "accounts": [], "statements": []}),
    )
    assert c.get("/api/tradility-trade/holdings/cat").get_json()["symbol"] == "CAT"
    assert seen == [("GET", "/holdings/CAT")]
    assert c.get("/api/tradility-trade/holdings/not a ticker!").status_code == 404
    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "function holdingsTable",
        "Where you hold",
        "(this order)",
        "function loadHeld",
        'id="held-box"',
        "Available for new stock orders",
        "spendable != null",
    ):
        assert needle in html, needle
    assert "cash_available_to_trade" not in html  # the field that was always 0


def test_the_fidelity_ticket_route_forwards_and_the_form_only_prepares_never_sends(
    api, monkeypatch
):
    from pathlib import Path

    c, _ = api
    seen = []
    monkeypatch.setattr(
        t,
        "call_trader",
        lambda m, p, b=None: seen.append((m, p)) or (200, {"ok": True, "venue": "fidelity"}),
    )
    assert (
        c.post("/api/tradility-trade/fidelity-ticket", json={"ticker": "AAPL"}).get_json()["venue"]
        == "fidelity"
    )
    assert seen == [("POST", "/fidelity/ticket")]
    assert (
        c.post(
            "/api/tradility-trade/fidelity-ticket", data="x", content_type="text/plain"
        ).status_code
        == 400
    )
    html = (
        Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
    ).read_text(encoding="utf-8")
    for needle in (
        "function startFidelityTicket",
        "function fidelityPanel",
        "Nothing is sent from here",
        "I placed it at Fidelity",
        "Open Fidelity's trade page",
        "Prepare Fidelity ticket",
        'value="fid:',
        "(submitted at Fidelity)",
        "Fidelity has no order API",
    ):
        assert needle in html, needle
    # a Fidelity account never reaches the preview/confirm path that sends orders
    assert html.index("if (fid) return startFidelityTicket(form, fid);") < html.index(
        "tradeCall('preview'"
    )
