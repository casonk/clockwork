"""The Tradility indicators combine into one joint buy probability when several are selected."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = (
    Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"
).read_text(encoding="utf-8")


def script(body: str) -> str:
    start = TEMPLATE.index("  // ── Indicator settings")
    end = TEMPLATE.index("  function colormapT(")
    return TEMPLATE[start:end] + "\n" + body


def run_node(body: str):
    out = subprocess.run(["node", "-e", script(body)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

TICKER = {"rsi_14": 30, "last_price": 100, "vwap_20": 100, "long_term_trend": "bullish"}


@needs_node
def test_one_indicator_reads_as_before_and_several_reinforce_or_cancel():
    r = run_node(f"""
        const t = {json.dumps(TICKER)};
        const one = jointT(t, ['rsi']).t;               // RSI 30 -> 0.70 buy
        const two = jointT(t, ['rsi', 'trend']).t;      // 0.70 and 0.85 agree
        const neutral = jointT(t, ['rsi', 'vwap']).t;   // vwap at 0.5 changes nothing
        const opposed = jointT({{...t, rsi_14: 80, long_term_trend: 'bullish'}}, ['rsi', 'trend']).t;
        console.log(JSON.stringify({{one, two, neutral, opposed}}));
        """)
    assert r["one"] == pytest.approx(0.70)
    assert r["two"] > 0.9 > r["one"]
    assert r["neutral"] == pytest.approx(r["one"])
    assert 0.3 < r["opposed"] < 0.7


@needs_node
def test_missing_signals_are_left_out_and_counted_and_extremes_are_held_off():
    r = run_node("""
        const bare = {rsi_14: 0, last_price: null, vwap_20: null, long_term_trend: null};
        const j = jointT(bare, ['rsi', 'vwap', 'trend']);
        console.log(JSON.stringify({j, none: jointT({fetch_status: 'error'}, ['rsi'])}));
        """)
    assert r["j"]["used"] == 1 and r["j"]["t"] < 1.0  # RSI 0 is held off certainty
    assert r["none"] is None


def test_the_indicator_buttons_toggle_and_keep_at_least_one():
    assert "toggleIndicator('rsi')" in TEMPLATE and "setIndicator(" not in TEMPLATE
    assert 'aria-pressed="true"' in TEMPLATE
    assert "activeIndicators.size === 1 ? 'weak' : 'off'" in TEMPLATE


def test_the_detail_dialog_steps_through_the_cards_in_grid_order():
    for needle in (
        'id="detail-prev"',
        'id="detail-next"',
        "stepTickerDetail(-1)",
        "stepTickerDetail(1)",
        "shownOrder = tickers.filter(t => t.detail_available)",
        "ArrowLeft",
        "if (detailTicker !== ticker) return;",
    ):
        assert needle in TEMPLATE, needle


def test_the_detail_view_links_to_the_company_site_and_to_yahoo_finance():
    assert "https://finance.yahoo.com/quote/${encodeURIComponent(ticker)}/" in TEMPLATE
    assert "Yahoo Finance ↗" in TEMPLATE and "Company website ↗" in TEMPLATE
    start = TEMPLATE.index('<div class="company-links">')
    assert (
        "No qualitative profile" in TEMPLATE[:start]
    )  # the links sit outside the profile-only block
    assert 'rel="noopener noreferrer"' in TEMPLATE[start : start + 700]


def test_the_chart_range_is_shared_by_every_card_and_remembered():
    assert (
        "detailRange = '5y';\n    const overlay" not in TEMPLATE
    )  # opening a card no longer resets it
    assert "const RANGE_KEY = 'tradility-detail-range';" in TEMPLATE
    assert "localStorage.setItem(RANGE_KEY, range)" in TEMPLATE
    assert "if (!RANGES.includes(range)) return;" in TEMPLATE


@needs_node
def test_fundamentals_read_as_a_buy_probability_and_join_the_others():
    r = run_node(f"""
        const base = {json.dumps(TICKER)};
        const strong = {{...base, fundamental_signal_count: 8, fundamental_score: 8, fundamental_rating: 'strong_buy'}};
        const weak = {{...base, fundamental_signal_count: 8, fundamental_score: -8, fundamental_rating: 'strong_sell'}};
        const none = {{...base, fundamental_rating: 'unavailable', fundamental_signal_count: 3, fundamental_score: 3}};
        console.log(JSON.stringify({{
          strong: signalT(strong, 'fund'), weak: signalT(weak, 'fund'), none: signalT(none, 'fund'),
          missing: signalT(base, 'fund'), joint: jointT(strong, ['trend', 'fund']).t,
        }}));
        """)
    assert r["strong"] == pytest.approx(0.9) and r["weak"] == pytest.approx(0.1)
    assert r["none"] is None and r["missing"] is None
    assert r["joint"] > 0.9


def test_the_fundamentals_card_and_button_are_in_the_page():
    assert 'data-ind="fund"' in TEMPLATE and "Fundamentals · Schwab" in TEMPLATE
    assert "${fundamentalsCardHtml(t)}" in TEMPLATE


@needs_node
def test_the_fundamentals_card_renders_signals_figures_and_fund_notes():
    start = TEMPLATE.index("  const FUND_LABELS = [")
    end = TEMPLATE.index("  function setDetailRange(")
    code = (
        "const esc = s => String(s); const ratingLabel = v => v; const ratingClass = v => 'c-' + v;\n"
        + TEMPLATE[start:end]
    )
    t = {
        "fundamentals": {"pe": 12.0, "roe": 22.0, "market_cap": 5.03e12, "beta": 1.1},
        "fundamental_signals": [
            {"key": "pe", "label": "Valuation (P/E)", "value": 12.0, "signal": "buy", "rule": "r"}
        ],
        "fundamental_rating": "buy",
        "fundamental_score": 1,
        "fundamental_signal_count": 4,
    }
    fund = {"fundamentals": {"beta": 1.0}}
    render = (
        "console.log(JSON.stringify(["
        f"fundamentalsCardHtml({json.dumps(t)}), "
        f"fundamentalsCardHtml({json.dumps(fund)}), "
        "fundamentalsCardHtml({})]));"
    )
    out = subprocess.run(["node", "-e", code + render], capture_output=True, text=True, check=True)
    company, etf, empty = json.loads(out.stdout)
    assert "12.00x · buy" in company and "$5.03T" in company and "22.00%" in company
    assert "+1 / 4" in company and "c-buy" in company
    assert "no buy or sell rules" in etf and empty == ""


@needs_node
def test_a_weak_indicator_counts_half_and_a_strong_one_double_but_only_when_decisive():
    r = run_node(f"""
        const t = {json.dumps(TICKER)};   // RSI 30 -> 0.70, decisive (<= 30)
        const mid = {{...t, rsi_14: 45}};  // 0.55, not decisive
        const at = (x, lvl) => jointT(x, ['rsi'], {{rsi: lvl}}).t;
        console.log(JSON.stringify({{
          weak: at(t, 'weak'), normal: at(t, 'normal'), strong: at(t, 'strong'),
          midNormal: at(mid, 'normal'), midStrong: at(mid, 'strong'),
          none: jointT(t, ['rsi']).t,
        }}));
        """)
    assert r["normal"] == pytest.approx(0.70) and r["none"] == pytest.approx(0.70)
    assert 0.5 < r["weak"] < r["normal"] < r["strong"]
    assert r["strong"] == pytest.approx(0.49 / (0.49 + 0.09))  # 0.7^2 against 0.3^2
    assert r["midNormal"] == pytest.approx(0.55) and r["midStrong"] == pytest.approx(0.5)


@needs_node
def test_strength_changes_how_far_one_indicator_pulls_against_another():
    r = run_node("""
        const t = {rsi_14: 30, last_price: 100, vwap_20: 90, long_term_trend: 'bullish', sma_50: 110, sma_200: 100};
        // RSI says buy (0.70); VWAP 11% above says sell; trend says buy
        const base = jointT(t, ['rsi', 'vwap']).t;
        const vwapStrong = jointT(t, ['rsi', 'vwap'], {vwap: 'strong'}).t;
        const vwapWeak = jointT(t, ['rsi', 'vwap'], {vwap: 'weak'}).t;
        console.log(JSON.stringify({base, vwapStrong, vwapWeak}));
        """)
    assert r["vwapStrong"] < r["base"] < r["vwapWeak"]


def test_indicator_buttons_cycle_and_show_their_strength():
    assert 'class="ind-tag"' in TEMPLATE and ".ind-btn.lvl-strong" in TEMPLATE
    assert "off → weak → normal → strong → off" in TEMPLATE


@needs_node
def test_the_button_cycle_order_and_the_last_indicator_never_turns_off():
    start = TEMPLATE.index("  const LEVEL_TAG =")
    end = TEMPLATE.index("  function paintIndicatorButtons(")
    code = (
        "const LEVELS = ['weak', 'normal', 'strong'];\n"
        "let activeIndicators = new Set(['rsi', 'vwap']); let indicatorLevels = {};\n"
        + TEMPLATE[start:end]
        + """
        const seq = [];
        indicatorLevels = {rsi: 'weak'};
        for (const lvl of ['weak', 'normal', 'strong']) { indicatorLevels.rsi = lvl; seq.push(nextIndicatorState('rsi')); }
        seq.push(nextIndicatorState('fund'));
        activeIndicators = new Set(['rsi']); indicatorLevels = {rsi: 'strong'};
        seq.push(nextIndicatorState('rsi'));
        console.log(JSON.stringify(seq));
        """
    )
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == ["normal", "strong", "off", "weak", "weak"]


@needs_node
def test_settings_choose_which_window_each_indicator_reads():
    r = run_node("""
        const t = {last_price: 102, rsi_14: 40, vwap_20: 100, sma_50: 110, sma_200: 100, long_term_trend: 'bullish',
          indicator_variants: {rsi: {7: 20, 14: 40, 21: 55}, vwap: {10: 98, 20: 100, 50: 90},
                               sma: {20: 95, 50: 110, 100: 105, 200: 100, 300: 90}}};
        const out = {};
        out.rsi14 = signalT(t, 'rsi');
        settings.rsi.period = 7;  out.rsi7 = signalT(t, 'rsi');
        settings.vwap.window = 50; out.vwap50 = signalT(t, 'vwap');
        out.trend50_200 = signalT(t, 'trend');
        settings.trend.short = 20; settings.trend.long = 100; out.trend20_100 = signalT(t, 'trend');
        console.log(JSON.stringify(out));
        """)
    assert r["rsi14"] == pytest.approx(0.6) and r["rsi7"] == pytest.approx(0.8)
    assert r["vwap50"] < 0.5  # price is above the 50-day VWAP
    assert r["trend50_200"] == 0.85  # 50 > 200 and price above 200
    assert r["trend20_100"] == 0.15  # 20-day below 100-day and price below it too: bearish


@needs_node
def test_a_strong_indicator_uses_the_chosen_bands_and_gaps():
    r = run_node("""
        const t = {last_price: 101, rsi_14: 38, vwap_20: 100, sma_50: 103, sma_200: 100, long_term_trend: 'bullish'};
        const at = (ind) => jointT(t, [ind], {[ind]: 'strong'}).t;
        const out = {};
        out.rsiDefault = at('rsi');                       // 38 is not past the 30 band
        settings.rsi.buy = 40; out.rsiWide = at('rsi');   // oversold at or below 40
        out.trendDefault = at('trend');                   // 3% gap < 5%
        settings.trend.gap = 2; out.trendWide = at('trend');
        console.log(JSON.stringify(out));
        """)
    assert r["rsiDefault"] == pytest.approx(0.5) and r["rsiWide"] > 0.5
    assert r["trendDefault"] == pytest.approx(0.5) and r["trendWide"] > 0.5


@needs_node
def test_fundamental_groups_pick_which_rules_count():
    r = run_node("""
        const rules = [
          {key: 'pe', signal: 'buy', score: 1}, {key: 'peg', signal: 'buy', score: 1},
          {key: 'roe', signal: 'sell', score: -1}, {key: 'net_margin', signal: 'sell', score: -1},
          {key: 'rev_growth', signal: 'buy', score: 1}, {key: 'current_ratio', signal: 'neutral', score: 0}];
        const t = {fundamental_signals: rules, fundamental_rating: 'hold'};
        const out = {all: fundRead(t)};
        settings.fund.groups = ['valuation'];
        out.valuation = fundRead(t);
        settings.fund.groups = ['short'];
        out.none = fundRead(t);
        console.log(JSON.stringify(out));
        """)
    assert r["all"] == {"score": 1, "n": 6} and r["valuation"] == {"score": 2, "n": 2}
    assert r["none"] is None


@needs_node
def test_price_ticks_are_round_and_date_ticks_follow_the_calendar():
    start = TEMPLATE.index("  const NICE_STEPS")
    end = TEMPLATE.index("  function drawDetailChart()")
    code = TEMPLATE[start:end] + """
        const day = d => Date.parse(d + 'T00:00:00Z');
        const out = {
          price: niceTicks(96.81, 143.09, 5),
          narrow: niceTicks(330000, 372000, 4),
          penny: niceTicks(0.0412, 0.0458, 4),
          year: timeAxis(day('2025-01-01'), day('2026-02-04'), 600).map(t => t.label),
          fiveYears: timeAxis(day('2021-02-01'), day('2026-02-01'), 600).map(t => t.label),
          month: timeAxis(day('2026-01-05'), day('2026-02-04'), 600).map(t => t.label),
          threeMonths: timeAxis(day('2025-11-04'), day('2026-02-04'), 600).map(t => t.label),
        };
        console.log(JSON.stringify(out));
        """
    r = json.loads(
        subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    )
    for key in ("price", "narrow", "penny"):
        ticks = r[key]
        gaps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:], strict=False)}
        assert len(ticks) >= 3 and len(gaps) == 1, (key, ticks)
    assert r["price"] == [100, 110, 120, 130, 140] and r["narrow"][0] % 5000 == 0
    assert (
        r["year"][0] == "Jan ’25" and r["year"][-1] == "Jan ’26"
    )  # month ticks, with the year on the first and each January
    assert (
        all(x.isdigit() for x in r["fiveYears"]) and len(r["fiveYears"]) >= 3
    )  # years on a long range
    assert all(
        " " in label and label.split()[1].isdigit() for label in r["month"]
    )  # "Jan 12": days on a short range
    assert len(r["threeMonths"]) >= 3
