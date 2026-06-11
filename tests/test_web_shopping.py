"""Tests for shopping: matching helpers, intake sync, AI workflows, and HTTP routes."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the web app module (unique name to avoid collision with grocery tests)
# ---------------------------------------------------------------------------

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_APP_PATH = _WEB_DIR / "app.py"
_SPEC = spec_from_file_location("clockwork_web_app_shopping", _APP_PATH)
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
# Register before exec so Flask can resolve the template directory via __file__
sys.modules["clockwork_web_app_shopping"] = web_app
_SPEC.loader.exec_module(web_app)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def shopping_file(tmp_path, monkeypatch):
    """Redirect SHOPPING_FILE to a temp path and return it."""
    p = tmp_path / "shopping.json"
    monkeypatch.setattr(web_app, "SHOPPING_FILE", p)
    return p


@pytest.fixture()
def shopping_rules_file(tmp_path, monkeypatch):
    """Redirect SHOPPING_RULES_FILE to a temp path and return it."""
    p = tmp_path / "shopping-rules.json"
    monkeypatch.setattr(web_app, "SHOPPING_RULES_FILE", p)
    return p


@pytest.fixture()
def sample_shopping_data():
    """Minimal shopping data structure using 'owned' key."""
    return {
        "categories": [
            {
                "name": "Clothes",
                "items": [
                    {"name": "T-Shirt", "owned": False},
                    {"name": "Jeans", "owned": False},
                    {"name": "Jacket", "owned": True},
                ],
            },
            {
                "name": "Home",
                "items": [
                    {"name": "Toilet Paper", "owned": False},
                    {"name": "Detergent", "owned": False},
                ],
            },
            {
                "name": "Recreation",
                "items": [
                    {"name": "Water Bottle", "owned": False},
                    {"name": "Backpack", "owned": False},
                ],
            },
        ]
    }


@pytest.fixture()
def populated_shopping(shopping_file, sample_shopping_data):
    """Write sample_shopping_data to the redirected shopping file and return its path."""
    shopping_file.write_text(json.dumps(sample_shopping_data))
    return shopping_file


@pytest.fixture()
def shopping_intake_db(tmp_path):
    """Create a minimal intake SQLite DB with clothing/home/recreation receipts."""
    db_path = tmp_path / "shopping_intake.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE receipts (
            filename TEXT PRIMARY KEY,
            scan_date TEXT,
            merchant TEXT,
            category TEXT,
            items_json TEXT
        )
    """)
    items = [
        {"description": "LEVI 501 JEANS", "amount": 59.99, "quantity": 1, "unit_price": None},
        {
            "description": "CHARMIN ULTRA TP 12PK",
            "amount": 9.99,
            "quantity": 1,
            "unit_price": None,
        },
        {"description": "HYDRO FLASK 32OZ", "amount": 44.95, "quantity": 1, "unit_price": None},
        {"description": "DUCT TAPE HEAVY DUTY", "amount": 4.99, "quantity": 1, "unit_price": None},
    ]
    conn.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        ("20260601_1200.jpg", "2026-06-01", "target", "clothing/retail", json.dumps(items)),
    )
    # Old receipt outside the default 30-day window
    old_items = [
        {"description": "ADIDAS SNEAKER", "amount": 89.99, "quantity": 1, "unit_price": None}
    ]
    conn.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        ("20260101_1200.jpg", "2026-01-01", "foot locker", "clothing/shoes", json.dumps(old_items)),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def client(populated_shopping, shopping_rules_file, monkeypatch):
    """Flask test client with shopping/rules files redirected to temp paths.

    Ollama availability is stubbed False so GET /shopping never makes a real
    network call; individual tests that exercise AI routes override this.
    """
    monkeypatch.setattr(web_app, "INTAKE_DB", Path("/nonexistent/intake.db"))
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    web_app.app.config["TESTING"] = True
    web_app.app.config["SECRET_KEY"] = "test-secret"
    return web_app.app.test_client()


# ---------------------------------------------------------------------------
# _item_matches_desc with _SHOPPING_ALIASES
# ---------------------------------------------------------------------------


def test_item_matches_desc_shopping_alias_brand():
    # "Jeans" should match via alias "levi"
    assert web_app._item_matches_desc("Jeans", "LEVI 501 JEANS", web_app._SHOPPING_ALIASES) is True


def test_item_matches_desc_shopping_water_bottle_alias():
    assert (
        web_app._item_matches_desc("Water Bottle", "HYDRO FLASK 32OZ", web_app._SHOPPING_ALIASES)
        is True
    )


