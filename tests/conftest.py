"""Keep the suite off the network: the To Code add form would ask a local decision model to sort
an item with a blank group or repository. Tests that exercise that path inject a fake client."""

import os

os.environ.setdefault("CLOCKWORK_TRIAGE", "0")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_portfolio_site(tmp_path_factory, monkeypatch):
    """Resolve the portfolio site from an empty store, never this machine's real one."""
    monkeypatch.setenv("PORTFOLIO_STORE", str(tmp_path_factory.mktemp("portfolio-store")))
    monkeypatch.delenv("PORTFOLIO_SITE_DIR", raising=False)
    monkeypatch.delenv("PORTFOLIO_ALLOWED_HOSTS", raising=False)
