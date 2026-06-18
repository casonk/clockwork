from pathlib import Path

HOME_TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "home.html"


def test_home_cards_use_shrinkable_mobile_grid_tracks():
    css = HOME_TEMPLATE.read_text()

    assert ".card-grid { grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert ".card {" in css
    assert "min-width: 0;" in css
    assert "overflow-wrap: anywhere;" in css


def test_home_header_uses_compact_mobile_icon_toolbar():
    css = HOME_TEMPLATE.read_text()

    assert "header {" in css
    assert "flex-wrap: nowrap;" in css
    assert ".nav-links {" in css
    assert "flex-wrap: nowrap;" in css
    assert ".home-title { display: none; }" in css
    assert ".nav-label { display: none; }" in css
    assert ".nav-icon { display: inline; }" in css
    assert (
        'nav_icons = {"Monitoring": "📊", "Pit Box": "💻", "Infrastructure": "🧰", "AI": "🤖"}'
        in css
    )