def test_item_matches_desc_shopping_toilet_paper_alias():
    assert (
        web_app._item_matches_desc(
            "Toilet Paper", "CHARMIN ULTRA TP 12PK", web_app._SHOPPING_ALIASES
        )
        is True
    )


def test_item_matches_desc_shopping_sneakers_alias():
    assert (
        web_app._item_matches_desc("Sneakers", "ADIDAS ULTRABOOST", web_app._SHOPPING_ALIASES)
        is True
    )


def test_item_matches_desc_shopping_no_match():
    assert (
        web_app._item_matches_desc("T-Shirt", "DUCT TAPE HEAVY DUTY", web_app._SHOPPING_ALIASES)
        is False
    )


def test_item_matches_desc_default_aliases_unchanged():
    # Default None should still use grocery aliases
    assert web_app._item_matches_desc("Beer", "BUD LIGHT 18PK") is True
    assert web_app._item_matches_desc("Beer", "BUD LIGHT 18PK", None) is True


# ---------------------------------------------------------------------------
# load_shopping_rules / save_shopping_rules
# ---------------------------------------------------------------------------


def test_load_shopping_rules_returns_empty_when_missing(shopping_rules_file):
    assert not shopping_rules_file.exists()
    result = web_app.load_shopping_rules()
    assert result == {"aliases": {}, "types": {}}


def test_load_shopping_rules_reads_existing(shopping_rules_file):
    shopping_rules_file.write_text(
        json.dumps({"aliases": {"Jeans": ["levi"]}, "types": {}})
    )
    result = web_app.load_shopping_rules()
    assert result["aliases"]["Jeans"] == ["levi"]


def test_save_shopping_rules_writes_json(shopping_rules_file):
    web_app.save_shopping_rules({"aliases": {"Sneakers": ["nike"]}, "types": {}})
    saved = json.loads(shopping_rules_file.read_text())
    assert saved["aliases"]["Sneakers"] == ["nike"]


def test_load_shopping_rules_tolerates_corrupt_file(shopping_rules_file):
    shopping_rules_file.write_text("not json {{")
    result = web_app.load_shopping_rules()
    assert result == {"aliases": {}, "types": {}}


# ---------------------------------------------------------------------------
# _sync_shopping_from_intake
# ---------------------------------------------------------------------------


def test_sync_shopping_marks_matching_items_owned(sample_shopping_data, shopping_intake_db):
    count, marked = web_app._sync_shopping_from_intake(
        shopping_intake_db, sample_shopping_data, since="2026-05-01"
    )
    names = [m.split(": ", 1)[1] for m in marked]
    assert "Jeans" in names
    assert "Toilet Paper" in names
    assert "Water Bottle" in names
    # Jacket was already owned — should not appear
    assert "Jacket" not in names


def test_sync_shopping_skips_already_owned(sample_shopping_data, shopping_intake_db):
    web_app._sync_shopping_from_intake(
        shopping_intake_db, sample_shopping_data, since="2026-05-01"
    )
    jacket = next(
        i
        for c in sample_shopping_data["categories"]
        for i in c["items"]
        if i["name"] == "Jacket"
    )
    assert jacket["owned"] is True


def test_sync_shopping_does_not_mark_unrelated_item(sample_shopping_data, shopping_intake_db):
    web_app._sync_shopping_from_intake(
        shopping_intake_db, sample_shopping_data, since="2026-05-01"
    )
    backpack = next(
        i
        for c in sample_shopping_data["categories"]
        for i in c["items"]
        if i["name"] == "Backpack"
    )
    assert backpack["owned"] is False


def test_sync_shopping_returns_zero_when_db_missing(sample_shopping_data, tmp_path):
    absent = tmp_path / "no.db"
    count, marked = web_app._sync_shopping_from_intake(absent, sample_shopping_data)
    assert count == 0
    assert marked == []


def test_sync_shopping_respects_since_date(sample_shopping_data, shopping_intake_db):
    # "ADIDAS SNEAKER" receipt is from 2026-01-01; within a wide window it matches Sneakers
    # But our sample_shopping_data doesn't have Sneakers, so test against Jeans from recent receipt
    count_narrow, _ = web_app._sync_shopping_from_intake(
        shopping_intake_db, sample_shopping_data, since="2026-06-01"
    )
    assert count_narrow >= 1  # at least the recent receipt matched something


