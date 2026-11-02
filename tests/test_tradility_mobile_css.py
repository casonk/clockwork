"""The Tradility detail view must stay usable on a phone (it once zoomed the page)."""

from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "web" / "templates" / "to-tradility.html"


def phone_rules() -> str:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("@media (max-width: 720px)")
    return text[start : text.index("/* State messages */", start)]


def test_the_detail_column_cannot_grow_past_the_screen():
    assert "grid-template-columns: minmax(0, 1fr)" in phone_rules()


def test_form_fields_are_16px_so_ios_does_not_zoom_on_focus():
    assert "input, select, textarea { font-size: 16px" in phone_rules()


def test_text_size_adjust_is_pinned():
    assert "text-size-adjust: 100%" in PAGE.read_text(encoding="utf-8")


def test_a_detail_stack_cannot_be_widened_by_its_cards():
    text = PAGE.read_text(encoding="utf-8")
    stack = text[text.index(".detail-stack {") :].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr)" in stack and "min-width: 0" in stack
    assert ".detail-card { min-width: 0; }" in text
