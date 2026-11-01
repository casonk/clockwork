# ruff: noqa: F811
"""To Invest targets shown on the Tradility cards, in the detail view and in the order preview."""

import json
from pathlib import Path

from tests.test_web_lists import client, list_files, web_app  # noqa: F401

HTML = (Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html").read_text(
    encoding="utf-8"
)

INVEST = {
    "categories": [
        {
            "name": "Stocks",
            "target_mode": "basket",
            "basket_target_pct": "70",
            "items": [
                {
                    "ticker": "AAPL",
                    "holding": True,
                    "name": "Apple",
                    "allocation_pct": "0.30",
                    "target_pct": "1",
                },
                {"ticker": "MSFT", "holding": False, "target_pct": "2"},
                {"ticker": "NOGOAL", "holding": True, "allocation_pct": "5"},
            ],
        },
        {
            "name": "Crypto",
            "items": [{"ticker": "BTC", "holding": True, "allocation_pct": "4", "target_pct": "3"}],
        },
        {"name": "ETFs", "items": []},
    ]
}


def setup(list_files, client):
    list_files["INVEST_LIST_FILE"].write_text(json.dumps(INVEST))
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(
        json.dumps({"holdings": [{"total_ending_value": 60000}, {"total_ending_value": 40000}]})
    )
    with client.session_transaction() as session:
        session["_csrf_token"] = "tok"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "tok"


def test_targets_are_global_percentages_with_the_current_allocation_and_the_gap(client, list_files):
    setup(list_files, client)
    got = client.get("/api/tradility-targets").get_json()
    aapl = got["targets"]["AAPL"]
    assert aapl["category"] == "Stocks" and aapl["mode"] == "basket" and aapl["target_input"] == 1.0
    assert aapl["target"] == 0.7  # 70% basket x 1% of the basket
    assert aapl["current"] == 0.3 and round(aapl["gap"], 4) == 0.4
    assert got["targets"]["MSFT"]["current"] is None and "gap" not in got["targets"]["MSFT"]
    assert got["targets"]["NOGOAL"]["target"] is None
    assert got["targets"]["BTC"]["target"] == 3.0
    assert (
        got["targets"]["BTC-USD"]["ticker"] == "BTC"
    )  # crypto is also reachable by its Tradility name
    assert got["portfolio_value"] == 100000 and got["categories"] == ["Stocks", "Crypto", "ETFs"]


def test_a_target_is_set_changed_cleared_and_validated(client, list_files):
    setup(list_files, client)
    ok = client.post("/api/tradility-targets/AAPL", json={"target_pct": "2.5"})
    assert ok.get_json()["target"]["target_input"] == 2.5
    stored = json.loads(list_files["INVEST_LIST_FILE"].read_text())
    assert stored["categories"][0]["items"][0]["target_pct"] == "2.5"
    client.post("/api/tradility-targets/AAPL", json={"target_pct": ""})
    assert (
        "target_pct"
        not in json.loads(list_files["INVEST_LIST_FILE"].read_text())["categories"][0]["items"][0]
    )
    assert client.post("/api/tradility-targets/AAPL", json={"target_pct": "150"}).status_code == 400
    assert client.post("/api/tradility-targets/AAPL", json={"target_pct": "x"}).status_code == 400
    assert (
        client.post("/api/tradility-targets/AAPL", data="x", content_type="text/plain").status_code
        == 400
    )
    assert client.post("/api/tradility-targets/not a ticker!", json={}).status_code == 404


def test_an_unlisted_ticker_is_added_to_the_chosen_category_and_crypto_uses_its_plain_symbol(
    client, list_files
):
    setup(list_files, client)
    assert (
        client.post("/api/tradility-targets/NVDA", json={"target_pct": "1"}).status_code == 400
    )  # no category
    done = client.post(
        "/api/tradility-targets/NVDA", json={"target_pct": "1", "category": "Stocks"}
    )
    assert done.get_json()["target"]["category"] == "Stocks"
    client.post("/api/tradility-targets/ETH-USD", json={"target_pct": "2", "category": "Crypto"})
    crypto = [
        c
        for c in json.loads(list_files["INVEST_LIST_FILE"].read_text())["categories"]
        if c["name"] == "Crypto"
    ][0]
    assert "ETH" in [i["ticker"] for i in crypto["items"]]
    assert (
        client.post("/api/tradility-targets/NVDA", json={"category": "Nope"}).status_code == 200
    )  # already added
    assert client.post("/api/tradility-targets/ZZZZ", json={"category": "Nope"}).status_code == 400


def test_the_page_shows_targets_on_cards_the_detail_view_and_the_order_preview():
    for needle in (
        "function targetChip",
        "card-target",
        "Sort: furthest below target",
        "target-gap",
        'id="detail-target-card"',
        "function targetCardHtml",
        "Add to To Invest",
        "Save target",
        "function targetPreviewLine",
        "moves the holding from",
        "/api/tradility-targets",
    ):
        assert needle in HTML, needle


def test_each_open_order_shows_where_it_would_leave_the_holding_against_its_target():
    for needle in (
        "function orderEffect",
        "function effectText",
        "function openOrdersEffect",
        "If this fills",
        "If all ${open.length} open order",
        "below target ${pctPlain(e.target)}",
        "above target ${pctPlain(e.target)}",
        "order-effect",
    ):
        assert needle in HTML, needle
    # history rows are not projected, only open orders
    assert "o.status === 'open' ? effectText(orderEffect(o)) : ''" in HTML
