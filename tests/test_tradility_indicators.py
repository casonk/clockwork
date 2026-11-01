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
    start = TEMPLATE.index("  function signalT(")
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
    assert "if (activeIndicators.size === 1) return;" in TEMPLATE
