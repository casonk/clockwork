"""The git hooks stay wired so a commit cannot skip the checks CI runs."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
CI_STYLE_HOOKS = ("ruff-format", "black", "gitleaks")


def test_style_hooks_run_on_commit_and_again_on_push():
    assert "default_install_hook_types: [pre-commit, pre-push]" in CONFIG
    assert "default_stages: [pre-commit, pre-push]" in CONFIG
    for hook in CI_STYLE_HOOKS:
        assert f"id: {hook}" in CONFIG
    # a hook that pins its own stages would opt out of the push check
    style = CONFIG.split("- repo: local")[0]
    assert not re.search(r"(?m)^\s+stages:", style)


def test_pytest_runs_before_a_push():
    local = CONFIG.split("- repo: local", 1)[1]
    assert "id: pytest" in local and "stages: [pre-push]" in local
    assert "always_run: true" in local


def test_the_hook_installer_installs_both_hook_types_and_is_executable():
    script = ROOT / "scripts" / "install-git-hooks.sh"
    text = script.read_text(encoding="utf-8")
    assert "pre-commit install --hook-type pre-commit --hook-type pre-push" in text
    assert os.access(script, os.X_OK)


def test_contributing_tells_people_to_install_the_hooks():
    assert "scripts/install-git-hooks.sh" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