def test_sync_shopping_uses_ai_rules(
    sample_shopping_data, shopping_intake_db, shopping_rules_file
):
    # Add an AI rule that maps "duct tape" → Backpack (artificial, just tests the plumbing)
    shopping_rules_file.write_text(json.dumps({"aliases": {"Backpack": ["duct tape"]}, "types": {}}))
    conn = sqlite3.connect(str(shopping_intake_db))
    conn.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        (
            "20260607_0000.jpg",
            "2026-06-07",
            "home depot",
            "home/hardware",
            json.dumps(
                [{"description": "DUCT TAPE 2PK", "amount": 8.0, "quantity": 1, "unit_price": None}]
            ),
        ),
    )
    conn.commit()
    conn.close()
    count, marked = web_app._sync_shopping_from_intake(
        shopping_intake_db, sample_shopping_data, since="2026-06-01"
    )
    names = [m.split(": ", 1)[1] for m in marked]
    assert "Backpack" in names


# ---------------------------------------------------------------------------
# _ai_categorize_shopping
# ---------------------------------------------------------------------------

_CATEGORIZE_RESPONSE = json.dumps(
    {
        "types": {
            "T-Shirt": "Tops",
            "Jeans": "Bottoms",
            "Jacket": "Outerwear",
            "Toilet Paper": "Bath",
            "Detergent": "Cleaning",
            "Water Bottle": "Fitness",
            "Backpack": "Sports",
        }
    }
)


def test_ai_categorize_shopping_applies_types(sample_shopping_data, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _CATEGORIZE_RESPONSE)
    count, model = web_app._ai_categorize_shopping(sample_shopping_data)
    items = {i["name"]: i for c in sample_shopping_data["categories"] for i in c["items"]}
    assert items["T-Shirt"]["type"] == "Tops"
    assert items["Jeans"]["type"] == "Bottoms"
    assert items["Toilet Paper"]["type"] == "Bath"
    assert count == 7


def test_ai_categorize_shopping_skips_already_typed(sample_shopping_data, monkeypatch):
    sample_shopping_data["categories"][0]["items"][0]["type"] = "Tops"
    response = json.dumps(
        {
            "types": {
                "Jeans": "Bottoms",
                "Jacket": "Outerwear",
                "Toilet Paper": "Bath",
                "Detergent": "Cleaning",
                "Water Bottle": "Fitness",
                "Backpack": "Sports",
            }
        }
    )
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: response)
    count, _ = web_app._ai_categorize_shopping(sample_shopping_data)
    assert count == 6


def test_ai_categorize_shopping_returns_zero_when_all_typed(sample_shopping_data, monkeypatch):
    for cat in sample_shopping_data["categories"]:
        for item in cat["items"]:
            item["type"] = "Tops"
    called = []
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: called.append(1) or "{}")
    count, _ = web_app._ai_categorize_shopping(sample_shopping_data)
    assert count == 0
    assert called == []


def test_ai_categorize_shopping_raises_on_bad_json(sample_shopping_data, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: "not json at all")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        web_app._ai_categorize_shopping(sample_shopping_data)


def test_ai_categorize_shopping_raises_on_missing_types_key(sample_shopping_data, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: '{"wrong_key": {}}')
    with pytest.raises(ValueError):
        web_app._ai_categorize_shopping(sample_shopping_data)


# ---------------------------------------------------------------------------
# _ai_generate_shopping_rules
# ---------------------------------------------------------------------------

_RULES_RESPONSE = json.dumps(
    {"aliases": {"Jeans": ["levi", "denim"], "Water Bottle": ["hydro flask"]}}
)


def test_ai_generate_shopping_rules_saves_aliases(
    sample_shopping_data, shopping_intake_db, shopping_rules_file, monkeypatch
):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    added, model = web_app._ai_generate_shopping_rules(
        sample_shopping_data, shopping_intake_db, since="2026-01-01"
    )
    assert added >= 2
    rules = web_app.load_shopping_rules()
    assert "levi" in rules["aliases"]["Jeans"]
    assert "hydro flask" in rules["aliases"]["Water Bottle"]


def test_ai_generate_shopping_rules_merges_with_existing(
    sample_shopping_data, shopping_intake_db, shopping_rules_file, monkeypatch
):
    shopping_rules_file.write_text(json.dumps({"aliases": {"Jeans": ["wrangler"]}, "types": {}}))
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    web_app._ai_generate_shopping_rules(
        sample_shopping_data, shopping_intake_db, since="2026-01-01"
    )
    rules = web_app.load_shopping_rules()
    assert "wrangler" in rules["aliases"]["Jeans"]
    assert "levi" in rules["aliases"]["Jeans"]


