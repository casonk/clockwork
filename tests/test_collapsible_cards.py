"""Every page gets collapsible cards from one shared script, and no page keeps its own copy."""

import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
SCRIPT = WEB / "static" / "collapsible-cards.js"


class CollapsibleCardsTest(unittest.TestCase):
    def test_the_site_nav_loads_the_shared_script_on_every_page(self):
        nav = (WEB / "templates" / "_site_nav.html").read_text()
        self.assertIn("filename='collapsible-cards.js'", nav)

    def test_the_stylesheet_every_page_links_carries_the_rules(self):
        css = (WEB / "static" / "site-nav.css").read_text()
        for needle in (".cc-title", ".cc-card.cc-collapsed", ".cc-head"):
            self.assertIn(needle, css)

    def test_no_page_keeps_its_own_copy(self):
        for page in (WEB / "templates").glob("*.html"):
            text = page.read_text()
            self.assertNotIn("initCollapsibleCards", text, page.name)
            self.assertNotIn(".cc-collapsed", text, page.name)

    def test_modals_fullscreen_and_popouts_are_left_alone(self):
        js = SCRIPT.read_text()
        for needle in ("dialog", ".fs-card", ".chart-fs", ".popout", "data-cc"):
            self.assertIn(needle, js)

    def test_script_parses(self):
        try:
            result = subprocess.run(
                ["node", "--check", str(SCRIPT)], capture_output=True, text=True
            )
        except FileNotFoundError:
            self.skipTest("node is not installed")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
