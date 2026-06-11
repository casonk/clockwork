"""Tests for groceries: matching helpers, intake sync, AI workflows, and HTTP routes."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the web app module (same approach as test_web_app.py)
# ---------------------------------------------------------------------------

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_APP_PATH = _WEB_DIR / "app.py"
_SPEC = spec_from_file_location("clockwork_web_app_groceries", _APP_PATH)
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
# Register before exec so Flask can resolve the template directory via __file__
sys.modules["clockwork_web_app_groceries"] = web_app
_SPEC.loader.exec_module(web_app)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def groceries_file(tmp_path, monkeypatch):
    """Redirect GROCERIES_FILE to a temp path and return it."""
    p = tmp_path / "groceries.json"
    monkeypatch.setattr(web_app, "GROCERIES_FILE", p)
    return p


@pytest.fixture()
def rules_file(tmp_path, monkeypatch):
    """Redirect GROCERY_RULES_FILE to a temp path and return it."""
    p = tmp_path / "grocery-rules.json"
    monkeypatch.setattr(web_app, "GROCERY_RULES_FILE", p)
    return p


@pytest.fixture()
def sample_data():
    """Minimal grocery data structure."""
    return {
        "categories": [
            {
                "name": "Food",
                "items": [
                    {"name": "Eggs", "stocked": False},
                    {"name": "Milk", "stocked": False},
                    {"name": "Bread", "stocked": False},
                    {"name": "Chicken", "stocked": True},
                ],
            },
            {
                "name": "Drinks",
                "items": [
                    {"name": "Beer", "stocked": False},
                    {"name": "Pop", "stocked": False},
                ],
            },
        ]
    }


@pytest.fixture()
def populated_groceries(groceries_file, sample_data):
    """Write sample_data to the redirected groceries file and return its path."""
    groceries_file.write_text(json.dumps(sample_data))
    return groceries_file


@pytest.fixture()
def intake_db(tmp_path):
    """Create a minimal intake SQLite DB with one grocery receipt."""
    db_path = tmp_path / "intake.db"
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
    # A grocery/supermarket receipt with recognizable items
    items = [
        {"description": "EGGS 6CT", "amount": 2.99, "quantity": 1, "unit_price": None},
        {"description": "GREAT VALUE MILK", "amount": 3.49, "quantity": 1, "unit_price": None},
        {"description": "BUD LIGHT 18PK", "amount": 14.99, "quantity": 1, "unit_price": None},
        {"description": "REPAIR TAPE", "amount": 4.99, "quantity": 1, "unit_price": None},
    ]
    conn.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        ("20260601_1200.jpg", "2026-06-01", "walmart", "grocery/supermarket", json.dumps(items)),
    )
    # An old receipt outside the default 30-day window
    old_items = [{"description": "BREAD LOAF", "amount": 2.49, "quantity": 1, "unit_price": None}]
    conn.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        (
            "20260101_1200.jpg",
            "2026-01-01",
            "walmart",
            "grocery/supermarket",
            json.dumps(old_items),
        ),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def client(populated_groceries, rules_file, monkeypatch):
    """Flask test client with grocery/rules files redirected to temp paths.

    Ollama availability is stubbed False by default so GET /groceries never
    makes a real network call; individual tests that exercise AI routes
    override this via their own monkeypatch.setattr call.
    """
    monkeypatch.setattr(web_app, "INTAKE_DB", Path("/nonexistent/intake.db"))
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    web_app.app.config["TESTING"] = True
    web_app.app.config["SECRET_KEY"] = "test-secret"
    return web_app.app.test_client()


# ---------------------------------------------------------------------------
# _norm_text
# ---------------------------------------------------------------------------


def test_norm_text_lowercases():
    assert web_app._norm_text("EGGS 6CT") == "eggs 6ct"


def test_norm_text_strips_punctuation():
    assert web_app._norm_text("BUD-LIGHT, 18pk!") == "bud light 18pk"


def test_norm_text_collapses_spaces():
    assert web_app._norm_text("  hello   world  ") == "hello world"


# ---------------------------------------------------------------------------
# _word_hits
# ---------------------------------------------------------------------------


def test_word_hits_exact_match():
    assert web_app._word_hits("eggs", ["eggs", "6ct"]) is True


def test_word_hits_prefix_overlap():
    # "potato" should match "potatoes"
    assert web_app._word_hits("potato", ["potatoes", "5lb"]) is True


def test_word_hits_grocery_in_dense_token():
    # "eggs" embedded in OCR token "eggs6ct"
    assert web_app._word_hits("eggs", ["eggs6ct"]) is True


def test_word_hits_no_match():
    assert web_app._word_hits("eggs", ["repair", "tape"]) is False


def test_word_hits_short_word_requires_exact():
    # tokens < 3 chars only match exactly
    assert web_app._word_hits("oj", ["oj"]) is True
    assert web_app._word_hits("oj", ["orange"]) is False


# ---------------------------------------------------------------------------
# _item_matches_desc
# ---------------------------------------------------------------------------


def test_item_matches_desc_exact_word():
    assert web_app._item_matches_desc("Eggs", "EGGS 6CT") is True


def test_item_matches_desc_alias_single_word():
    assert web_app._item_matches_desc("Beer", "BUD LIGHT 18PK") is True


def test_item_matches_desc_alias_multi_token():
    assert web_app._item_matches_desc("Pop", "CANADA DRY PC") is True


def test_item_matches_desc_alias_brand():
    assert web_app._item_matches_desc("Pop", "DR PEPPER ZERO") is True


def test_item_matches_desc_milk_variant():
    assert web_app._item_matches_desc("Milk", "GREAT VALUE MILK") is True


def test_item_matches_desc_multi_word_item():
    # "Green Beans" — both words must appear
    assert web_app._item_matches_desc("Green Beans", "FRESH GREEN BEANS 12OZ") is True


def test_item_matches_desc_multi_word_item_missing_one_word():
    assert web_app._item_matches_desc("Green Beans", "GREEN SALAD") is False


def test_item_matches_desc_no_match():
    assert web_app._item_matches_desc("Eggs", "REPAIR TAPE") is False


def test_item_matches_desc_prefix_stem():
    assert web_app._item_matches_desc("Potatoes", "RUSSET POTATO 5LB") is True


def test_item_matches_desc_case_insensitive():
    assert web_app._item_matches_desc("avocado", "Hass Avocado") is True


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


def test_extract_json_plain_object():
    assert web_app._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_markdown_fence():
    raw = '```json\n{"types": {"Eggs": "Dairy"}}\n```'
    assert web_app._extract_json(raw) == {"types": {"Eggs": "Dairy"}}


def test_extract_json_with_leading_text():
    raw = 'Sure, here you go:\n{"aliases": {}}'
    assert web_app._extract_json(raw) == {"aliases": {}}


def test_extract_json_array():
    assert web_app._extract_json("[1, 2, 3]") == [1, 2, 3]


# ---------------------------------------------------------------------------
# load_grocery_rules / save_grocery_rules
# ---------------------------------------------------------------------------


def test_load_grocery_rules_returns_empty_when_missing(rules_file):
    assert not rules_file.exists()
    result = web_app.load_grocery_rules()
    assert result == {"aliases": {}, "types": {}}


def test_load_grocery_rules_reads_existing(rules_file):
    rules_file.write_text(json.dumps({"aliases": {"Beer": ["bud"]}, "types": {}}))
    result = web_app.load_grocery_rules()
    assert result["aliases"]["Beer"] == ["bud"]


def test_save_grocery_rules_writes_json(rules_file):
    web_app.save_grocery_rules({"aliases": {"Milk": ["dairy"]}, "types": {}})
    saved = json.loads(rules_file.read_text())
    assert saved["aliases"]["Milk"] == ["dairy"]


def test_load_grocery_rules_tolerates_corrupt_file(rules_file):
    rules_file.write_text("not json {{")
    result = web_app.load_grocery_rules()
    assert result == {"aliases": {}, "types": {}}


# ---------------------------------------------------------------------------
# _sync_from_intake
# ---------------------------------------------------------------------------


def test_sync_marks_matching_items_stocked(sample_data, intake_db):
    count, marked = web_app._sync_from_intake(intake_db, sample_data, since="2026-05-01")
    names = [m.split(": ", 1)[1] for m in marked]
    assert "Eggs" in names
    assert "Milk" in names
    assert "Beer" in names
    # Chicken was already stocked — should not appear
    assert "Chicken" not in names


def test_sync_skips_already_stocked_items(sample_data, intake_db):
    count, _ = web_app._sync_from_intake(intake_db, sample_data, since="2026-05-01")
    # Chicken was stocked before sync; assert its stocked state is unchanged
    chicken = next(
        i for c in sample_data["categories"] for i in c["items"] if i["name"] == "Chicken"
    )
    assert chicken["stocked"] is True


def test_sync_does_not_mark_unrelated_item(sample_data, intake_db):
    web_app._sync_from_intake(intake_db, sample_data, since="2026-05-01")
    bread = next(i for c in sample_data["categories"] for i in c["items"] if i["name"] == "Bread")
    # "REPAIR TAPE" is in the receipt but should not match Bread
    assert bread["stocked"] is False


def test_sync_returns_zero_when_db_missing(sample_data, tmp_path):
    absent = tmp_path / "no.db"
    count, marked = web_app._sync_from_intake(absent, sample_data)
    assert count == 0
    assert marked == []


def test_sync_respects_since_date(sample_data, intake_db):
    # "BREAD LOAF" receipt is from 2026-01-01; within a wide window it matches
    count_wide, marked_wide = web_app._sync_from_intake(intake_db, sample_data, since="2026-01-01")
    bread_wide = next(
        i for c in sample_data["categories"] for i in c["items"] if i["name"] == "Bread"
    )
    assert bread_wide["stocked"] is True


def test_sync_uses_ai_rules(sample_data, intake_db, rules_file):
    # Add an AI-generated rule that maps "BLKCHRY" to an item not in hardcoded aliases
    rules_file.write_text(json.dumps({"aliases": {"Bread": ["blkchry"]}, "types": {}}))
    # Insert a receipt with "BLKCHRY" description
    conn = sqlite3.connect(str(intake_db))
    conn.execute(
        "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
        (
            "20260605_0000.jpg",
            "2026-06-05",
            "walmart",
            "grocery/supermarket",
            json.dumps(
                [{"description": "BLKCHRY 80OZ", "amount": 3.0, "quantity": 1, "unit_price": None}]
            ),
        ),
    )
    conn.commit()
    conn.close()
    count, marked = web_app._sync_from_intake(intake_db, sample_data, since="2026-06-01")
    names = [m.split(": ", 1)[1] for m in marked]
    assert "Bread" in names


# ---------------------------------------------------------------------------
# _ai_categorize
# ---------------------------------------------------------------------------

_CATEGORIZE_RESPONSE = json.dumps(
    {
        "types": {
            "Eggs": "Dairy",
            "Milk": "Beverages",
            "Bread": "Bakery",
            "Beer": "Spirits",
            "Pop": "Beverages",
        }
    }
)


def test_ai_categorize_applies_types(sample_data, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _CATEGORIZE_RESPONSE)
    count, model = web_app._ai_categorize(sample_data)
    items = {i["name"]: i for c in sample_data["categories"] for i in c["items"]}
    assert items["Eggs"]["type"] == "Dairy"
    assert items["Bread"]["type"] == "Bakery"
    assert items["Beer"]["type"] == "Spirits"
    assert count == 5


def test_ai_categorize_skips_already_typed(sample_data, monkeypatch):
    # Pre-type one item
    sample_data["categories"][0]["items"][0]["type"] = "Dairy"
    response = json.dumps(
        {"types": {"Milk": "Beverages", "Bread": "Bakery", "Beer": "Spirits", "Pop": "Beverages"}}
    )
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: response)
    count, _ = web_app._ai_categorize(sample_data)
    assert count == 4  # Eggs was already typed, skipped in prompt


def test_ai_categorize_returns_zero_when_all_typed(sample_data, monkeypatch):
    for cat in sample_data["categories"]:
        for item in cat["items"]:
            item["type"] = "Produce"
    called = []
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: called.append(1) or "{}")
    count, _ = web_app._ai_categorize(sample_data)
    assert count == 0
    assert called == []  # LLM never called


def test_ai_categorize_raises_on_bad_json(sample_data, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: "not json at all")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        web_app._ai_categorize(sample_data)


def test_ai_categorize_raises_on_missing_types_key(sample_data, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: '{"wrong_key": {}}')
    with pytest.raises(ValueError):
        web_app._ai_categorize(sample_data)


# ---------------------------------------------------------------------------
# _ai_generate_rules
# ---------------------------------------------------------------------------

_RULES_RESPONSE = json.dumps({"aliases": {"Beer": ["bud", "coors"], "Eggs": ["eggs 6ct"]}})


def test_ai_generate_rules_saves_aliases(sample_data, intake_db, rules_file, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    added, model = web_app._ai_generate_rules(sample_data, intake_db, since="2026-01-01")
    assert added >= 2
    rules = web_app.load_grocery_rules()
    assert "bud" in rules["aliases"]["Beer"]
    assert "eggs 6ct" in rules["aliases"]["Eggs"]


def test_ai_generate_rules_merges_with_existing(sample_data, intake_db, rules_file, monkeypatch):
    rules_file.write_text(json.dumps({"aliases": {"Beer": ["heineken"]}, "types": {}}))
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    web_app._ai_generate_rules(sample_data, intake_db, since="2026-01-01")
    rules = web_app.load_grocery_rules()
    assert "heineken" in rules["aliases"]["Beer"]
    assert "bud" in rules["aliases"]["Beer"]


def test_ai_generate_rules_deduplicates_tokens(sample_data, intake_db, rules_file, monkeypatch):
    rules_file.write_text(json.dumps({"aliases": {"Beer": ["bud", "coors"]}, "types": {}}))
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    added, _ = web_app._ai_generate_rules(sample_data, intake_db, since="2026-01-01")
    rules = web_app.load_grocery_rules()
    assert rules["aliases"]["Beer"].count("bud") == 1
    assert rules["aliases"]["Beer"].count("coors") == 1


def test_ai_generate_rules_returns_zero_on_empty_db(sample_data, tmp_path, rules_file, monkeypatch):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE receipts (filename TEXT, scan_date TEXT, merchant TEXT, category TEXT, items_json TEXT)"
    )
    conn.commit()
    conn.close()
    called = []
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: called.append(1) or "{}")
    added, _ = web_app._ai_generate_rules(sample_data, db, since="2026-01-01")
    assert added == 0
    assert called == []


def test_ai_generate_rules_raises_on_bad_response(sample_data, intake_db, rules_file, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: '{"no_aliases_key": {}}')
    with pytest.raises(ValueError):
        web_app._ai_generate_rules(sample_data, intake_db, since="2026-01-01")


# ---------------------------------------------------------------------------
# _ollama_available
# ---------------------------------------------------------------------------


def test_ollama_available_returns_true_on_200(monkeypatch):
    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp())
    assert web_app._ollama_available() is True


def test_ollama_available_returns_false_on_error(monkeypatch):
    def _raise(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    assert web_app._ollama_available() is False


# ---------------------------------------------------------------------------
# HTTP routes — basic CRUD
# ---------------------------------------------------------------------------


def test_get_groceries_renders_ok(client):
    resp = client.get("/groceries")
    assert resp.status_code == 200
    assert b"Groceries" in resp.data


def test_get_groceries_shows_items(client):
    resp = client.get("/groceries")
    assert b"Eggs" in resp.data
    assert b"Beer" in resp.data


def test_add_category(client, groceries_file):
    resp = client.post(
        "/groceries/add-category",
        data={"category": "Produce"},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 302
    data = json.loads(groceries_file.read_text())
    assert any(c["name"] == "Produce" for c in data["categories"])


def test_add_category_duplicate_flashes_error(client):
    resp = client.post(
        "/groceries/add-category",
        data={"category": "Food"},
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"already exists" in resp.data


def test_add_item(client, groceries_file):
    resp = client.post(
        "/groceries/add-item",
        data={"category": "Food", "item": "Salmon"},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 302
    data = json.loads(groceries_file.read_text())
    food = next(c for c in data["categories"] if c["name"] == "Food")
    assert any(i["name"] == "Salmon" for i in food["items"])


def test_toggle_item_flips_stocked(client, groceries_file):
    client.post(
        "/groceries/toggle-item",
        data={"category": "Food", "item": "Eggs"},
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(groceries_file.read_text())
    food = next(c for c in data["categories"] if c["name"] == "Food")
    eggs = next(i for i in food["items"] if i["name"] == "Eggs")
    assert eggs["stocked"] is True


def test_delete_item(client, groceries_file):
    client.post(
        "/groceries/delete-item",
        data={"category": "Food", "item": "Eggs"},
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(groceries_file.read_text())
    food = next(c for c in data["categories"] if c["name"] == "Food")
    assert not any(i["name"] == "Eggs" for i in food["items"])


def test_delete_category(client, groceries_file):
    client.post(
        "/groceries/delete-category",
        data={"category": "Drinks"},
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(groceries_file.read_text())
    assert not any(c["name"] == "Drinks" for c in data["categories"])


def test_reset_marks_all_not_stocked(client, groceries_file):
    client.post(
        "/groceries/reset",
        headers={"Origin": "http://localhost"},
    )
    data = json.loads(groceries_file.read_text())
    all_items = [i for c in data["categories"] for i in c["items"]]
    assert all(not i["stocked"] for i in all_items)


# ---------------------------------------------------------------------------
# HTTP routes — sync-intake
# ---------------------------------------------------------------------------


def test_sync_intake_route_marks_items(client, groceries_file, intake_db, monkeypatch):
    monkeypatch.setattr(web_app, "INTAKE_DB", intake_db)
    resp = client.post(
        "/groceries/sync-intake",
        data={"since": "2026-05-01"},
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    data = json.loads(groceries_file.read_text())
    food = next(c for c in data["categories"] if c["name"] == "Food")
    eggs = next(i for i in food["items"] if i["name"] == "Eggs")
    assert eggs["stocked"] is True
    assert b"stocked" in resp.data or b"Marked" in resp.data


def test_sync_intake_route_missing_db(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "INTAKE_DB", tmp_path / "absent.db")
    resp = client.post(
        "/groceries/sync-intake",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"not found" in resp.data or b"error" in resp.data.lower()


def test_sync_intake_route_no_matches_flashes_info(client, groceries_file, monkeypatch, tmp_path):
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
            "grocery/supermarket",
            json.dumps([{"description": "DUCT TAPE", "amount": 5}]),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(web_app, "INTAKE_DB", db)
    resp = client.post(
        "/groceries/sync-intake",
        data={"since": "2026-05-01"},
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"No new matches" in resp.data


# ---------------------------------------------------------------------------
# HTTP routes — ai-categorize
# ---------------------------------------------------------------------------


def test_ai_categorize_route_applies_types(client, groceries_file, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _CATEGORIZE_RESPONSE)
    resp = client.post(
        "/groceries/ai-categorize",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    data = json.loads(groceries_file.read_text())
    food = next(c for c in data["categories"] if c["name"] == "Food")
    eggs = next(i for i in food["items"] if i["name"] == "Eggs")
    assert eggs.get("type") == "Dairy"
    assert b"Assigned" in resp.data or b"types" in resp.data


def test_ai_categorize_route_ollama_down(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    resp = client.post(
        "/groceries/ai-categorize",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"not reachable" in resp.data or b"Ollama" in resp.data


def test_ai_categorize_route_llm_error_flashes_error(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(
        web_app, "_ollama_generate", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("timeout"))
    )
    resp = client.post(
        "/groceries/ai-categorize",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"failed" in resp.data or b"error" in resp.data.lower()


# ---------------------------------------------------------------------------
# HTTP routes — ai-rules
# ---------------------------------------------------------------------------


def test_ai_rules_route_saves_aliases(client, rules_file, intake_db, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "INTAKE_DB", intake_db)
    monkeypatch.setattr(web_app, "_ollama_generate", lambda *a, **kw: _RULES_RESPONSE)
    resp = client.post(
        "/groceries/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    rules = web_app.load_grocery_rules()
    assert "bud" in rules["aliases"].get("Beer", [])
    assert b"token" in resp.data or b"alias" in resp.data.lower() or b"Added" in resp.data


def test_ai_rules_route_ollama_down(client, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    resp = client.post(
        "/groceries/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"Ollama" in resp.data or b"not reachable" in resp.data


def test_ai_rules_route_missing_db(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "INTAKE_DB", tmp_path / "absent.db")
    resp = client.post(
        "/groceries/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"not found" in resp.data or b"Intake" in resp.data


def test_ai_rules_route_llm_error_flashes_error(client, intake_db, rules_file, monkeypatch):
    monkeypatch.setattr(web_app, "_ollama_available", lambda: True)
    monkeypatch.setattr(web_app, "INTAKE_DB", intake_db)
    monkeypatch.setattr(
        web_app,
        "_ollama_generate",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("model error")),
    )
    resp = client.post(
        "/groceries/ai-rules",
        headers={"Origin": "http://localhost"},
        follow_redirects=True,
    )
    assert b"failed" in resp.data or b"error" in resp.data.lower()


# ---------------------------------------------------------------------------
# GET /groceries template vars
# ---------------------------------------------------------------------------


def test_get_groceries_passes_rules_count(client, rules_file, monkeypatch):
    rules_file.write_text(
        json.dumps({"aliases": {"Beer": ["bud", "coors"], "Eggs": ["eggct"]}, "types": {}})
    )
    monkeypatch.setattr(web_app, "_ollama_available", lambda: False)
    resp = client.get("/groceries")
    assert b"3 AI rules" in resp.data


def test_get_groceries_shows_type_badge_after_categorize(client, groceries_file, monkeypatch):
    # Manually write a typed item
    data = json.loads(groceries_file.read_text())
    data["categories"][0]["items"][0]["type"] = "Dairy"
    groceries_file.write_text(json.dumps(data))
    resp = client.get("/groceries")
    assert b"Dairy" in resp.data
    assert b"type-Dairy" in resp.data
