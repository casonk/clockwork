"""Grandmaster Clock: the apps (long-running services) Clockwork can enable, disable and restart.

Pure selection and view-model helpers; the systemd/launchd calls stay in ``web.app`` so the
page uses exactly the same enable, disable and restart paths as the scheduler page.
"""

from __future__ import annotations

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
    }


def group_by_repo(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["repo"], []).append(row)
    return sorted(grouped.items())