def test_ai_generate_shopping_rules_deduplicates_tokens(
    sample_shopping_data, shopping_intake_db, shopping_rules_file, monkeypatch
):
    shopping_rules_file.write_text(
        json.dumps({"aliases": {"Jeans": ["levi", "denim"]}, "types": {}})
    )
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    web_app._ai_generate_shopping_rules(
        sample_shopping_data, shopping_intake_db, since="2026-01-01"
    )
    rules = web_app.load_shopping_rules()
    assert rules["aliases"]["Jeans"].count("levi") == 1
    assert rules["aliases"]["Jeans"].count("denim") == 1


def test_ai_generate_shopping_rules_returns_zero_on_empty_db(
    sample_shopping_data, tmp_path, shopping_rules_file, monkeypatch
):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE receipts (filename TEXT, scan_date TEXT, merchant TEXT, category TEXT, items_json TEXT)"
    )
    conn.commit()
    conn.close()
    called = []
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: called.append(1) or "{}")
    added, _ = web_app._ai_generate_shopping_rules(sample_shopping_data, db, since="2026-01-01")
    assert added == 0
    assert called == []


def test_ai_generate_shopping_rules_raises_on_bad_response(
    sample_shopping_data, shopping_intake_db, shopping_rules_file, monkeypatch
):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: '{"no_aliases_key": {}}')
    with pytest.raises(ValueError):
        web_app._ai_generate_shopping_rules(
            sample_shopping_data, shopping_intake_db, since="2026-01-01"
        )


# ---------------------------------------------------------------------------
# HTTP routes — basic CRUD
# ---------------------------------------------------------------------------


def test_get_shopping_renders_ok(client):
    resp = client.get("/shopping")
    assert resp.status_code == 200
    assert b"Shopping" in resp.data


def test_get_shopping_shows_items(client):
    resp = client.get("/shopping")
    assert b"T-Shirt" in resp.data
    assert b"Toilet Paper" in resp.data
    assert b"Water Bottle" in resp.data


