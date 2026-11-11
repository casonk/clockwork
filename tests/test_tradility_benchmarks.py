"""The Tradility page's Sectors / Industries tabs and benchmark indicator, and the API fields they use."""

from __future__ import annotations

import json
from pathlib import Path

from web import app as web_app

PAGE = Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"


def test_page_has_the_group_tabs_benchmark_indicator_sorts_and_detail_card():
    html = PAGE.read_text(encoding="utf-8")
    for needle in (
        'id="tab-sectors"',
        'id="tab-industries"',
        'data-ind="bench"',
        'value="bench-signal"',
        'value="bench-behind"',
        'value="bench-ahead"',
        "benchmarkCardHtml",
        'id="grp-basis"',
        "basisIsActual",
        "incomeShare",
        'id="grp-sectorset"',
        "sectorCompareHtml",
        "rawBenchReturn",
    ):
        assert needle in html


def test_analysis_api_carries_classification_and_position_size(tmp_path, monkeypatch):
    analysis = tmp_path / "analysis.json"
    analysis.write_text(
        json.dumps(
            {
                "generated_at": "2026-11-09T00:00:00Z",
                "benchmarks": {"market": {"symbol": "VTI", "returns": {"1y": 11.0}}},
                "tickers": [
                    {
                        "ticker": "AAPL",
                        "price_history": [{"date": "2026-11-06", "close": 200}],
                        "company_profile": {
                            "available": True,
                            "sector": "Technology",
                            "industry": "Consumer Electronics",
                        },
                    }
                ],
            }
        )
    )
    holdings = tmp_path / "holdings.json"
    holdings.write_text(
        json.dumps(
            {
                "holdings": [
                    {"ticker": "AAPL", "total_quantity": 2},
                    {"ticker": "AAPL", "total_quantity": 3},
                ]
            }
        )
    )
    monkeypatch.setattr(web_app, "TRADILITY_ANALYSIS_FILE", analysis)
    monkeypatch.setattr(web_app, "HOLDINGS_AGGREGATE_FILE", holdings)
    monkeypatch.setattr(web_app, "_systemctl", lambda *a, **k: (3, "inactive"))
    web_app.app.config["TESTING"] = True

    payload = web_app.app.test_client().get("/api/tradility-analysis").get_json()

    record = payload["tickers"][0]
    assert (record["sector"], record["industry"]) == ("Technology", "Consumer Electronics")
    assert record["held_quantity"] == 5
    assert "company_profile" not in record
    assert payload["benchmarks"]["market"]["symbol"] == "VTI"
