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
    assert "o.status === 'open' ? effectCell(o) : ''" in HTML


def test_the_numbers_are_backed_by_accessible_share_bars_on_cards_orders_the_target_card_and_the_preview():
    for needle in (
        "function shareBar",
        "function barLegend",
        'role="img"',
        "aria-label=",
        "sbar-target",
        "sbar-move",
        "shareBar(r.current || 0, null, r.target)",
        "function effectCell",
        "shareBar(currentShare(), e.after, e.target)",
        "{big: true}",
        "shareBar(p.before, p.after, null, {max: shareMax})",
        "shareBar(r.percent, r.chosen ? r.percent_after : null, null, {max: barMax})",
    ):
        assert needle in HTML, needle
    # theme tokens only: no literal colours inside the bar styles
    css = HTML[HTML.index(".sbar {") : HTML.index(".card-target {")]
    assert "#" not in css.replace("/* ", "").split("*/")[-1]
    assert "var(--accent)" in css and "var(--text)" in css


def test_the_detail_view_fills_the_window_below_the_top_bar_and_can_open_in_its_own_window():
    for needle in (
        "top: var(--detail-top, 0px)",
        "z-index: 250",
        "function placeDetailOverlay",
        "--detail-top",
        "function popOutDetail",
        "window.open(url",
        "resizable=yes",
        "window=1",
        "body.popout",
        'id="detail-popout"',
        "if (IS_POPOUT) { window.close(); return; }",
        "display: block; position: relative",
    ):
        assert needle in HTML, needle
    overlay = HTML[HTML.index(".detail-overlay {") : HTML.index(".detail-overlay[hidden]")]
    assert "inset: 0" not in overlay  # no longer covers the top bar
    assert "width: min(980px" not in HTML  # no longer a 980px card


def test_the_target_bar_is_redrawn_once_the_open_orders_are_known():
    assert "function refreshTargetCard" in HTML
    assert HTML.index("refreshTargetCard();\n    await loadTradeStatus();") > HTML.index(
        "detailOrders = data;"
    )


def test_setting_a_target_from_tradility_rebalances_and_rolls_up_like_to_invest(client, list_files):
    list_files["INVEST_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Stocks",
                        "items": [
                            {"ticker": "AAA", "holding": True, "target_pct": "40"},
                            {"ticker": "BBB", "holding": True, "target_pct": "40"},
                            {"ticker": "CCC", "holding": True, "target_pct": "20"},
                        ],
                    },
                    {"name": "Sectors", "items": [{"ticker": "TECHNOLOGY"}, {"ticker": "ENERGY"}]},
                ]
            }
        )
    )
    list_files["HOLDINGS_AGGREGATE_FILE"].write_text(
        json.dumps({"holdings": [{"ticker": "AAA", "total_ending_value": 100}]})
    )
    list_files["INVEST_CLASSIFICATIONS_FILE"].write_text(
        "symbol,cusip,sector,industry,sub_industry\n"
        "AAA,1,Technology,Software,\nBBB,2,Technology,Software,\nCCC,3,Energy,Oil & Gas,\n"
    )
    with client.session_transaction() as session:
        session["_csrf_token"] = "tok"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "tok"

    reply = client.post("/api/tradility-targets/AAA", json={"target_pct": "60"})
    assert reply.status_code == 200 and reply.get_json()["target"]["target"] == 60

    cards = {
        c["name"]: {i["ticker"]: float(i.get("target_pct") or 0) for i in c["items"]}
        for c in json.loads(list_files["INVEST_LIST_FILE"].read_text())["categories"]
    }
    assert cards["Stocks"]["AAA"] == 60
    assert round(sum(cards["Stocks"].values()), 3) == 100  # the others rescaled around it
    assert abs(cards["Stocks"]["BBB"] - 2 * cards["Stocks"]["CCC"]) < 1e-3  # still 40:20
    assert (
        abs(cards["Sectors"]["TECHNOLOGY"] - cards["Stocks"]["AAA"] - cards["Stocks"]["BBB"]) < 1e-3
    )
    assert round(cards["Sectors"]["TECHNOLOGY"] + cards["Sectors"]["ENERGY"], 3) == 100


def test_a_tradility_target_that_cannot_be_placed_is_refused_without_saving(client, list_files):
    setup(list_files, client)
    before = list_files["INVEST_LIST_FILE"].read_text()
    reply = client.post(
        "/api/tradility-targets/NEWCO", json={"target_pct": "5"}
    )  # no category chosen
    assert reply.status_code == 400
    assert list_files["INVEST_LIST_FILE"].read_text() == before


def test_the_target_card_links_to_its_own_row_in_to_invest():
    assert "/to-invest#invest-item-${encodeURIComponent(r.ticker || ticker)}" in HTML
