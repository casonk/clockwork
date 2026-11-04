"""Load /finances in a phone-sized browser with sample data: it renders and does not overflow."""

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

POINTS = [
    {
        "month": f"2026-{m:02d}",
        "total": 300000.0 + m * 5000,
        "cash": 20000.0,
        "total_with_cash": 320000.0 + m * 5000,
        "invest": 300000.0 + m * 5000,
        "bank": 20000.0,
        "crypt": 0.0,
        "cred": -500.0,
        "by_account": {"fid-401k": 90000.0 + m * 1000, "cs-old": 121000.0, "co-card": -500.0},
        "growth_rate": None if m == 1 else (0.06 if m % 3 == 0 else -0.06 if m % 4 == 0 else 0.01),
        "growth_signal": (
            "n/a" if m == 1 else ("green" if m % 3 == 0 else "red" if m % 4 == 0 else "yellow")
        ),
    }
    for m in range(1, 11)
]
ACCOUNTS = [
    {
        "id": "fid-401k",
        "label": "FID - Fidelity 401(k) (***401K)",
        "institution": "fid",
        "category": "invest",
        "nature": "asset",
        "group": "Investments",
        "balance": 90846.38,
        "flows": {"money_in": 309.13, "money_out": 2299.99, "interest": 0.14},
        "as_of": "2026-10-29",
        "age_days": 5,
        "stale": False,
        "counted": True,
        "possible_duplicate": False,
    },
    {
        "id": "cs-old",
        "label": "CS - Schwab One Brokerage (***6427)",
        "institution": "cs",
        "category": "invest",
        "nature": "asset",
        "group": "Investments",
        "balance": 121072.54,
        "as_of": "2026-01-31",
        "age_days": 276,
        "stale": True,
        "counted": True,
        "possible_duplicate": True,
    },
    {
        "id": "hunt-mma",
        "label": "HUNT - Huntington Relationship MMA Account (-------4990)",
        "institution": "hunt",
        "category": "bank",
        "nature": "asset",
        "group": "Cash",
        "balance": 13854.2,
        "as_of": "2026-10-19",
        "age_days": 15,
        "stale": False,
        "counted": False,
        "possible_duplicate": False,
    },
    {
        "id": "co-card",
        "label": "CO - VentureOne Credit Card | World Elite Mastercard (***5653)",
        "institution": "co",
        "category": "cred",
        "nature": "liability",
        "group": "Credit",
        "balance": -548.87,
        "as_of": "2026-03-20",
        "age_days": 228,
        "stale": True,
        "counted": True,
        "possible_duplicate": False,
    },
]
SUMMARY = {
    "as_of": "2026-10-31",
    "net_worth": 359542.61,
    "cash": 20028.77,
    "net_worth_with_cash": 379571.38,
    "groups": {
        "invest": {"label": "Investments", "balance": 360067.17, "accounts": 2},
        "bank": {"label": "Cash", "balance": 20028.77, "accounts": 1},
        "cred": {"label": "Credit", "balance": -524.56, "accounts": 1},
    },
    "change": {
        "month": {"amount": 28616.74, "percent": 8.15},
        "quarter": None,
        "year": {"amount": -4200.0, "percent": -1.1},
    },
    "stale_accounts": 2,
    "possible_duplicates": 1,
}
FRESH = {
    "sources": [
        {
            "source": "bank/hunt",
            "status": "success",
            "ended": "2026-11-02T16:12:19Z",
            "age_days": 0,
            "downloaded": 8,
            "failure": "",
        },
        {
            "source": "bank/macu",
            "status": "failed",
            "ended": "2026-11-02T16:58:46Z",
            "age_days": 0,
            "downloaded": 0,
            "failure": "post_login_challenge_not_cleared",
        },
    ]
}


SEEN: list[str] = []


def route(r):
    url = r.request.url
    SEEN.append(url)
    url = url.split("?")[0]
    if url.endswith("/summary"):
        return r.fulfill(json=SUMMARY)
    if url.endswith("/freshness"):
        return r.fulfill(json=FRESH)
    if "/networth" in url:
        body = {"points": POINTS, "thresholds": {"green": 0.05, "red": -0.05}}
        if "by=account" in SEEN[-1]:
            body["accounts"] = [
                {
                    "id": a["id"],
                    "label": a["label"],
                    "category": a["category"],
                    "balance": a["balance"],
                }
                for a in ACCOUNTS
                if a["id"] != "hunt-mma"
            ]
        return r.fulfill(json=body)
    if url.endswith("/history"):
        return r.fulfill(
            json={
                "points": [
                    {"as_of": "2026-01-31", "balance": 1.0},
                    {"as_of": "2026-02-28", "balance": 2.0},
                ]
            }
        )
    if url.endswith("/accounts"):
        return r.fulfill(json={"accounts": ACCOUNTS})
    return r.fulfill(json={})


