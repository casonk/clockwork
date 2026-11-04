"""Load the Tradility page in a real browser with sample data and drive the indicator settings."""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from web import app as web_app  # noqa: E402

CHROMIUM = (
    shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("google-chrome")
)
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium for a browser test")


def record(ticker: str, rsi14: float, rsi7: float) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "asset_class": "Equity",
        "fetch_status": "ok",
        "last_price": 100.0,
        "rsi_14": rsi14,
        "vwap_20": 100.0,
        "sma_50": 110.0,
        "sma_200": 100.0,
        "long_term_trend": "bullish",
        "detail_available": True,
        "indicator_variants": {
            "rsi": {"7": rsi7, "14": rsi14, "21": 50.0},
            "vwap": {"10": 99.0, "20": 100.0, "50": 90.0},
            "sma": {"20": 105.0, "50": 110.0, "100": 95.0, "200": 100.0, "300": 90.0},
        },
    }


DATA = {
    "ok": True,
    "generated_at": "2026-11-02T00:00:00Z",
    "tickers": [record("AAA", 28.0, 15.0), record("BBB", 60.0, 80.0)],
    "freshness": {"stale": False},
    "refresh": {"active": False},
    "csrf_token": "x",
    "artifact_version": "1",
}


def test_settings_panel_changes_what_the_cards_read_without_script_errors():
    html = web_app.app.test_client().get("/to-tradility").get_data(as_text=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route(
            "**/api/**",
            lambda r: r.fulfill(
                json=DATA if "tradility-analysis" in r.request.url else {"ok": False}
            ),
        )
        page.set_content(html.replace("</head>", "<base href='http://localhost/'></head>"))
        page.wait_for_selector(".ticker-card")

        def badges() -> dict[str, str]:
            return page.evaluate(
                "Object.fromEntries([...document.querySelectorAll('.ticker-card')]"
                ".map(c => [c.dataset.ticker, c.querySelector('.signal-badge').textContent]))"
            )

        assert badges() == {"AAA": "Oversold", "BBB": "Neutral"}
        page.click("#ind-settings > summary")
        page.select_option("#ind-settings-body select >> nth=0", "7")  # RSI period
        assert page.inner_text(".ind-btn[data-ind=rsi] .ind-name") == "RSI 7"
        assert badges() == {"AAA": "Oversold", "BBB": "Overbought"}
        page.click("#ind-settings-body .ind-set-reset")
        assert page.inner_text(".ind-btn[data-ind=rsi] .ind-name") == "RSI 14"
        browser.close()
    assert errors == []


def test_accounts_panel_links_to_finances_for_net_worth_and_cash():
    html = web_app.app.test_client().get("/to-tradility").get_data(as_text=True)
    accounts = {
        "ok": True,
        "generated_at": "2026-11-02T00:00:00Z",
        "sources": {},
        "accounts": [],
        "statements": [],
    }
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.on("pageerror", lambda e: errors.append(str(e)))

        def respond(route):
            url = route.request.url
            if "tradility-analysis" in url:
                route.fulfill(json=DATA)
            elif "tradility-accounts" in url:
                route.fulfill(json=accounts)
            elif "finances/summary" in url:
                route.fulfill(json={"net_worth_with_cash": 379800.05, "cash": 20921.32})
            else:
                route.fulfill(json={"ok": False})

        page.route("**/api/**", respond)
        page.set_content(html.replace("</head>", "<base href='http://localhost/'></head>"))
        page.wait_for_selector(".ticker-card")
        page.click("#accounts-panel > summary")
        page.wait_for_selector("#accounts-body .account-card")
        text = page.inner_text("#accounts-panel")
        browser.close()
    assert "$379,800.05" in text and "$20,921.32" in text
    assert "Huntington" not in text and "Finances" in text
    assert errors == []


def _history(n: int = 400) -> list[dict]:
    from datetime import date, timedelta

    start = date(2025, 1, 1)
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "close": 100 + i * 0.1} for i in range(n)
    ]


def _detail_route(route):
    url = route.request.url
    if "/api/tradility-analysis/AAA" in url:
        route.fulfill(
            json={"ok": True, "ticker": {**record("AAA", 28.0, 15.0), "price_history": _history()}}
        )
    elif "tradility-analysis" in url:
        route.fulfill(json=DATA)
    else:
        route.fulfill(json={"ok": False})


def test_detail_and_chart_offer_full_screen_and_pop_out_as_separate_options():
    import threading

    from werkzeug.serving import make_server

    srv = make_server("127.0.0.1", 0, web_app.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/api/**", _detail_route)
            page.goto(base + "/to-tradility")
            page.wait_for_selector(".ticker-card")
            page.click(".ticker-card[data-ticker='AAA']")
            page.wait_for_selector("#detail-chart-card")
            windows = len(page.context.pages)

            def top() -> float:
                return page.evaluate(
                    "document.getElementById('detail-overlay').getBoundingClientRect().top"
                )

            # the whole detail: full screen covers the top bar, Esc leaves it but keeps the detail open
            assert top() > 0
            page.click("#detail-fs")
            page.wait_for_function("document.body.classList.contains('detail-fs')")
            assert top() == 0 and len(page.context.pages) == windows
            page.keyboard.press("Escape")
            page.wait_for_function("!document.body.classList.contains('detail-fs')")
            assert not page.locator("#detail-overlay").is_hidden()

            # the chart: full screen is in place and bigger; Esc leaves it, the detail stays
            page.click("#detail-chart-card .chart-open[aria-label='Full screen chart']")
            page.wait_for_selector("#detail-chart-card.chart-fs")
            assert len(page.context.pages) == windows
            assert (
                page.evaluate(
                    "document.getElementById('detail-chart').getBoundingClientRect().height"
                )
                >= 260
            )
            assert page.locator("#detail-chart-card .chart-fs-close").is_visible()
            page.keyboard.press("Escape")
            page.wait_for_function(
                "!document.getElementById('detail-chart-card').classList.contains('chart-fs')"
            )
            assert not page.locator("#detail-overlay").is_hidden()

            # pop out is the one that opens a window
            with page.expect_popup() as popup:
                page.click(
                    "#detail-chart-card .chart-open[aria-label='Open the chart in its own window']"
                )
            popup.value.wait_for_load_state()
            assert (
                "window=1" in popup.value.url
                and "card=chart" in popup.value.url
                and "ticker=AAA" in popup.value.url
            )

            # that window shows only the chart
            win = browser.new_page(viewport={"width": 1100, "height": 800})
            win.on("pageerror", lambda e: errors.append(str(e)))
            win.route("**/api/**", _detail_route)
            win.goto(base + "/to-tradility?ticker=AAA&window=1&card=chart&range=1y")
            win.wait_for_selector("#detail-chart-card")
            assert (
                win.locator("#detail-chart-card").is_visible()
                and not win.locator("#detail-orders-card").is_visible()
            )
            assert not win.locator(".chart-actions").first.is_visible()
            assert win.locator(".detail-range-btn.active").inner_text() == "1Y"
            browser.close()
    finally:
        srv.shutdown()
    assert errors == []
