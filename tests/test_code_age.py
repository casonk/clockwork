"""Entry dates and the staleness rank on the To Code backlog."""

# The route tests reuse the list fixtures from test_web_lists as parameters.
# ruff: noqa: F811, F401

from __future__ import annotations

import datetime as dt
import json

from tests.test_web_lists import _csrf, client, list_files, web_app
from web import code_age

TODAY = dt.date(2026, 10, 31)


def day(text):
    return dt.date.fromisoformat(text)


def test_a_stamp_beats_history_and_history_beats_the_title_prefix():
    history = code_age.history_dates(
        [{"ts": "2026-10-20T09:00:00", "action": "add", "category": "util", "item": "A"}]
    )
    stamped = {"title": "A", "added": "2026-10-25"}
    assert code_age.added_date(stamped, "util", history) == day("2026-10-25")
    assert code_age.added_date({"title": "A"}, "util", history) == day("2026-10-20")
    prefixed = {"title": "[manual:2026-10-15] Do it"}
    assert code_age.added_date(prefixed, "util", history) == day("2026-10-15")
    assert code_age.added_date({"title": "Plain"}, "util", history) is None


def test_only_the_first_add_event_counts_and_other_actions_are_ignored():
    events = [
        {"ts": "2026-10-01T00:00:00", "action": "add", "category": "c", "item": "x"},
        {"ts": "2026-10-09T00:00:00", "action": "done", "category": "c", "item": "x"},
        {"ts": "2026-10-12T00:00:00", "action": "add", "category": "c", "item": "x"},
    ]
    assert code_age.history_dates(events) == {("c", "x"): day("2026-10-01")}


def test_a_bad_stamp_is_treated_as_missing():
    assert code_age.added_date({"title": "A", "added": "soon"}, "c", {}) is None
    assert code_age.parse_day(None) is None


def test_labels_and_staleness():
    assert [code_age.label(n) for n in (None, 0, 5, 14, 90)] == ["undated", "today", "5d", "2w", "3mo"]
    assert code_age.is_stale(30) and not code_age.is_stale(29) and not code_age.is_stale(None)


def test_sorts_put_undated_and_done_items_last():
    items = [
        {"title": "old", "age_days": 40, "priority": "P1"},
        {"title": "new", "age_days": 2, "priority": "P3"},
        {"title": "none", "age_days": None, "priority": "P1"},
        {"title": "closed", "age_days": 1, "done": True},
    ]

    def order(sort):
        return [i["title"] for i in sorted(items, key=lambda i: code_age.sort_key(sort, i))]

    assert order("newest") == ["new", "old", "none", "closed"]
    assert order("oldest") == ["old", "new", "none", "closed"]
    assert order("priority") == ["old", "none", "new", "closed"]


def _seed(list_files, items):
    list_files["CODE_LIST_FILE"].write_text(
        json.dumps({"categories": [{"name": "util", "items": items}]})
    )


def test_new_items_are_stamped_through_the_form_and_the_api(client, list_files):
    _seed(list_files, [])
    token = _csrf(client, "/api/code")
    client.post(
        "/api/code/add-item",
        json={"category": "util", "repo": "clockwork", "item": "From the API"},
        headers={"X-CSRF-Token": token},
    )
    client.post(
        "/to-code/add-item",
        data={"csrf_token": token, "category": "util", "repo": "clockwork", "item": "From the form"},
    )
    stored = json.loads(list_files["CODE_LIST_FILE"].read_text())["categories"][0]["items"]
    assert [i["added"] for i in stored] == [dt.date.today().isoformat()] * 2


def test_the_page_shows_ages_and_sorts_by_them(client, list_files):
    today = dt.date.today()
    _seed(
        list_files,
        [
            {"title": "Older item", "done": False, "repo": "r", "added": str(today - dt.timedelta(40))},
            {"title": "Newer item", "done": False, "repo": "r", "added": str(today - dt.timedelta(2))},
            {"title": "Undated item", "done": False, "repo": "r"},
        ],
    )
    html = client.get("/to-code?sort=newest").get_data(as_text=True)
    assert html.index("Newer item") < html.index("Older item") < html.index("Undated item")
    assert "1 stale" in html and "undated" in html and "5w" in html
    oldest = client.get("/to-code?sort=oldest").get_data(as_text=True)
    assert oldest.index("Older item") < oldest.index("Newer item")
    assert client.get("/to-code?sort=bogus").status_code == 200


def test_rendering_does_not_write_derived_dates_into_the_backlog(client, list_files):
    _seed(list_files, [{"title": "[manual:2026-10-15] Old", "done": False, "repo": "r"}])
    before = list_files["CODE_LIST_FILE"].read_text()
    client.get("/to-code")
    assert list_files["CODE_LIST_FILE"].read_text() == before


def test_an_updated_item_keeps_its_entry_date(client, list_files):
    _seed(list_files, [{"title": "Keep", "done": False, "repo": "r", "added": "2026-10-01"}])
    client.post(
        "/to-code/update-item",
        data={
            "csrf_token": _csrf(client, "/api/code"),
            "category": "util",
            "item": "Keep",
            "repo": "r",
            "priority": "P2",
        },
    )
    stored = json.loads(list_files["CODE_LIST_FILE"].read_text())["categories"][0]["items"][0]
    assert stored["added"] == "2026-10-01" and stored["priority"] == "P2"