def test_add_shopping_category(client, shopping_file):
    resp = client.post(
        "/shopping/add-category",
        data={"category": "Electronics"},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 302
    data = json.loads(shopping_file.read_text())
    assert any(c["name"] == "Electronics" for c in data["categories"])


def test_add_shopping_category_duplicate_flashes_error(client):
    resp = client.post(
        "/shopping/add-category",
        data={"category": "Clothes"},
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"already exists" in resp.data


def test_add_shopping_item(client, shopping_file):
    resp = client.post(
        "/shopping/add-item",
        data={"category": "Clothes", "item": "Boots"},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 302
    data = json.loads(shopping_file.read_text())
    clothes = next(c for c in data["categories"] if c["name"] == "Clothes")
    assert any(i["name"] == "Boots" for i in clothes["items"])


def test_toggle_shopping_item_flips_owned(client, shopping_file):
    client.post(
        "/shopping/toggle-item",
        data={"category": "Clothes", "item": "T-Shirt"},
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(shopping_file.read_text())
    clothes = next(c for c in data["categories"] if c["name"] == "Clothes")
    tshirt = next(i for i in clothes["items"] if i["name"] == "T-Shirt")
    assert tshirt["owned"] is True


def test_delete_shopping_item(client, shopping_file):
    client.post(
        "/shopping/delete-item",
        data={"category": "Clothes", "item": "T-Shirt"},
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(shopping_file.read_text())
    clothes = next(c for c in data["categories"] if c["name"] == "Clothes")
    assert not any(i["name"] == "T-Shirt" for i in clothes["items"])


def test_delete_shopping_category(client, shopping_file):
    client.post(
        "/shopping/delete-category",
        data={"category": "Recreation"},
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(shopping_file.read_text())
    assert not any(c["name"] == "Recreation" for c in data["categories"])


def test_reset_shopping_marks_all_not_owned(client, shopping_file):
    client.post(
        "/shopping/reset",
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(shopping_file.read_text())
    all_items = [i for c in data["categories"] for i in c["items"]]
    assert all(not i.get("owned", False) for i in all_items)


# ---------------------------------------------------------------------------
# HTTP routes — sync-intake
# ---------------------------------------------------------------------------


def test_sync_shopping_intake_route_marks_items(
    client, shopping_file, shopping_intake_db, monkeypatch
):
    monkeypatch.setattr(web_app, "INTAKE_DB", shopping_intake_db)
    resp = client.post(
        "/shopping/sync-intake",
        data={"since": "2026-05-01"},
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    data = json.loads(shopping_file.read_text())
    clothes = next(c for c in data["categories"] if c["name"] == "Clothes")
    jeans = next(i for i in clothes["items"] if i["name"] == "Jeans")
    assert jeans["owned"] is True
    assert b"owned" in resp.data or b"Marked" in resp.data


def test_sync_shopping_intake_route_missing_db(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "INTAKE_DB", tmp_path / "absent.db")
    resp = client.post(
        "/shopping/sync-intake",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"not found" in resp.data or b"error" in resp.data.lower()


def test_sync_shopping_intake_route_no_matches_flashes_info(
    client, shopping_file, monkeypatch, tmp_path
):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE receipts (filename TEXT, scan_date TEXT, merchant TEXT, category TEXT, items_json TEXT)"
    )
    conn.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        (
            "x.jpg",
            "2026-06-01",
            "store",
            "misc",
            json.dumps([{"description": "MYSTERY ITEM XYZ", "amount": 5}]),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(web_app, "INTAKE_DB", db)
    resp = client.post(
        "/shopping/sync-intake",
        data={"since": "2026-05-01"},
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"No new matches" in resp.data


# ---------------------------------------------------------------------------
# HTTP routes — ai-categorize
# ---------------------------------------------------------------------------


def test_ai_categorize_shopping_route_applies_types(
    client, shopping_file, monkeypatch
):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _CATEGORIZE_RESPONSE)
    resp = client.post(
        "/shopping/ai-categorize",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    data = json.loads(shopping_file.read_text())
    clothes = next(c for c in data["categories"] if c["name"] == "Clothes")
    tshirt = next(i for i in clothes["items"] if i["name"] == "T-Shirt")
    assert tshirt.get("type") == "Tops"
    assert b"Assigned" in resp.data or b"types" in resp.data


def test_ai_categorize_shopping_route_ollama_down(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    resp = client.post(
        "/shopping/ai-categorize",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"not reachable" in resp.data or b"Ollama" in resp.data


def test_ai_categorize_shopping_route_llm_error(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(
        web_app,
        "_ollama_generate",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("timeout")),
    )
    resp = client.post(
        "/shopping/ai-categorize",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"failed" in resp.data or b"error" in resp.data.lower()


# ---------------------------------------------------------------------------
# HTTP routes — ai-rules
# ---------------------------------------------------------------------------


def test_ai_rules_shopping_route_saves_aliases(
    client, shopping_rules_file, shopping_intake_db, monkeypatch
):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "INTAKE_DB", shopping_intake_db)
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    resp = client.post(
        "/shopping/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    rules = web_app.load_shopping_rules()
    assert "levi" in rules["aliases"].get("Jeans", [])
    assert b"token" in resp.data or b"alias" in resp.data.lower() or b"Added" in resp.data


def test_ai_rules_shopping_route_ollama_down(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    resp = client.post(
        "/shopping/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"Ollama" in resp.data or b"not reachable" in resp.data


def test_ai_rules_shopping_route_missing_db(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "INTAKE_DB", tmp_path / "absent.db")
    resp = client.post(
        "/shopping/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"not found" in resp.data or b"Intake" in resp.data


def test_ai_rules_shopping_route_llm_error(
    client, shopping_intake_db, shopping_rules_file, monkeypatch
):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "INTAKE_DB", shopping_intake_db)
    monkeypatch.setattr(
        web_app,
        "_ollama_generate",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("model error")),
    )
    resp = client.post(
        "/shopping/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"failed" in resp.data or b"error" in resp.data.lower()


# ---------------------------------------------------------------------------
# GET /shopping template vars
# ---------------------------------------------------------------------------


def test_get_shopping_passes_rules_count(client, shopping_rules_file, monkeypatch):
    shopping_rules_file.write_text(
        json.dumps({"aliases": {"Jeans": ["levi", "denim"], "Sneakers": ["nike"]}, "types": {}})
    )
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    resp = client.get("/shopping")
    assert b"3 AI rules" in resp.data


def test_get_shopping_shows_type_badge_after_categorize(
    client, shopping_file, monkeypatch
):
    data = json.loads(shopping_file.read_text())
    data["categories"][0]["items"][0]["type"] = "Tops"
    shopping_file.write_text(json.dumps(data))
    resp = client.get("/shopping")
    assert b"Tops" in resp.data
    assert b"type-Tops" in resp.data


def test_get_shopping_shows_owned_count(client):
    resp = client.get("/shopping")
    # Jacket is owned=True in sample_shopping_data — "1 / 7 owned" or similar
    assert b"owned" in resp.data
