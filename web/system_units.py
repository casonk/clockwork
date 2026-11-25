"""System-scope units with a Restart button, and the per-repo Deploy action.

Pure helpers: the systemctl calls and the audit trail stay in ``web.app`` beside the other
Grandmaster Clock actions. Everything here takes its subprocess runner as an argument so the
tests never touch git, systemd or sudo.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "system-units.toml"
UNIT_RE = re.compile(r"[A-Za-z0-9@_.:-]+\.service\Z")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
DEFAULT_BRANCH = "main"

Runner = Callable[[list[str], Path | None, int], tuple[int, str]]


def run_command(args: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    """Run one command without a shell; returns (exit code, combined output)."""
    try:
        done = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{exc.__class__.__name__}: {exc}"
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def portfolio_root() -> Path:
    override = os.environ.get("CLOCKWORK_PORTFOLIO_ROOT")
    return Path(override).expanduser() if override else Path(__file__).resolve().parents[3]


def load_units(path: Path = CONFIG_PATH) -> list[dict]:
    """The allowlisted system units; malformed entries are skipped, never trusted."""
    if not path.exists():
        return []
    units = []
    for raw in tomllib.loads(path.read_text(encoding="utf-8")).get("units", []):
        name, unit = str(raw.get("name", "")), str(raw.get("unit", ""))
        if not name or not UNIT_RE.match(unit):
            continue
        units.append(
            {
                "name": name,
                "unit": unit,
                "repo": str(raw.get("repo", "")),
                "description": str(raw.get("description", "")),
                "critical": bool(raw.get("critical", True)),
            }
        )
    return units


def find_unit(units: list[dict], name: str) -> dict | None:
    return next((u for u in units if u["name"] == name), None)


def restart_command(unit: str) -> list[str]:
    """The exact command the sudoers drop-in allows for this unit."""
    return ["sudo", "-n", "/usr/bin/systemctl", "restart", unit]


def find_checkout(repo: str, root: Path | None = None) -> Path | None:
    """``<root>/<category>/<repo>`` when it is a git checkout; the name is never a path."""
    if not REPO_RE.match(repo):
        return None
    root = root or portfolio_root()
    for candidate in sorted(root.glob(f"*/{repo}")):
        if (candidate / ".git").exists():
            return candidate
    return None


def deploy(checkout: Path, run: Runner = run_command, branch: str = DEFAULT_BRANCH) -> dict:
    """Fast-forward ``checkout`` to ``origin/<branch>``; refuses anything that could lose work.

    Refuses unless the checkout is on ``branch`` with a clean tree, and only ever fast-forwards.
    Returns ``{ok, changed, before, after, detail}``.
    """

    def git(*args: str, timeout: int = 60) -> tuple[int, str]:
        return run(["git", *args], checkout, timeout)

    rc, current = git("rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        return _outcome(False, detail=f"not a git checkout: {current}")
    if current != branch:
        return _outcome(
            False, detail=f"on branch {current!r}, not {branch!r}; switch it yourself first"
        )
    rc, dirty = git("status", "--porcelain")
    if rc != 0 or dirty:
        return _outcome(
            False, detail="uncommitted changes in the checkout; commit or stash them first"
        )
    rc, before = git("rev-parse", "--short", "HEAD")
    rc_fetch, out = git("fetch", "--quiet", "origin", branch, timeout=120)
    if rc_fetch != 0:
        return _outcome(False, before=before, detail=f"git fetch failed: {out[-300:]}")
    rc_merge, out = git("merge", "--ff-only", f"origin/{branch}")
    if rc_merge != 0:
        return _outcome(False, before=before, detail=f"cannot fast-forward: {out[-300:]}")
    _, after = git("rev-parse", "--short", "HEAD")
    return _outcome(True, changed=before != after, before=before, after=after)


def _outcome(
    ok: bool, *, changed: bool = False, before: str = "", after: str = "", detail: str = ""
) -> dict:
    return {
        "ok": ok,
        "changed": changed,
        "before": before,
        "after": after or before,
        "detail": detail,
    }
