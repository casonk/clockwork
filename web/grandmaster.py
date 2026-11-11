"""Grandmaster Clock: the apps (long-running services) Clockwork can enable, disable and restart.

Pure selection and view-model helpers; the systemd/launchd calls stay in ``web.app`` so the
page uses exactly the same enable, disable and restart paths as the scheduler page.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ACTIONS = ("enable", "disable", "restart")

# An app whose Home card has a different name from the app's own job.
HOME_CARD_ALIASES = {"pit-box-guacamole": "pit-box-remote-desktop"}


def home_card_name(job_name: str, card_names: set[str]) -> str | None:
    """The Home card that fronts this app, if it has one."""
    candidate = HOME_CARD_ALIASES.get(job_name, job_name)
    return candidate if candidate in card_names else None


def is_app(job: dict) -> bool:
    """A long-running service: no timer, no cron, and not a one-shot task."""
    return not job.get("timer") and not job.get("cron") and job.get("service_type") != "oneshot"


def state_label(status: dict, enabled: bool) -> str:
    """One word for the row: running, restarting, failed, stopped or disabled."""
    active = str(status.get("active_state") or "")
    if active == "failed":
        return "failed"
    if status.get("active"):
        return "running"
    if active in ("activating", "auto-restart"):
        return "restarting"  # a crash loop: systemd keeps starting it and it keeps exiting
    if active.startswith("unsupported"):
        return "unknown"
    return "stopped" if enabled else "disabled"


def app_row(
    repo: str,
    manifest_path: str,
    job: dict,
    status: dict,
    enabled: bool,
    is_self: bool,
    home_card: str | None = None,
    home_hidden: bool = False,
):
    return {
        "home_card": home_card,
        "home_hidden": home_hidden,
        "repo": repo,
        "manifest_path": manifest_path,
        "name": job["name"],
        "description": job.get("description", ""),
        "unit": job.get("scheduler_id", ""),
        "scope": job.get("scope", "user"),
        "enabled": enabled,
        "active_state": status.get("active_state", ""),
        "state": state_label(status, enabled),
        "protected": is_self,
        "critical": job["name"] in critical_apps(),
    }


def group_by_repo(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["repo"], []).append(row)
    return sorted(grouped.items())


# ---- Guardrails -------------------------------------------------------------------------
#
# Critical apps are the ones whose loss can cut you off from the machine. Disabling or
# restarting one needs its name typed back (the API takes it as ``confirm``), and bulk actions
# never touch them. Set CLOCKWORK_GRANDMASTER_CRITICAL (comma separated; empty means none) to
# change the list.

CRITICAL_DEFAULT = frozenset({"pit-box-guacd", "pit-box-guacamole"})
GUARDED_ACTIONS = frozenset({"disable", "restart"})
BULK_COOLDOWN_S = 10.0
AUDIT_FILE = Path(
    os.environ.get(
        "CLOCKWORK_GRANDMASTER_AUDIT_FILE",
        Path(__file__).resolve().parents[1] / "config" / "grandmaster-audit.jsonl",
    )
)
AUDIT_MAX_BYTES = 512 * 1024
AUDIT_KEEP_LINES = 1000


def critical_apps() -> frozenset[str]:
    raw = os.environ.get("CLOCKWORK_GRANDMASTER_CRITICAL")
    if raw is None:
        return CRITICAL_DEFAULT
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def confirmation_problem(action: str, name: str, confirm: object, critical: frozenset[str]) -> str:
    """Why this action on this app must not run yet, or an empty string when it may."""
    if action in GUARDED_ACTIONS and name in critical and confirm != name:
        return f"{name} is a critical app: send its name as confirm to {action} it"
    return ""


class ActionGate:
    """One change at a time, and a cooldown between bulk changes.

    ``try_enter`` returns ``(True, "")`` and holds the gate until ``leave``, or
    ``(False, reason)`` when another change is running or a bulk change ran too recently.
    """

    def __init__(self, cooldown: float = BULK_COOLDOWN_S, clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._cooldown = cooldown
        self._clock = clock
        self._last_bulk: float | None = None

    def try_enter(self, *, bulk: bool) -> tuple[bool, str]:
        if not self._lock.acquire(blocking=False):
            return False, "another change is in progress; try again in a moment"
        if bulk and self._last_bulk is not None:
            wait = self._cooldown - (self._clock() - self._last_bulk)
            if wait > 0:
                self._lock.release()
                return False, f"a bulk change just ran; wait {wait:.0f}s before the next one"
        if bulk:
            self._last_bulk = self._clock()
        return True, ""

    def leave(self) -> None:
        self._lock.release()


def audit(entry: dict, path: Path | None = None) -> None:
    """Append one line to the audit log (owner-only), keeping it from growing without bound.

    Never raises: a log that cannot be written must not stop the change being reported.
    """
    path = path or AUDIT_FILE
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(descriptor)
        if path.stat().st_size > AUDIT_MAX_BYTES:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            temporary = path.with_suffix(".jsonl.tmp")
            temporary.write_text("\n".join(lines[-AUDIT_KEEP_LINES:]) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
    except OSError:
        pass


def audit_tail(count: int = 50, path: Path | None = None) -> list[dict]:
    """The newest ``count`` entries, newest first. Unreadable lines are skipped."""
    path = path or AUDIT_FILE
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            entries.append(item)
        if len(entries) >= max(1, min(count, 200)):
            break
    return entries
