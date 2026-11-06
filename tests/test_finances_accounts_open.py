"""The Finances page lets several accounts stay open at once."""

import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "web" / "templates" / "finances.html"


class FinancesAccountsOpenTest(unittest.TestCase):
    def test_open_accounts_are_a_set_not_a_single_id(self):
        html = PAGE.read_text()
        self.assertIn("open: new Set()", html)
        self.assertIn("state.open.has(", html)
        self.assertNotIn("state.open === ", html)

    def test_histories_are_cached_so_reopening_does_not_refetch(self):
        self.assertIn("state.hist", PAGE.read_text())


if __name__ == "__main__":
    unittest.main()
