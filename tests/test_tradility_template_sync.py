"""Clockwork's /to-tradility is Tradility's own page template, served with ``embedded=True``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.tradility_page import PAGE, rendered

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_tradility_template.py"


def _sync():
    spec = importlib.util.spec_from_file_location("sync_tradility_template", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_copy_matches_the_tradility_checkout_when_there_is_one():
    source = _sync().source_path()
    if not source.is_file():
        pytest.skip("no tradility checkout beside this repository")
    assert PAGE.read_bytes() == source.read_bytes(), (
        "web/templates/to-tradility.html differs from Tradility's template; change the page in "
        "the Tradility repository and run scripts/sync_tradility_template.py"
    )


def test_the_embedded_page_carries_clockwork_chrome_only():
    page = rendered(embedded=True)
    assert "<title>Tradility — Clockwork</title>" in page
    assert "Standalone web application" not in page
    assert "initCollapsibleCards" not in page  # the shared collapsible-cards.js folds the cards


def test_sync_script_copies_and_checks(tmp_path, monkeypatch, capsys):
    sync = _sync()
    source = tmp_path / "index.html"
    source.write_text("<html>new</html>")
    copy = tmp_path / "to-tradility.html"
    copy.write_text("<html>old</html>")
    monkeypatch.setattr(sync, "COPY", copy)
    monkeypatch.setattr(sync, "REPO", tmp_path)
    assert sync.main(["--source", str(source), "--check"]) == 1
    assert copy.read_text() == "<html>old</html>"
    assert sync.main(["--source", str(source)]) == 0
    assert copy.read_text() == "<html>new</html>"
    assert sync.main(["--source", str(source), "--check"]) == 0
    assert sync.main(["--source", str(tmp_path / "missing.html")]) == 2
