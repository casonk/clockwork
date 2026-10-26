from pathlib import Path

HOME_TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "home.html"
GROCERIES_TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "groceries.html"
SHOPPING_TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "shopping.html"


def test_home_cards_use_shrinkable_mobile_grid_tracks():
    css = HOME_TEMPLATE.read_text()

    assert ".card-grid { grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert ".card {" in css
    assert "min-width: 0;" in css
    assert "overflow-wrap: anywhere;" in css


def test_standalone_pages_share_site_navigation():
    templates = HOME_TEMPLATE.parent
    for path in templates.glob("*.html"):
        page = path.read_text()
        if "<body>" in page:
            assert '{% include "_site_nav.html" %}' in page, path.name
            assert "site-nav.css" in page, path.name

    home = HOME_TEMPLATE.read_text()
    assert 'action="/refresh/site"' in home
    assert 'aria-label="Refresh site"' in home
    assert 'class="nav-links"' not in home


def test_list_pages_keep_refresh_action_beside_shared_navigation():
    for template in (GROCERIES_TEMPLATE, SHOPPING_TEMPLATE):
        page = template.read_text()

        assert '{% include "_site_nav.html" %}' in page
        assert 'class="back-link nav-category-link"' not in page
        assert 'action="/refresh/site"' in page
        assert 'aria-label="Refresh site"' in page