def test_the_page_renders_on_a_phone_without_overflow_or_script_errors():
    html = web_app.app.test_client().get("/finances").get_data(as_text=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/finances/**", route)
        page.set_content(html.replace("</head>", "<base href='http://localhost/'></head>"))
        page.wait_for_selector(".hero-value")
        assert "$379,571" in page.inner_text(".hero-value")
        assert page.evaluate("document.documentElement.scrollWidth <= 390")
        assert "2 balances are older than 45 days" in page.inner_text("#notice")
        assert not page.locator("#cross").is_visible() and not page.locator("#dot").is_visible()
        assert page.locator(".acct").count() == 3  # the uncounted bank account is hidden by default
        page.click(".acct[data-id='fid-401k']")
        page.wait_for_selector("#detail-fid-401k svg.chart")
        assert "Money in $309.13" in page.inner_text(".acct-card[data-id='fid-401k']")
        page.locator("#chart").scroll_into_view_if_needed()
        box = page.locator("#chart").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + 80)
        page.wait_for_selector("#tip:not([hidden])")
        assert "Investments" in page.inner_text("#tip") and "Step" in page.inner_text("#tip")

        # trend rendering follows the pipeline's: signal-colored dots, a band, categories on request
        assert (
            page.locator("#chart .mk-green").count() >= 1
            and page.locator("#chart .mk-red").count() >= 1
        )
        assert (
            page.locator("#chart .mk-yellow").count() >= 1
            and page.locator("#chart .band").count() == 1
        )
        # every marker path is well formed and small (a string-concatenation bug once drew huge ones)
        assert page.evaluate(
            "[...document.querySelectorAll('#chart path.mk')].every(p => /^M[\\d.]+,[\\d.]+L[\\d.]+,[\\d.]+L[\\d.]+,[\\d.]+Z$/.test(p.getAttribute('d')))"
        )
        assert page.locator("#chart .cat-line").count() == 0
        legend = page.inner_text("#chart-legend")
        assert "+5.0% or more" in legend and "−5.0% or less" in legend
        page.check("#show-cats")
        assert page.locator("#chart .cat-line").count() == 4 and "Investments" in page.inner_text(
            "#chart-legend"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= 390")
        page.uncheck("#show-cats")

        # stacked views: by category, then by account
        page.click("#view button[data-v='category']")
        assert (
            page.locator("#chart path.stk").count() >= 2
            and not page.locator("#cats-switch").is_visible()
        )
        assert (
            "Investments" in page.inner_text("#chart-legend")
            and page.locator("#chart .mk").count() == 0
        )
        SEEN.clear()
        page.click("#view button[data-v='account']")
        page.wait_for_selector("#chart path.stk")
        assert any("by=account" in u for u in SEEN if "/networth" in u)
        assert page.locator("#chart path.stk").count() == 3
        assert "Each band is one account" in page.inner_text("#chart-note")
        legend = page.inner_text("#chart-legend")
        assert (
            "Fidelity 401(k)" in legend
            and "Schwab One Brokerage" in legend
            and "VentureOne" in legend
        )
        box = page.locator("#chart").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + 100)
        page.wait_for_selector("#tip:not([hidden])")
        assert (
            "Fidelity 401(k)" in page.inner_text("#tip") and not page.locator("#dot").is_visible()
        )
        assert page.evaluate("document.documentElement.scrollWidth <= 390")
        page.click("#view button[data-v='total']")
        assert page.locator("#chart .mk").count() > 0 and page.locator("#cats-switch").is_visible()
        page.uncheck("#with-cash")
        assert page.evaluate("document.documentElement.scrollWidth <= 390")

        # accounts and dates are chosen, not preset
        page.click("#filters > summary")
        assert "3 of 4 accounts" in page.inner_text(
            "#filters-summary"
        )  # the bank account is not counted here
        SEEN.clear()
        page.uncheck("#picker input[data-id='cs-old']")
        page.wait_for_timeout(500)
        summary_calls = [u for u in SEEN if "/summary" in u]
        assert summary_calls and "accounts=fid-401k%2Cco-card" in summary_calls[-1].replace(
            ",", "%2C"
        )
        assert "2 of 4 accounts" in page.inner_text("#filters-summary")
        assert page.locator(".acct").count() == 2
        assert "Selected accounts (2 of 4)" in page.inner_text("#hero")

        SEEN.clear()
        page.fill("#from", "2026-03")
        page.fill("#to", "2026-08")
        page.wait_for_timeout(500)
        last = [u for u in SEEN if "/networth" in u][-1]
        assert "from=2026-03" in last and "to=2026-08" in last
        assert "Mar 2026" in page.inner_text("#filters-summary")

        page.click("#presets button[data-p='ytd']")
        page.wait_for_timeout(500)
        assert page.input_value("#from") == "2026-01" and page.input_value("#to") == ""
        page.click("#tools button[data-act='counted']")
        page.wait_for_timeout(500)
        assert "3 of 4 accounts" in page.inner_text("#filters-summary")
        page.click("#tools button[data-act='none']")
        page.wait_for_timeout(300)
        assert "No accounts selected" in page.inner_text("#hero")
        assert page.evaluate("document.documentElement.scrollWidth <= 390")
        browser.close()
    assert errors == []


def _serve():
    import threading

    from werkzeug.serving import make_server

    srv = make_server("127.0.0.1", 0, web_app.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_axis_ticks_are_round_and_evenly_spaced_and_cards_pop_out():
    import re

    srv = _serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 800})
            page.route("**/api/finances/**", route)
            page.goto(base + "/finances")
            page.wait_for_selector(".hero-value")

            def number(label):
                m = re.fullmatch(r"(−?)\$(\d+(?:\.\d+)?)([KM]?)", label)
                assert m, label
                return (
                    float(m.group(2))
                    * {"": 1, "K": 1e3, "M": 1e6}[m.group(3)]
                    * (-1 if m.group(1) else 1)
                )

            y = [
                number(t)
                for t in page.locator("#chart text.axis-text").all_text_contents()
                if t.startswith(("$", "−$"))
            ]
            assert len(y) >= 3
            steps = {round(b - a, 2) for a, b in zip(sorted(y), sorted(y)[1:], strict=False)}
            assert len(steps) == 1 and next(iter(steps)) in {5000, 10000, 20000, 25000, 50000}, y
            x = [
                t
                for t in page.locator("#chart text.axis-text").all_text_contents()
                if not t.startswith(("$", "−$"))
            ]
            assert len(x) >= 3 and any(
                "’26" in t for t in x
            ), x  # calendar-aligned, with the year shown

            # the chart card and a card's history pop out into their own window
            with page.expect_popup() as popup:
                page.click("#pop-chart")
            popup.value.wait_for_load_state()
            assert "window=1" in popup.value.url and "card=chart" in popup.value.url
            page.click(".acct[data-id='fid-401k']")
            page.wait_for_selector("#detail-fid-401k svg.chart")
            with page.expect_popup() as popup:
                page.click("[data-pop='fid-401k']")
            popup.value.wait_for_load_state()
            assert "window=1" in popup.value.url and "account=fid-401k" in popup.value.url

            # the pop-out of the chart shows only the chart, in the view it was opened with
            win = browser.new_page(viewport={"width": 1100, "height": 800})
            win.route("**/api/finances/**", route)
            win.goto(base + "/finances?window=1&card=chart&view=category&cash=0")
            win.wait_for_selector("#chart path.stk")
            assert (
                not win.locator("#hero").is_visible() and not win.locator("#pop-chart").is_visible()
            )
            assert win.locator("#chart-card").is_visible() and "Cash" not in win.inner_text(
                "#chart-legend"
            )
            assert (
                win.evaluate("document.getElementById('chart').getBoundingClientRect().height")
                >= 320
            )

            # an account's pop-out shows its balance chart and table
            acct = browser.new_page(viewport={"width": 1100, "height": 800})
            acct.route("**/api/finances/**", route)
            acct.goto(base + "/finances?window=1&account=fid-401k")
            acct.wait_for_selector("#ap-chart svg.chart")
            assert (
                "Fidelity 401(k)" in acct.inner_text("#ap-title")
                and not acct.locator("#hero").is_visible()
            )
            assert acct.locator("#ap-table tr").count() == 3  # header and two balances
            browser.close()
    finally:
        srv.shutdown()
