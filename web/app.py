"""Clockwork Configuration Web UI."""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
from pathlib import Path

import tomlkit
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for

try:
    from web.security import same_origin_host, validate_remote_bind
    from web.status_helpers import (
        build_cron_status,
        build_repo_next_run_candidate,
        build_unit_status,
        parse_show_properties,
        select_next_run,
    )
except ModuleNotFoundError:
    from security import same_origin_host, validate_remote_bind
    from status_helpers import (
        build_cron_status,
        build_repo_next_run_candidate,
        build_unit_status,
        parse_show_properties,
        select_next_run,
    )

BASE_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = BASE_DIR / "examples"
STATE_FILE = BASE_DIR / "config" / "web-state.json"
GROCERIES_FILE = Path(
    os.environ.get("CLOCKWORK_GROCERIES_FILE", BASE_DIR / "config" / "groceries.json")
)
WIRING_HARNESS_DIR = Path(
    os.environ.get("CLOCKWORK_WIRING_HARNESS_DIR", BASE_DIR.parent / "wiring-harness")
)
INTAKE_DB = Path(
    os.environ.get("CLOCKWORK_INTAKE_DB", BASE_DIR.parent / "intake" / "data" / "intake.db")
)
GROCERY_RULES_FILE = Path(
    os.environ.get("CLOCKWORK_GROCERY_RULES_FILE", BASE_DIR / "config" / "grocery-rules.json")
)
SHOPPING_FILE = Path(
    os.environ.get("CLOCKWORK_SHOPPING_FILE", BASE_DIR / "config" / "shopping.json")
)
SHOPPING_RULES_FILE = Path(
    os.environ.get("CLOCKWORK_SHOPPING_RULES_FILE", BASE_DIR / "config" / "shopping-rules.json")
)
_OLLAMA_BASE = os.environ.get("CREW_CHIEF_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.environ.get("CLOCKWORK_GROCERY_MODEL", "qwen2.5-coder:7b")
WATCH_DIR = Path(os.environ.get("CLOCKWORK_WATCH_DIR", "/mnt/16tb-sata/watch"))
MAGNETO_URL = os.environ.get("CLOCKWORK_MAGNETO_URL", "http://127.0.0.1:5400")
GROCERIES_HISTORY_FILE = Path(
    os.environ.get(
        "CLOCKWORK_GROCERIES_HISTORY_FILE", BASE_DIR / "config" / "groceries-history.jsonl"
    )
)
SHOPPING_HISTORY_FILE = Path(
    os.environ.get(
        "CLOCKWORK_SHOPPING_HISTORY_FILE", BASE_DIR / "config" / "shopping-history.jsonl"
    )
)


def _resolve_manifest_path(canonical_rel: str) -> Path:
    """Return *.local.toml if it exists alongside the canonical *.toml, else the canonical."""
    canonical = EXAMPLES_DIR / canonical_rel
    local = canonical.with_name(canonical.stem + ".local.toml")
    return local if local.exists() else canonical


def _journal_timestamp(line: str) -> str:
    return line[:25].strip()


def _systemd_timestamp_value(value: str) -> str:
    text = str(value or "").strip()
    if not text or text in {"0", "n/a", "[not set]"} or text.startswith("0 "):
        return ""
    return text


def _first_systemd_timestamp(*values: str) -> str:
    for value in values:
        text = _systemd_timestamp_value(value)
        if text:
            return text
    return ""


def _is_zero_error_summary(line: str) -> bool:
    low = line.lower()
    return "done:" in low and "0 error(s)" in low


def _journal_line_level(line: str) -> str:
    low = line.lower()
    if "warning" in low or "non-fatal" in low:
        return "warn"
    if _is_zero_error_summary(line) or "finished" in low or "succeeded" in low:
        return "ok"
    if "failed" in low or "error" in low:
        return "fail"
    if "start" in low:
        return "ok"
    return ""


def _journal_line_counts_as_success(line: str) -> bool:
    low = line.lower()
    return _is_zero_error_summary(line) or "finished" in low or "succeeded" in low


def _request_host_candidates() -> set[str]:
    """Return hostnames that legitimately represent this request.

    Flask sees the backend host when the app is behind Caddy, while browsers
    send Origin/Referer for the public proxy host.
    """
    candidates = {request.host}
    for header in ("X-Forwarded-Host", "X-Original-Host"):
        value = request.headers.get(header, "")
        if not value:
            continue
        candidates.add(value.split(",", 1)[0].strip())
    return {host for host in candidates if host}


app = Flask(__name__)
app.secret_key = os.environ.get("CLOCKWORK_WEB_SECRET") or secrets.token_hex(32)


def _csrf_token() -> str:
    token = session.get("_csrf_token", "")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _has_valid_csrf_token() -> bool:
    expected = session.get("_csrf_token", "")
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    return bool(expected and provided and secrets.compare_digest(expected, provided))


@app.context_processor
def _inject_csrf_token() -> dict[str, object]:
    return {"csrf_token": _csrf_token()}


@app.context_processor
def _inject_nav_categories() -> dict[str, object]:
    return {"nav_categories": _load_nav_categories()}


@app.before_request
def _protect_state_changing_requests() -> None:
    """Reject cross-origin state-changing requests."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if _has_valid_csrf_token():
        return
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if source and any(same_origin_host(source, host) for host in _request_host_candidates()):
        return
    # Safari and plain HTML form posts can omit Origin, and our proxy currently
    # strips Referer with Referrer-Policy: no-referrer. Accept explicit browser
    # same-origin fetch metadata as the fallback signal for real same-site posts.
    if not source and request.headers.get("Sec-Fetch-Site") == "same-origin":
        return
    abort(403, description="Cross-origin state-changing request blocked.")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"repos": {}, "jobs": {}, "global_target": "systemd"}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def repo_enabled(state: dict, repo: str) -> bool:
    return state.get("repos", {}).get(repo, {}).get("enabled", True)


def job_enabled(state: dict, manifest_rel: str, job_name: str) -> bool:
    return state.get("jobs", {}).get(f"{manifest_rel}:{job_name}", {}).get("enabled", True)


def global_target(state: dict) -> str:
    return state.get("global_target", "systemd")


def job_target(state: dict, manifest_rel: str, job_name: str) -> str:
    """Return 'systemd' or 'cron' preference for this job."""
    return (
        state.get("jobs", {})
        .get(f"{manifest_rel}:{job_name}", {})
        .get("target", global_target(state))
    )


# ---------------------------------------------------------------------------
# Cron auto-generation helpers
# ---------------------------------------------------------------------------

_DOW = {"Mon": "1", "Tue": "2", "Wed": "3", "Thu": "4", "Fri": "5", "Sat": "6", "Sun": "0"}


def _calendar_to_cron(on_calendar: str) -> tuple[str, str]:
    """Convert a systemd OnCalendar value to a 5-field cron expression.

    Returns (cron_expression, comment_hint).
    Handles: ``*-*-* HH:MM[:SS]`` and ``DOW *-*-* HH:MM[:SS]``.
    """
    s = on_calendar.strip()
    dow = "*"
    m = re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+", s, re.IGNORECASE)
    if m:
        dow = _DOW[m.group(1).capitalize()]
        s = s[m.end() :]
    m2 = re.match(r"^\*-\*-\*\s+(\d{1,2}):(\d{2})(?::\d{2})?$", s)
    if m2:
        hour, minute = int(m2.group(1)), int(m2.group(2))
        return f"{minute} {hour} * * {dow}", on_calendar.strip()
    raise ValueError(f"Unrecognised OnCalendar: {on_calendar!r}")


def _interval_to_cron(on_unit_active_sec: str) -> tuple[str, str]:
    """Convert a systemd interval duration to a cron expression (best effort)."""
    s = on_unit_active_sec.strip().lower()
    m = re.match(r"^(\d+)\s*([smhd])", s)
    if not m:
        raise ValueError(f"Cannot parse interval: {on_unit_active_sec!r}")
    val, unit = int(m.group(1)), m.group(2)
    if unit == "s":
        return "* * * * *", f"every {val}s (cron resolution: every minute)"
    if unit == "m":
        return ("* * * * *" if val == 1 else f"*/{val} * * * *"), f"every {val}m"
    if unit == "h":
        return ("0 * * * *" if val == 1 else f"0 */{val} * * *"), f"every {val}h"
    if unit == "d":
        return ("0 0 * * *" if val == 1 else f"0 0 */{val} * *"), f"every {val}d"
    return "* * * * *", f"interval {on_unit_active_sec}"


def ensure_cron_sections(examples_dir: Path) -> list[str]:
    """Write ``[jobs.cron]`` sections for any timer job that lacks one.

    Converts the systemd schedule to an equivalent cron expression and
    generates a wrapper command that redirects output to a per-job log file.
    Returns a list of manifest paths (relative to *examples_dir*) that were
    modified.  Safe to call repeatedly — skips jobs that already have cron.
    """
    modified: list[str] = []
    for toml_file in sorted(examples_dir.glob("**/*.toml")):
        doc = tomlkit.loads(toml_file.read_text())
        changed = False
        for job in doc.get("jobs", []):
            if job.get("cron") or not job.get("timer"):
                continue
            timer = job["timer"]
            kind = str(timer.get("kind", ""))
            name = str(job.get("name", "job"))
            working_dir = str(job.get("working_directory", "")).replace("%h", "$HOME")
            exec_start = str(job.get("exec_start", ""))
            scope = str(job.get("scope", "user"))

            try:
                if kind == "calendar":
                    expr, comment = _calendar_to_cron(str(timer.get("on_calendar", "")))
                elif kind == "interval":
                    expr, comment = _interval_to_cron(str(timer.get("on_unit_active_sec", "")))
                else:
                    continue
            except ValueError:
                continue

            log_dir = f"$HOME/.local/share/{name}/runs"
            log_path = f"{log_dir}/cron.log"

            cmd_parts = [f"mkdir -p {log_dir}"]
            if working_dir and working_dir not in ("$HOME", ""):
                cmd_parts.append(f"cd {working_dir}")
            cmd_parts.append(f"{exec_start} >> {log_path} 2>&1")
            cmd = " && ".join(cmd_parts)

            cron_table = tomlkit.table()
            cron_table.add("expression", expr)
            cron_table.add("command", cmd)
            comments: list[str] = [f"Auto-generated from systemd timer: {comment}"]
            if scope == "system":
                comments.append("NOTE: system-scope job — review user context and log path")
            cron_table.add("comments", comments)
            job.add("cron", cron_table)
            changed = True

        if changed:
            toml_file.write_text(tomlkit.dumps(doc))
            modified.append(str(toml_file.relative_to(examples_dir)))
    return modified


# ---------------------------------------------------------------------------
# Manifest scanning
# ---------------------------------------------------------------------------


def _coerce(val) -> str:
    return str(val) if val is not None else ""


def _coerce_list(values) -> list[str]:
    if not values:
        return []
    return [_coerce(item) for item in values]


def _coerce_mapping(values) -> dict[str, str]:
    if not values:
        return {}
    return {str(key): _coerce(value) for key, value in values.items()}


def _environment_key(environment: dict[str, str], suffix: str) -> str:
    matches = sorted(key for key in environment if key.endswith(suffix))
    return matches[0] if matches else ""


def scan_repos() -> dict[str, dict]:
    repos: dict[str, dict] = {}
    all_files = sorted(EXAMPLES_DIR.glob("**/*.toml"))
    local_files = {f for f in all_files if f.name.endswith(".local.toml")}
    # Base files that have a .local.toml sibling are shadowed — skip them
    shadowed = {f.parent / (f.name[: -len(".local.toml")] + ".toml") for f in local_files}
    for toml_file in all_files:
        if toml_file in shadowed:
            continue

        repo_name = toml_file.parent.name
        if repo_name not in repos:
            repos[repo_name] = {"name": repo_name, "manifests": []}

        # Canonical path always uses .toml (stable state key regardless of local/base)
        rel_obj = toml_file.relative_to(EXAMPLES_DIR)
        if toml_file.name.endswith(".local.toml"):
            rel_path = str(rel_obj.parent / (toml_file.name[: -len(".local.toml")] + ".toml"))
        else:
            rel_path = str(rel_obj)

        doc = tomlkit.loads(toml_file.read_text())

        jobs = []
        for job in doc.get("jobs", []):
            timer = job.get("timer")
            cron = job.get("cron")
            environment = _coerce_mapping(job.get("environment"))
            environment_files = _coerce_list(job.get("environment_files"))
            provider_env_key = _environment_key(environment, "_PROVIDER")
            model_env_key = _environment_key(environment, "_MODEL")
            jobs.append(
                {
                    "name": _coerce(job.get("name")),
                    "description": _coerce(job.get("description")),
                    "scope": _coerce(job.get("scope")) or "user",
                    "service_type": _coerce(job.get("service_type")) or "oneshot",
                    "exec_start": _coerce(job.get("exec_start")),
                    "working_directory": _coerce(job.get("working_directory")),
                    "environment": environment,
                    "environment_files": environment_files,
                    "provider_env_key": provider_env_key,
                    "provider_value": environment.get(provider_env_key, ""),
                    "model_env_key": model_env_key,
                    "model_value": environment.get(model_env_key, ""),
                    "timer_name": _coerce(job.get("timer_name")),
                    "service_name": _coerce(job.get("service_name")),
                    "poll_interval": _coerce(job.get("poll_interval")),
                    "timer": {k: _coerce(v) for k, v in timer.items()} if timer else None,
                    "cron": {k: _coerce(v) for k, v in cron.items()} if cron else None,
                    # dual_target = has both systemd timer and cron, user can choose
                    "dual_target": bool(timer) and bool(cron),
                }
            )

        repos[repo_name]["manifests"].append(
            {"path": rel_path, "name": toml_file.stem.removesuffix(".local"), "jobs": jobs}
        )
    return repos


def primary_unit(job: dict) -> str:
    if job.get("timer"):
        return job["timer_name"] or f"{job['name']}.timer"
    return job["service_name"] or f"{job['name']}.service"


# ---------------------------------------------------------------------------
# System interaction
# ---------------------------------------------------------------------------


def _run(*cmd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"


_SYSTEMCTL_WRITE_CMDS = {"enable", "disable", "start", "stop", "restart", "daemon-reload"}


def _systemctl(*args: str, scope: str = "user") -> tuple[int, str]:
    if scope == "user":
        return _run("systemctl", "--user", *args)
    # Read-only queries work without elevation; only writes need sudo -n.
    needs_sudo = bool(args) and args[0] in _SYSTEMCTL_WRITE_CMDS
    if needs_sudo:
        return _run("sudo", "-n", "systemctl", *args)
    return _run("systemctl", *args)


def _self_unit() -> str | None:
    """Return the systemd unit this process is running under, or None.

    Reads /proc/self/cgroup (cgroups v2) which contains a path like
    .../clockwork-web.service — the last .service component is our unit name.
    """
    try:
        text = Path("/proc/self/cgroup").read_text()
        for line in text.splitlines():
            path = line.partition(":")[2]
            for part in reversed(path.split("/")):
                if part.endswith(".service"):
                    return part
    except Exception:
        pass
    return None


def _journalctl(*args: str, scope: str = "user") -> tuple[int, str]:
    flags = ["--user"] if scope == "user" else []
    return _run("journalctl", *flags, *args, timeout=15)


def _log_units(unit: str) -> list[str]:
    """For a timer unit return both the timer and its service; otherwise just the unit."""
    if unit.endswith(".timer"):
        return [unit, unit[:-6] + ".service"]
    return [unit]


def unit_status(unit: str, scope: str = "user") -> dict:
    _, out = _systemctl(
        "show",
        unit,
        "--property=ActiveState,UnitFileState,NextElapseUSecRealtime",
        scope=scope,
    )
    props = parse_show_properties(out)
    if props:
        return build_unit_status(out)

    rc_a, out_a = _systemctl("is-active", unit, scope=scope)
    rc_e, out_e = _systemctl("is-enabled", unit, scope=scope)
    return {
        "active": rc_a == 0 and out_a.strip() == "active",
        "enabled": rc_e == 0 and out_e.strip() in ("enabled", "static", "alias"),
        "active_state": out_a.strip(),
        "enabled_state": out_e.strip(),
        "next_run_text": "",
        "next_run_iso": "",
    }


def fetch_all_statuses(repos: dict) -> dict[str, dict]:
    statuses: dict[str, dict] = {}
    for repo in repos.values():
        for manifest in repo["manifests"]:
            for job in manifest["jobs"]:
                key = f"{manifest['path']}:{job['name']}"
                if job.get("cron") and job.get("target") == "cron":
                    statuses[key] = build_cron_status(job)
                elif not job.get("cron") or job.get("timer"):
                    status = unit_status(primary_unit(job), scope=job.get("scope", "user"))
                    if not status.get("next_run_iso") and job.get("cron"):
                        cron_status = build_cron_status(job)
                        status["next_run_text"] = cron_status.get("next_run_text", "")
                        status["next_run_iso"] = cron_status.get("next_run_iso", "")
                    statuses[key] = status
                else:
                    statuses[key] = build_cron_status(job)
    return statuses


def _resolve_target(job: dict, pref: str) -> str:
    """Given a preference ('systemd'|'cron'), return the actual install target string."""
    has_timer = bool(job.get("timer"))
    has_cron = bool(job.get("cron"))
    if has_timer and has_cron:
        return "cron" if pref == "cron" else f"systemd-{job.get('scope', 'user')}"
    if has_cron:
        return "cron"
    return f"systemd-{job.get('scope', 'user')}"


def do_enable(
    manifest_full_path: Path, job: dict, target_pref: str = "systemd"
) -> list[tuple[str, int, str]]:
    scope = job.get("scope", "user")
    target = _resolve_target(job, target_pref)
    results: list[tuple[str, int, str]] = []

    if scope == "system":
        install_cmd = [
            "sudo",
            "-n",
            "/usr/local/bin/clockwork-system-install",
            "--manifest",
            str(manifest_full_path),
            "--target",
            target,
        ]
    else:
        install_cmd = [
            "clockwork",
            "install",
            "--manifest",
            str(manifest_full_path),
            "--target",
            target,
        ]
    rc, out = _run(*install_cmd, timeout=60)
    results.append((f"clockwork install --target {target}", rc, out))

    if target.startswith("systemd") and rc == 0:
        rc2, out2 = _systemctl("daemon-reload", scope=scope)
        results.append(("systemctl daemon-reload", rc2, out2))
        unit = primary_unit(job)
        rc3, out3 = _systemctl("enable", "--now", unit, scope=scope)
        results.append((f"systemctl enable --now {unit}", rc3, out3))

    return results


def do_disable(job: dict) -> list[tuple[str, int, str]]:
    scope = job.get("scope", "user")
    if job.get("cron") and not job.get("timer"):
        return [("(cron-only — remove from installed .crontab manually)", 0, "")]
    unit = primary_unit(job)
    rc, out = _systemctl("disable", "--now", unit, scope=scope)
    return [(f"systemctl disable --now {unit}", rc, out)]


def _flash_results(results: list[tuple[str, int, str]], prefix: str = "") -> None:
    for cmd, rc, out in results:
        cat = "success" if rc == 0 else "error"
        msg = (f"{prefix}{cmd}" + (f": {out}" if out else "")).strip()
        flash(msg, cat)


# ---------------------------------------------------------------------------
# Routes — index
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    state = load_state()
    repos = scan_repos()
    job_lookup: dict[str, dict] = {}

    for repo_name, repo in repos.items():
        repo["enabled"] = repo_enabled(state, repo_name)
        for manifest in repo["manifests"]:
            for job in manifest["jobs"]:
                job["enabled"] = job_enabled(state, manifest["path"], job["name"])
                job["target"] = job_target(state, manifest["path"], job["name"])
                job_lookup[f"{manifest['path']}:{job['name']}"] = job

    sys_statuses = fetch_all_statuses(repos)
    for repo in repos.values():
        repo["next_run"] = select_next_run(
            [
                build_repo_next_run_candidate(
                    job, sys_statuses.get(f"{manifest['path']}:{job['name']}", {})
                )
                for manifest in repo["manifests"]
                for job in manifest["jobs"]
            ]
        )

    # Count how many jobs have both targets available
    dual_count = sum(1 for j in job_lookup.values() if j.get("dual_target"))

    return render_template(
        "index.html",
        repos=repos,
        sys_statuses=sys_statuses,
        job_data_json=json.dumps(job_lookup),
        global_target=global_target(state),
        dual_count=dual_count,
    )


# ---------------------------------------------------------------------------
# Routes — global controls
# ---------------------------------------------------------------------------


@app.post("/toggle/all")
def toggle_all():
    state = load_state()
    repos = scan_repos()
    self_unit = _self_unit()  # don't let the UI disable itself

    # If any non-self job is enabled → disable all; otherwise enable all
    any_on = any(
        job_enabled(state, m["path"], j["name"])
        for repo in repos.values()
        for m in repo["manifests"]
        for j in m["jobs"]
        if not (self_unit and primary_unit(j) == self_unit)
    )
    new_enabled = not any_on
    global_target(state)

    for repo_name, repo in repos.items():
        toggled_any = False
        for manifest in repo["manifests"]:
            full_path = EXAMPLES_DIR / manifest["path"]
            for job in manifest["jobs"]:
                if self_unit and primary_unit(job) == self_unit:
                    continue  # never disable/enable the running web UI via toggle-all
                toggled_any = True
                key = f"{manifest['path']}:{job['name']}"
                tpref = job_target(state, manifest["path"], job["name"])
                results = do_enable(full_path, job, tpref) if new_enabled else do_disable(job)
                _flash_results(results, prefix=f"[{job['name']}] ")
                state.setdefault("jobs", {}).setdefault(key, {})["enabled"] = new_enabled
        # Always mark enabled on enable-all; skip marking disabled if no jobs were toggled
        # (avoids setting a self-unit-only repo to disabled during disable-all)
        if new_enabled or toggled_any:
            state.setdefault("repos", {})[repo_name] = {"enabled": new_enabled}

    save_state(state)
    return redirect(url_for("index"))


@app.post("/toggle/target/global")
def toggle_target_global():
    state = load_state()
    requested = request.form.get("_t", "").strip()
    new = (
        requested
        if requested in ("systemd", "cron")
        else ("cron" if global_target(state) == "systemd" else "systemd")
    )
    state["global_target"] = new
    save_state(state)
    flash(f"Global target set to {new}.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Routes — per-repo / per-job
# ---------------------------------------------------------------------------


@app.post("/toggle/repo/<repo_name>")
def toggle_repo(repo_name: str):
    state = load_state()
    currently_on = repo_enabled(state, repo_name)
    repos = scan_repos()
    repo = repos.get(repo_name, {})

    for manifest in repo.get("manifests", []):
        full_path = _resolve_manifest_path(manifest["path"])
        for job in manifest["jobs"]:
            key = f"{manifest['path']}:{job['name']}"
            tpref = job_target(state, manifest["path"], job["name"])
            results = do_disable(job) if currently_on else do_enable(full_path, job, tpref)
            _flash_results(results, prefix=f"[{job['name']}] ")
            state.setdefault("jobs", {}).setdefault(key, {})["enabled"] = not currently_on

    state.setdefault("repos", {})[repo_name] = {"enabled": not currently_on}
    save_state(state)
    return redirect(url_for("index"))


@app.post("/toggle/job")
def toggle_job():
    mpath = request.form["manifest_path"]
    jname = request.form["job_name"]
    state = load_state()
    currently_on = job_enabled(state, mpath, jname)

    repos = scan_repos()
    job_data = next(
        (
            j
            for repo in repos.values()
            for m in repo["manifests"]
            if m["path"] == mpath
            for j in m["jobs"]
            if j["name"] == jname
        ),
        None,
    )
    if job_data:
        full_path = _resolve_manifest_path(mpath)
        tpref = job_target(state, mpath, jname)
        results = do_disable(job_data) if currently_on else do_enable(full_path, job_data, tpref)
        _flash_results(results)

    key = f"{mpath}:{jname}"
    state.setdefault("jobs", {}).setdefault(key, {})["enabled"] = not currently_on
    save_state(state)
    return redirect(url_for("index"))


@app.post("/restart/job")
def restart_job():
    mpath = request.form["manifest_path"]
    jname = request.form["job_name"]
    repos = scan_repos()
    job_data = next(
        (
            j
            for repo in repos.values()
            for m in repo["manifests"]
            if m["path"] == mpath
            for j in m["jobs"]
            if j["name"] == jname
        ),
        None,
    )
    if job_data:
        unit = primary_unit(job_data)
        scope = job_data.get("scope", "user")
        rc, out = _systemctl("restart", unit, scope=scope)
        _flash_results([(f"systemctl restart {unit}", rc, out)])
    return redirect(url_for("index"))


@app.post("/toggle/target/job")
def toggle_target_job():
    """Switch a job between systemd and cron, re-installing if enabled."""
    mpath = request.form["manifest_path"]
    jname = request.form["job_name"]
    state = load_state()
    key = f"{mpath}:{jname}"
    current = job_target(state, mpath, jname)
    requested = request.form.get("_t", "").strip()
    new_target = (
        requested
        if requested in ("systemd", "cron")
        else ("cron" if current == "systemd" else "systemd")
    )
    # Validate the job actually supports the requested target
    repos = scan_repos()
    job_data = next(
        (
            j
            for repo in repos.values()
            for m in repo["manifests"]
            if m["path"] == mpath
            for j in m["jobs"]
            if j["name"] == jname
        ),
        None,
    )
    if (
        job_data
        and new_target == "systemd"
        and not job_data.get("timer")
        and job_data.get("service_type") == "oneshot"
    ):
        flash(
            f"{jname}: no [jobs.timer] section — add one to the manifest to use systemd timer.",
            "error",
        )
        return redirect(url_for("index"))

    state.setdefault("jobs", {}).setdefault(key, {})["target"] = new_target

    # If currently enabled, swap the install
    if job_enabled(state, mpath, jname):
        repos = scan_repos()
        job_data = next(
            (
                j
                for repo in repos.values()
                for m in repo["manifests"]
                if m["path"] == mpath
                for j in m["jobs"]
                if j["name"] == jname
            ),
            None,
        )
        if job_data:
            _flash_results(do_disable(job_data))
            full_path = _resolve_manifest_path(mpath)
            _flash_results(do_enable(full_path, job_data, new_target))

    save_state(state)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Routes — edit
# ---------------------------------------------------------------------------


@app.post("/edit/job")
def edit_job():
    mpath = request.form["manifest_path"]
    jname = request.form["job_name"]

    full_path = _resolve_manifest_path(mpath)
    doc = tomlkit.loads(full_path.read_text())

    for job in doc.get("jobs", []):
        if str(job.get("name", "")) != jname:
            continue

        def _set(field: str, _job: dict = job) -> None:
            val = request.form.get(field, "").strip()
            if val:
                _job[field] = val

        _set("description")
        _set("exec_start")
        _set("working_directory")

        if job.get("timer"):
            timer = job["timer"]
            timer_kind = str(timer.get("kind", "")).strip()
            on_calendar = request.form.get("on_calendar", "").strip()
            on_boot_sec = request.form.get("on_boot_sec", "").strip()
            on_unit_active_sec = request.form.get("on_unit_active_sec", "").strip()
            if timer_kind == "calendar" and on_calendar:
                timer["on_calendar"] = on_calendar
            if timer_kind == "interval":
                if on_boot_sec:
                    timer["on_boot_sec"] = on_boot_sec
                if on_unit_active_sec:
                    timer["on_unit_active_sec"] = on_unit_active_sec
        if job.get("cron") and request.form.get("cron_expression", "").strip():
            job["cron"]["expression"] = request.form["cron_expression"].strip()

        provider_env_key = request.form.get("provider_env_key", "").strip()
        model_env_key = request.form.get("model_env_key", "").strip()
        if provider_env_key or model_env_key:
            if not job.get("environment"):
                job["environment"] = tomlkit.table()
            env_table = job["environment"]
            if provider_env_key:
                env_table[provider_env_key] = (
                    request.form.get("provider_value", "").strip() or "auto"
                )
            if model_env_key:
                env_table[model_env_key] = request.form.get("model_value", "").strip()
        break

    full_path.write_text(tomlkit.dumps(doc))
    flash(f"Saved changes to {jname}.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Routes — job details API + log viewer
# ---------------------------------------------------------------------------


@app.get("/api/job-details")
def job_details():
    unit = request.args.get("unit", "").strip()
    scope = request.args.get("scope", "user")
    if not unit:
        return jsonify({"error": "missing unit"}), 400

    props = [
        "Result",
        "ActiveEnterTimestamp",
        "InactiveEnterTimestamp",
        "InactiveExitTimestamp",
        "ExecMainStartTimestamp",
        "ExecMainStatus",
        "ExecMainExitTimestamp",
        "NRestarts",
    ]
    # Execution properties live on the service unit, not the timer
    show_unit = unit[:-6] + ".service" if unit.endswith(".timer") else unit
    rc, show_out = _systemctl("show", show_unit, f"--property={','.join(props)}", scope=scope)
    info = parse_show_properties(show_out)
    timer_info: dict[str, str] = {}
    if unit.endswith(".timer"):
        _, timer_out = _systemctl("show", unit, "--property=LastTriggerUSec", scope=scope)
        timer_info = parse_show_properties(timer_out)

    last_started = _first_systemd_timestamp(
        info.get("ActiveEnterTimestamp", ""),
        info.get("ExecMainStartTimestamp", ""),
        info.get("InactiveExitTimestamp", ""),
        timer_info.get("LastTriggerUSec", ""),
    )
    last_finished = _first_systemd_timestamp(
        info.get("InactiveEnterTimestamp", ""),
        info.get("ExecMainExitTimestamp", ""),
    )

    # Build journal args covering both timer and service units
    unit_flags: list[str] = []
    for u in _log_units(unit):
        unit_flags.extend(["-u", u])

    # Recent journal lines (last 40)
    _, log_out = _journalctl(*unit_flags, "-n", "40", "--no-pager", "-o", "short-iso", scope=scope)
    recent = [ln for ln in log_out.splitlines() if ln.strip()]
    recent_entries = [{"text": ln, "level": _journal_line_level(ln)} for ln in recent]

    # Scan journal for last success and last failure timestamps
    _, scan_out = _journalctl(
        *unit_flags, "-n", "500", "--no-pager", "-o", "short-iso", scope=scope
    )
    last_success: str | None = None
    last_warning: str | None = None
    last_failure: str | None = None
    for line in scan_out.splitlines():
        level = _journal_line_level(line)
        if _journal_line_counts_as_success(line):
            last_success = _journal_timestamp(line)
        elif level == "warn":
            last_warning = _journal_timestamp(line)
        elif level == "fail":
            last_failure = _journal_timestamp(line)

    return jsonify(
        {
            "unit": unit,
            "scope": scope,
            "result": info.get("Result", ""),
            "last_active": last_started,
            "last_inactive": last_finished,
            "last_exit_code": info.get("ExecMainStatus", ""),
            "n_restarts": info.get("NRestarts", ""),
            "last_success": last_success,
            "last_warning": last_warning,
            "last_failure": last_failure,
            "recent_entries": recent_entries,
            "recent_lines": recent,
            "log_url": f"/logs/{unit}?scope={scope}",
        }
    )


@app.get("/logs/<unit>")
def job_logs(unit: str):
    scope = request.args.get("scope", "user")
    n = min(int(request.args.get("n", "500")), 2000)
    unit_flags: list[str] = []
    for u in _log_units(unit):
        unit_flags.extend(["-u", u])
    _, out = _journalctl(*unit_flags, "-n", str(n), "--no-pager", "-o", "short-iso", scope=scope)
    lines = out.splitlines() if out else []
    return render_template("logs.html", unit=unit, scope=scope, lines=lines, n=n)


# ---------------------------------------------------------------------------
# Grocery — intake sync helpers
# ---------------------------------------------------------------------------

# Extra match tokens per grocery item name (lowercase).  Brand names, common
# OCR fragments, and singular/plural alternates that won't appear verbatim.
_GROCERY_ALIASES: dict[str, tuple[str, ...]] = {
    "beer": (
        "bud",
        "coors",
        "miller",
        "heineken",
        "corona",
        "modelo",
        "ale",
        "lager",
        "ipa",
        "stout",
        "pilsner",
        "busch",
        "natty",
        "michelob",
    ),
    "pop": (
        "cola",
        "pepsi",
        "coke",
        "sprite",
        "fanta",
        "soda",
        "7up",
        "canada dry",
        "mountain dew",
        "dr pepper",
        "ginger ale",
        "mt dew",
    ),
    "orange juice": ("tropicana", "simply orange", "minute maid", "oj"),
    "apple juice": ("motts", "mott"),
    "salmon": ("atlantic salmon", "sockeye", "pink salmon", "lox"),
    "shrimp": ("prawn",),
    "instant ramen": ("ramen", "maruchan", "nissin", "top ramen"),
    "avocado": ("hass", "avacado"),
    "potatoes": ("potato", "russet", "yukon"),
    "onions": ("onion",),
    "green beans": ("green bean", "string bean"),
    "salad": ("lettuce", "spinach", "romaine", "arugula", "mixed greens"),
    "bread": ("loaf", "sourdough", "ciabatta", "baguette", "wheat bread", "white bread"),
    "bagels": ("bagel",),
    "chips": ("doritos", "lays", "pringles", "fritos", "cheetos", "tortilla chip"),
    "chocolate": ("choc", "cocoa", "hershey", "ghirardelli", "lindt", "dove"),
    "hot sauce": ("tabasco", "sriracha", "franks", "cholula"),
    "soy sauce": ("soy sauce",),
    "bbq sauce": ("bbq", "barbeque", "barbecue"),
    "caesar dressing": ("caesar",),
    "olive oil": ("evoo",),
    "cream cheese": ("philly", "philadelphia"),
    "coffee": ("folgers", "maxwell", "starbucks", "dunkin", "nescafe", "espresso"),
    "tea": ("lipton", "bigelow", "celestial", "chai"),
    "milk": ("dairy", "whole milk", "2 percent", "skim milk", "almond milk", "oat milk"),
    "cheese": ("cheddar", "mozzarella", "gouda", "swiss", "parmesan", "brie", "feta"),
    "yogurt": ("greek yogurt", "chobani", "fage", "siggi"),
    "mushrooms": ("mushroom",),
    "peppers": ("bell pepper", "jalapeno", "habanero", "serrano"),
    "cherries": ("cherry",),
    "banana": ("bananas",),
    "apple": ("apples", "gala", "fuji", "granny smith", "honeycrisp"),
    "orange": ("oranges", "navel", "clementine", "mandarin"),
    "mango": ("mangos", "mangoes"),
    "kiwi": ("kiwis",),
    "chicken": ("poultry", "breast", "thigh", "drumstick", "tyson", "perdue", "rotisserie"),
    "beef": ("ground beef", "steak", "sirloin", "ribeye", "chuck", "brisket", "angus"),
    "pork": ("bacon", "ham", "pork chop", "tenderloin", "ribs"),
    "sausage": ("bratwurst", "kielbasa", "chorizo", "andouille", "italian sausage"),
    "soup": ("broth", "bouillon", "ramen broth", "chowder", "bisque"),
    "rice": ("white rice", "brown rice", "basmati", "jasmine", "uncle bens", "uncle ben"),
    "corn": (
        "sweet corn",
        "canned corn",
    ),
    "tomato": ("tomatoes", "roma", "cherry tomato", "beefsteak"),
    "garlic": ("minced garlic",),
    "jam": ("jelly", "preserves", "marmalade"),
    "eggs": ("egg",),
    "butter": ("margarine", "country crock", "land o lakes", "lurpak"),
    "mayo": ("mayonnaise", "hellmans", "best foods", "dukes"),
    "honey": ("raw honey",),
    "salt": ("sea salt", "kosher salt", "table salt"),
    "mustard": ("dijon",),
    "ketchup": ("heinz ketchup", "hunts ketchup"),
    "muffins": ("muffin",),
    "sake": ("sake",),
}

# ---------------------------------------------------------------------------
# Shopping — intake sync helpers
# ---------------------------------------------------------------------------

_SHOPPING_ALIASES: dict[str, tuple[str, ...]] = {
    "t-shirt": ("tee", "graphic tee"),
    "jeans": ("denim", "levi", "wrangler"),
    "shorts": ("board short", "gym short"),
    "jacket": ("coat", "parka", "windbreaker"),
    "hoodie": ("sweatshirt", "pullover"),
    "socks": ("sock",),
    "underwear": ("briefs", "boxers"),
    "shoes": ("shoe", "footwear"),
    "sneakers": ("nike", "adidas", "new balance", "jordan", "converse", "vans"),
    "boots": ("boot",),
    "hat": ("cap", "beanie"),
    "belt": ("leather belt",),
    "sweater": ("cardigan", "knit"),
    "pants": ("trousers", "slacks", "chinos"),
    "towels": ("towel",),
    "sheets": ("sheet set", "bed sheet"),
    "pillows": ("pillow",),
    "blanket": ("comforter", "throw"),
    "lamp": ("floor lamp", "table lamp"),
    "shelf": ("shelving", "bookcase"),
    "storage bins": ("storage box", "organizer"),
    "hangers": ("hanger",),
    "detergent": ("tide", "arm hammer", "persil", "gain"),
    "toilet paper": ("charmin", "cottonelle", "scott tissue"),
    "paper towels": ("bounty", "viva"),
    "trash bags": ("hefty", "glad bags"),
    "cleaning supplies": ("clorox", "lysol", "windex"),
    "lightbulbs": ("bulb", "led"),
    "batteries": ("duracell", "energizer"),
    "yoga mat": ("yoga mat",),
    "water bottle": ("hydro flask", "nalgene", "stanley"),
    "gym bag": ("duffel",),
    "backpack": ("rucksack",),
    "tent": ("camping tent",),
    "sleeping bag": ("sleep bag",),
    "sunscreen": ("sunblock", "spf"),
    "bug spray": ("insect repellent", "deet"),
    "bicycle": ("bike",),
    "helmet": ("bike helmet",),
}


def _norm_text(text: str) -> str:
    """Lowercase; keep only letters, digits, and spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def _word_hits(grocery_word: str, desc_words: list[str]) -> bool:
    """Return True when grocery_word has an exact or stem match in desc_words."""
    gw = grocery_word.strip()
    if not gw or len(gw) < 3:
        return gw in desc_words
    for dw in desc_words:
        if gw == dw:
            return True
        # prefix overlap (handles 'potato'/'potatoes', 'mushroom'/'mushrooms')
        shorter, longer = (gw, dw) if len(gw) <= len(dw) else (dw, gw)
        if len(shorter) >= 4 and longer.startswith(shorter):
            return True
        # grocery word is a substring of a dense OCR token like "EGGS6CT"
        if len(gw) >= 4 and gw in dw:
            return True
    return False


def _item_matches_desc(
    item_name: str,
    description: str,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    """Return True when *item_name* matches *description* (a receipt line).

    *aliases* defaults to _GROCERY_ALIASES when None.
    """
    desc_norm = _norm_text(description)
    desc_words = desc_norm.split()

    _aliases = _GROCERY_ALIASES if aliases is None else aliases
    item_lower = item_name.lower()
    candidates: list[str] = [item_lower, *_aliases.get(item_lower, ())]

    for candidate in candidates:
        cand_words = _norm_text(candidate).split()
        if not cand_words:
            continue
        if all(_word_hits(cw, desc_words) for cw in cand_words):
            return True
    return False


def _sync_from_intake(
    db_path: Path,
    data: dict,
    since: str | None = None,
) -> tuple[int, list[str]]:
    """Query intake for grocery receipts and mark matching items as stocked.

    Returns (items_marked, list_of_item_labels) so the caller can flash results.
    *since* is an ISO date string (YYYY-MM-DD); defaults to 30 days ago.

    Per-item receipt dedup: data["seen_receipts"] maps item_name_lower → list of
    receipt filenames already matched.  The same receipt can never re-trigger the
    same item, so manually unchecking an item is always respected — only a new
    receipt (different filename) will re-mark it.
    """
    import sqlite3
    from datetime import date, timedelta

    if not db_path.exists():
        return 0, []

    if since is None:
        since = (date.today() - timedelta(days=30)).isoformat()

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT filename, items_json FROM receipts
        WHERE category LIKE 'grocery%'
          AND items_json IS NOT NULL
          AND items_json != '[]'
          AND scan_date >= ?
        ORDER BY scan_date DESC
        """,
        (since,),
    ).fetchall()
    conn.close()

    # Build per-receipt description lists
    receipt_descs: list[tuple[str, list[str]]] = []
    for filename, items_json in rows:
        descs: list[str] = []
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            for entry in json.loads(items_json):
                desc = str(entry.get("description") or "").strip()
                if desc:
                    descs.append(desc)
        if descs:
            receipt_descs.append((filename, descs))

    # Merge AI-generated rules into a temporary alias table for this run
    ai_aliases: dict[str, tuple[str, ...]] = {}
    rules = load_grocery_rules()
    for item_name, tokens in rules.get("aliases", {}).items():
        if isinstance(tokens, list):
            ai_aliases[item_name.lower()] = tuple(str(t) for t in tokens)

    seen: dict[str, list[str]] = data.setdefault("seen_receipts", {})

    marked: list[str] = []
    for cat in data["categories"]:
        for item in cat["items"]:
            if item["stocked"]:
                continue
            name = item["name"]
            name_lower = name.lower()
            item_seen = set(seen.get(name_lower, []))

            prev = _GROCERY_ALIASES.get(name_lower)
            if name_lower in ai_aliases:
                _GROCERY_ALIASES[name_lower] = (*(prev or ()), *ai_aliases[name_lower])
            try:
                for filename, descs in receipt_descs:
                    if filename in item_seen:
                        continue
                    if any(_item_matches_desc(name, d) for d in descs):
                        item["stocked"] = True
                        item_seen.add(filename)
                        seen[name_lower] = list(item_seen)
                        marked.append(f"{cat['name']}: {name}")
                        break
            finally:
                if prev is None:
                    _GROCERY_ALIASES.pop(name_lower, None)
                elif name_lower in ai_aliases:
                    _GROCERY_ALIASES[name_lower] = prev

    return len(marked), marked


def _sync_shopping_from_intake(
    db_path: Path,
    data: dict,
    since: str | None = None,
) -> tuple[int, list[str]]:
    """Query intake for all receipts and mark matching shopping items as owned.

    Returns (items_marked, list_of_item_labels).
    *since* is an ISO date string (YYYY-MM-DD); defaults to 30 days ago.

    Per-item receipt dedup: data["seen_receipts"] maps item_name_lower → list of
    receipt filenames already matched.  The same receipt can never re-trigger the
    same item, so manually unchecking an item is always respected — only a new
    receipt (different filename) will re-mark it.
    """
    import sqlite3
    from datetime import date, timedelta

    if not db_path.exists():
        return 0, []

    if since is None:
        since = (date.today() - timedelta(days=30)).isoformat()

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT filename, items_json FROM receipts
        WHERE items_json IS NOT NULL
          AND items_json != '[]'
          AND scan_date >= ?
        ORDER BY scan_date DESC
        """,
        (since,),
    ).fetchall()
    conn.close()

    # Build per-receipt description lists
    receipt_descs: list[tuple[str, list[str]]] = []
    for filename, items_json in rows:
        descs: list[str] = []
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            for entry in json.loads(items_json):
                desc = str(entry.get("description") or "").strip()
                if desc:
                    descs.append(desc)
        if descs:
            receipt_descs.append((filename, descs))

    # Merge AI-generated rules into a temporary alias table for this run
    ai_aliases: dict[str, tuple[str, ...]] = {}
    rules = load_shopping_rules()
    for item_name, tokens in rules.get("aliases", {}).items():
        if isinstance(tokens, list):
            ai_aliases[item_name.lower()] = tuple(str(t) for t in tokens)

    seen: dict[str, list[str]] = data.setdefault("seen_receipts", {})

    marked: list[str] = []
    for cat in data["categories"]:
        for item in cat["items"]:
            if item.get("owned", False):
                continue
            name = item["name"]
            name_lower = name.lower()
            item_seen = set(seen.get(name_lower, []))

            prev = _SHOPPING_ALIASES.get(name_lower)
            if name_lower in ai_aliases:
                _SHOPPING_ALIASES[name_lower] = (*(prev or ()), *ai_aliases[name_lower])
            try:
                for filename, descs in receipt_descs:
                    if filename in item_seen:
                        continue
                    if any(_item_matches_desc(name, d, _SHOPPING_ALIASES) for d in descs):
                        item["owned"] = True
                        item_seen.add(filename)
                        seen[name_lower] = list(item_seen)
                        marked.append(f"{cat['name']}: {name}")
                        break
            finally:
                if prev is None:
                    _SHOPPING_ALIASES.pop(name_lower, None)
                elif name_lower in ai_aliases:
                    _SHOPPING_ALIASES[name_lower] = prev

    return len(marked), marked


# ---------------------------------------------------------------------------
# Grocery — AI helpers (Ollama via stdlib urllib)
# ---------------------------------------------------------------------------


def _ollama_available() -> bool:
    """Return True when the local Ollama service is reachable."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{_OLLAMA_BASE}/", timeout=3):
            return True
    except Exception:
        return False


def _ollama_generate(prompt: str, *, timeout: int = 180) -> str:
    """POST to Ollama /api/generate and return the response text."""
    import urllib.request

    payload = json.dumps({"model": _OLLAMA_MODEL, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{_OLLAMA_BASE}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("response", "")


def _extract_json(text: str) -> object:
    """Return the first JSON object or array found in *text* (strips markdown fences)."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return json.loads(text[i:])
            except json.JSONDecodeError:
                pass
    return json.loads(text)


def load_grocery_rules() -> dict:
    if GROCERY_RULES_FILE.exists():
        try:
            return json.loads(GROCERY_RULES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"aliases": {}, "types": {}}


def save_grocery_rules(rules: dict) -> None:
    GROCERY_RULES_FILE.write_text(json.dumps(rules, indent=2))


def load_shopping_rules() -> dict:
    if SHOPPING_RULES_FILE.exists():
        try:
            return json.loads(SHOPPING_RULES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"aliases": {}, "types": {}}


def save_shopping_rules(rules: dict) -> None:
    SHOPPING_RULES_FILE.write_text(json.dumps(rules, indent=2))


def _ai_categorize(data: dict) -> tuple[int, str]:
    """Ask the LLM to assign a grocery type to every uncategorized item.

    Updates *data* in-place, adding/updating a ``type`` field on each item.
    Returns (items_updated, model_used).
    """
    all_items: list[dict] = [
        {"name": item["name"], "category": cat["name"]}
        for cat in data["categories"]
        for item in cat["items"]
        if not item.get("type")
    ]
    if not all_items:
        return 0, _OLLAMA_MODEL

    names_csv = "\n".join(f"- {it['name']} (in {it['category']})" for it in all_items)
    prompt = f"""\
You are a grocery classification assistant.

Classify each item below into exactly one type from this list:
Meat, Seafood, Produce, Dairy, Bakery, Beverages, Spirits, Spices, Condiments, Pantry, Snacks, Frozen

Items:
{names_csv}

Return ONLY a JSON object — no markdown, no explanation:
{{"types": {{"Bread": "Bakery", "Chicken": "Meat", "Beer": "Spirits", ...}}}}
"""
    raw = _ollama_generate(prompt)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "types" not in parsed:
        raise ValueError(f"Unexpected model output: {raw[:200]}")

    type_map: dict[str, str] = {str(k).lower(): str(v) for k, v in parsed["types"].items()}
    updated = 0
    for cat in data["categories"]:
        for item in cat["items"]:
            t = type_map.get(item["name"].lower())
            if t:
                item["type"] = t
                updated += 1
    return updated, _OLLAMA_MODEL


def _ai_generate_rules(data: dict, db_path: Path, since: str | None = None) -> tuple[int, str]:
    """Ask the LLM to generate receipt-to-item alias rules from recent grocery receipts.

    Saves results to GROCERY_RULES_FILE.  Returns (new_aliases_added, model_used).
    """
    import sqlite3
    from datetime import date, timedelta

    if since is None:
        since = (date.today() - timedelta(days=60)).isoformat()

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT items_json FROM receipts
        WHERE category LIKE 'grocery%'
          AND items_json IS NOT NULL
          AND items_json != '[]'
          AND scan_date >= ?
        ORDER BY scan_date DESC
        LIMIT 200
        """,
        (since,),
    ).fetchall()
    conn.close()

    seen: set[str] = set()
    descriptions: list[str] = []
    for (items_json,) in rows:
        try:
            for item in json.loads(items_json):
                d = str(item.get("description") or "").strip()
                if d and d not in seen:
                    seen.add(d)
                    descriptions.append(d)
        except (json.JSONDecodeError, TypeError):
            pass

    if not descriptions:
        return 0, _OLLAMA_MODEL

    all_item_names = [item["name"] for cat in data["categories"] for item in cat["items"]]
    items_csv = ", ".join(all_item_names)
    descs_sample = "\n".join(f"- {d}" for d in descriptions[:80])

    prompt = f"""\
You are a grocery receipt alias generator. Receipt OCR descriptions often contain brand
names, abbreviations, and product codes. Your job: given receipt descriptions and a
grocery item list, produce short keyword tokens that appear in the descriptions and
reliably identify each grocery item.

Receipt OCR descriptions (sample):
{descs_sample}

Grocery item list:
{items_csv}

Rules:
- Only create aliases for items where the descriptions contain useful, non-obvious tokens
- Tokens should be 1–3 words, lowercase
- Skip items whose name already appears verbatim in receipts
- Prefer brand fragments and abbreviations over generic words

Return ONLY a JSON object — no markdown, no explanation:
{{"aliases": {{"Beer": ["bud", "coors", "natty light"], "Cherries": ["blkchry", "cherry"]}}}}
"""
    raw = _ollama_generate(prompt)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "aliases" not in parsed:
        raise ValueError(f"Unexpected model output: {raw[:200]}")

    new_aliases: dict[str, list[str]] = {}
    for item_name, tokens in parsed["aliases"].items():
        if isinstance(tokens, list) and tokens:
            new_aliases[str(item_name)] = [str(t).lower() for t in tokens if t]

    rules = load_grocery_rules()
    existing = rules.get("aliases", {})
    added = 0
    for item_name, tokens in new_aliases.items():
        prev = existing.get(item_name, [])
        merged = list(dict.fromkeys([*prev, *tokens]))  # dedupe, preserve order
        if len(merged) > len(prev):
            added += len(merged) - len(prev)
        existing[item_name] = merged
    rules["aliases"] = existing
    rules["rules_generated_at"] = (
        __import__("datetime").datetime.now().isoformat(timespec="seconds")
    )
    save_grocery_rules(rules)
    return added, _OLLAMA_MODEL


def _ai_categorize_shopping(data: dict) -> tuple[int, str]:
    """Ask the LLM to assign a consumer type to every uncategorized shopping item.

    Updates *data* in-place, adding/updating a ``type`` field on each item.
    Returns (items_updated, model_used).
    """
    all_items: list[dict] = [
        {"name": item["name"], "category": cat["name"]}
        for cat in data["categories"]
        for item in cat["items"]
        if not item.get("type")
    ]
    if not all_items:
        return 0, _OLLAMA_MODEL

    names_csv = "\n".join(f"- {it['name']} (in {it['category']})" for it in all_items)
    prompt = f"""\
You are a consumer shopping classification assistant.

Classify each item below into exactly one type from this list:
Tops, Bottoms, Outerwear, Footwear, Accessories, Underwear, Bedding, Bath, Cleaning, Lighting, Storage, Furniture, Fitness, Camping, Sports, Electronics

Items:
{names_csv}

Return ONLY a JSON object — no markdown, no explanation:
{{"types": {{"T-Shirt": "Tops", "Jeans": "Bottoms", "Tent": "Camping", ...}}}}
"""
    raw = _ollama_generate(prompt)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "types" not in parsed:
        raise ValueError(f"Unexpected model output: {raw[:200]}")

    type_map: dict[str, str] = {str(k).lower(): str(v) for k, v in parsed["types"].items()}
    updated = 0
    for cat in data["categories"]:
        for item in cat["items"]:
            t = type_map.get(item["name"].lower())
            if t:
                item["type"] = t
                updated += 1
    return updated, _OLLAMA_MODEL


def _ai_generate_shopping_rules(
    data: dict, db_path: Path, since: str | None = None
) -> tuple[int, str]:
    """Ask the LLM to generate receipt-to-item alias rules from recent receipts.

    Saves results to SHOPPING_RULES_FILE.  Returns (new_aliases_added, model_used).
    """
    import sqlite3
    from datetime import date, timedelta

    if since is None:
        since = (date.today() - timedelta(days=60)).isoformat()

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT items_json FROM receipts
        WHERE items_json IS NOT NULL
          AND items_json != '[]'
          AND scan_date >= ?
        ORDER BY scan_date DESC
        LIMIT 200
        """,
        (since,),
    ).fetchall()
    conn.close()

    seen: set[str] = set()
    descriptions: list[str] = []
    for (items_json,) in rows:
        try:
            for item in json.loads(items_json):
                d = str(item.get("description") or "").strip()
                if d and d not in seen:
                    seen.add(d)
                    descriptions.append(d)
        except (json.JSONDecodeError, TypeError):
            pass

    if not descriptions:
        return 0, _OLLAMA_MODEL

    all_item_names = [item["name"] for cat in data["categories"] for item in cat["items"]]
    items_csv = ", ".join(all_item_names)
    descs_sample = "\n".join(f"- {d}" for d in descriptions[:80])

    prompt = f"""\
You are a consumer receipt alias generator. Receipt OCR descriptions often contain brand
names, abbreviations, and product codes. Your job: given receipt descriptions and a
shopping item list, produce short keyword tokens that appear in the descriptions and
reliably identify each shopping item.

Receipt OCR descriptions (sample):
{descs_sample}

Shopping item list:
{items_csv}

Rules:
- Only create aliases for items where the descriptions contain useful, non-obvious tokens
- Tokens should be 1–3 words, lowercase
- Skip items whose name already appears verbatim in receipts
- Prefer brand fragments and abbreviations over generic words

Return ONLY a JSON object — no markdown, no explanation:
{{"aliases": {{"T-Shirt": ["tee", "graphic"], "Sneakers": ["nike", "adidas"]}}}}
"""
    raw = _ollama_generate(prompt)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "aliases" not in parsed:
        raise ValueError(f"Unexpected model output: {raw[:200]}")

    new_aliases: dict[str, list[str]] = {}
    for item_name, tokens in parsed["aliases"].items():
        if isinstance(tokens, list) and tokens:
            new_aliases[str(item_name)] = [str(t).lower() for t in tokens if t]

    rules = load_shopping_rules()
    existing = rules.get("aliases", {})
    added = 0
    for item_name, tokens in new_aliases.items():
        prev = existing.get(item_name, [])
        merged = list(dict.fromkeys([*prev, *tokens]))
        if len(merged) > len(prev):
            added += len(merged) - len(prev)
        existing[item_name] = merged
    rules["aliases"] = existing
    rules["rules_generated_at"] = (
        __import__("datetime").datetime.now().isoformat(timespec="seconds")
    )
    save_shopping_rules(rules)
    return added, _OLLAMA_MODEL


# ---------------------------------------------------------------------------
# Routes — groceries
# ---------------------------------------------------------------------------


def _backup_slots(path: Path) -> tuple[Path, Path, Path]:
    """Return (hot, cold, archive) sibling paths for a data file."""
    stem = path.stem
    return (
        path.parent / f"{stem}.hot.json",
        path.parent / f"{stem}.cold.json",
        path.parent / f"{stem}.archive.json",
    )


def _rotate_backup(path: Path) -> None:
    """Rotate hot→cold→archive before a write."""
    hot, cold, archive = _backup_slots(path)
    if cold.exists():
        cold.replace(archive)
    if hot.exists():
        hot.replace(cold)
    if path.exists():
        shutil.copy2(path, hot)


def _log_event(
    history_file: Path,
    action: str,
    *,
    item: str | None = None,
    category: str | None = None,
    detail: str | None = None,
) -> None:
    """Append one JSON line to a history log."""
    entry: dict = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": action,
    }
    if category:
        entry["category"] = category
    if item:
        entry["item"] = item
    if detail:
        entry["detail"] = detail
    with history_file.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _read_history(history_file: Path) -> list[dict]:
    """Return history entries newest-first."""
    if not history_file.exists():
        return []
    entries = []
    for line in history_file.read_text().splitlines():
        line = line.strip()
        if line:
            with contextlib.suppress(json.JSONDecodeError):
                entries.append(json.loads(line))
    entries.reverse()
    return entries


def load_groceries() -> dict:
    if GROCERIES_FILE.exists():
        return json.loads(GROCERIES_FILE.read_text())
    return {"categories": []}


def save_groceries(data: dict) -> None:
    _rotate_backup(GROCERIES_FILE)
    GROCERIES_FILE.write_text(json.dumps(data, indent=2))


def _find_category(data: dict, name: str) -> dict | None:
    return next((c for c in data["categories"] if c["name"] == name), None)


def _backup_slot_info(path: Path) -> tuple[list[bool], list[str]]:
    """Return (exists_list, mtime_list) for hot/cold/archive slots."""
    slots = _backup_slots(path)
    exists = [s.exists() for s in slots]
    mtimes = []
    for s, ex in zip(slots, exists, strict=False):
        if ex:
            ts = datetime.datetime.fromtimestamp(s.stat().st_mtime)
            mtimes.append(ts.strftime("%b %-d %H:%M"))
        else:
            mtimes.append("")
    return exists, mtimes


@app.get("/groceries")
def groceries():
    data = load_groceries()
    total = sum(len(c["items"]) for c in data["categories"])
    stocked = sum(1 for c in data["categories"] for i in c["items"] if i["stocked"])
    rules = load_grocery_rules()
    rules_count = sum(len(v) for v in rules.get("aliases", {}).values())
    backup_exists, backup_mtimes = _backup_slot_info(GROCERIES_FILE)
    return render_template(
        "groceries.html",
        data=data,
        total=total,
        stocked=stocked,
        last_synced_at=data.get("last_synced_at"),
        intake_available=INTAKE_DB.exists(),
        ollama_available=_ollama_available(),
        rules_count=rules_count,
        rules_generated_at=rules.get("rules_generated_at"),
        backup_slots_exist=backup_exists,
        backup_slots_mtime=backup_mtimes,
    )


@app.post("/groceries/add-category")
def groceries_add_category():
    name = request.form.get("category", "").strip()
    if not name:
        flash("Category name cannot be empty.", "error")
        return redirect(url_for("groceries"))
    data = load_groceries()
    if _find_category(data, name):
        flash(f'Category "{name}" already exists.', "error")
        return redirect(url_for("groceries"))
    data["categories"].append({"name": name, "items": []})
    save_groceries(data)
    _log_event(GROCERIES_HISTORY_FILE, "add-category", category=name)
    return redirect(url_for("groceries"))


@app.post("/groceries/delete-category")
def groceries_delete_category():
    name = request.form.get("category", "").strip()
    data = load_groceries()
    data["categories"] = [c for c in data["categories"] if c["name"] != name]
    save_groceries(data)
    _log_event(GROCERIES_HISTORY_FILE, "delete-category", category=name)
    return redirect(url_for("groceries"))


@app.post("/groceries/add-item")
def groceries_add_item():
    category = request.form.get("category", "").strip()
    item_name = request.form.get("item", "").strip()
    if not item_name:
        return redirect(url_for("groceries"))
    data = load_groceries()
    cat = _find_category(data, category)
    if cat is None:
        flash(f'Category "{category}" not found.', "error")
        return redirect(url_for("groceries"))
    if any(i["name"].lower() == item_name.lower() for i in cat["items"]):
        flash(f'"{item_name}" is already in {category}.', "error")
        return redirect(url_for("groceries"))
    cat["items"].append({"name": item_name, "stocked": False})
    save_groceries(data)
    _log_event(GROCERIES_HISTORY_FILE, "add", item=item_name, category=category)
    return redirect(url_for("groceries") + f"#{_slug(category)}")


@app.post("/groceries/toggle-item")
def groceries_toggle_item():
    category = request.form.get("category", "").strip()
    item_name = request.form.get("item", "").strip()
    data = load_groceries()
    cat = _find_category(data, category)
    new_state = None
    if cat:
        for item in cat["items"]:
            if item["name"] == item_name:
                item["stocked"] = not item["stocked"]
                new_state = item["stocked"]
                break
    save_groceries(data)
    if new_state is not None:
        _log_event(
            GROCERIES_HISTORY_FILE,
            "stocked" if new_state else "unstocked",
            item=item_name,
            category=category,
        )
    return redirect(url_for("groceries") + f"#{_slug(category)}")


@app.post("/groceries/delete-item")
def groceries_delete_item():
    category = request.form.get("category", "").strip()
    item_name = request.form.get("item", "").strip()
    data = load_groceries()
    cat = _find_category(data, category)
    if cat:
        cat["items"] = [i for i in cat["items"] if i["name"] != item_name]
    save_groceries(data)
    _log_event(GROCERIES_HISTORY_FILE, "remove", item=item_name, category=category)
    return redirect(url_for("groceries") + f"#{_slug(category)}")


@app.post("/groceries/reset")
def groceries_reset():
    data = load_groceries()
    for cat in data["categories"]:
        for item in cat["items"]:
            item["stocked"] = False
    save_groceries(data)
    _log_event(GROCERIES_HISTORY_FILE, "reset")
    flash("All items marked as not stocked.", "success")
    return redirect(url_for("groceries"))


@app.post("/groceries/sync-intake")
def groceries_sync_intake():
    since = request.form.get("since", "").strip() or None
    data = load_groceries()
    count, marked = _sync_from_intake(INTAKE_DB, data, since=since)
    if not INTAKE_DB.exists():
        flash("Intake DB not found — set CLOCKWORK_INTAKE_DB.", "error")
        return redirect(url_for("groceries"))
    if count == 0:
        flash("No new matches found in recent grocery receipts.", "success")
    else:
        data["last_synced_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_groceries(data)
        _log_event(GROCERIES_HISTORY_FILE, "sync", detail=f"stocked {count}: {', '.join(marked)}")
        flash(
            f"Marked {count} item{'s' if count != 1 else ''} as stocked: {', '.join(marked)}.",
            "success",
        )
    return redirect(url_for("groceries"))


@app.post("/groceries/ai-categorize")
def groceries_ai_categorize():
    if not _ollama_available():
        flash("Ollama is not reachable — start crew-chief before using AI features.", "error")
        return redirect(url_for("groceries"))
    data = load_groceries()
    try:
        count, model = _ai_categorize(data)
    except Exception as exc:
        flash(f"AI categorize failed: {exc}", "error")
        return redirect(url_for("groceries"))
    if count == 0:
        flash("All items already have types — nothing to categorize.", "success")
    else:
        save_groceries(data)
        flash(f"Assigned types to {count} item{'s' if count != 1 else ''} via {model}.", "success")
    return redirect(url_for("groceries"))


@app.post("/groceries/ai-rules")
def groceries_ai_rules():
    if not _ollama_available():
        flash("Ollama is not reachable — start crew-chief before using AI features.", "error")
        return redirect(url_for("groceries"))
    if not INTAKE_DB.exists():
        flash("Intake DB not found — set CLOCKWORK_INTAKE_DB.", "error")
        return redirect(url_for("groceries"))
    data = load_groceries()
    try:
        added, model = _ai_generate_rules(data, INTAKE_DB)
    except Exception as exc:
        flash(f"AI rules generation failed: {exc}", "error")
        return redirect(url_for("groceries"))
    if added == 0:
        flash("No new alias tokens generated — try after more receipts are scanned.", "success")
    else:
        flash(f"Added {added} new alias token{'s' if added != 1 else ''} via {model}.", "success")
    return redirect(url_for("groceries"))


@app.post("/groceries/restore")
def groceries_restore():
    slot = request.form.get("slot", "").strip()
    if slot not in ("hot", "cold", "archive"):
        flash("Invalid backup slot.", "error")
        return redirect(url_for("groceries"))
    hot, cold, archive = _backup_slots(GROCERIES_FILE)
    slot_file = {"hot": hot, "cold": cold, "archive": archive}[slot]
    if not slot_file.exists():
        flash(f"No {slot} backup found.", "error")
        return redirect(url_for("groceries"))
    _rotate_backup(GROCERIES_FILE)
    shutil.copy2(slot_file, GROCERIES_FILE)
    _log_event(GROCERIES_HISTORY_FILE, "restore", detail=f"from {slot}")
    flash(f"Restored groceries from {slot} backup.", "success")
    return redirect(url_for("groceries"))


@app.get("/groceries/history")
def groceries_history():
    entries = _read_history(GROCERIES_HISTORY_FILE)
    # Recurring item summary: count add+remove events per item name
    counts: dict[str, int] = {}
    for e in entries:
        if e.get("action") in ("add", "remove") and e.get("item"):
            counts[e["item"]] = counts.get(e["item"], 0) + 1
    recurring = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:20]
    return render_template("groceries-history.html", entries=entries, recurring=recurring)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# Routes — shopping
# ---------------------------------------------------------------------------


def load_shopping() -> dict:
    if SHOPPING_FILE.exists():
        return json.loads(SHOPPING_FILE.read_text())
    return {"categories": []}


def save_shopping(data: dict) -> None:
    _rotate_backup(SHOPPING_FILE)
    SHOPPING_FILE.write_text(json.dumps(data, indent=2))


def _find_shopping_category(data: dict, name: str) -> dict | None:
    return next((c for c in data["categories"] if c["name"] == name), None)


@app.get("/shopping")
def shopping():
    data = load_shopping()
    total = sum(len(c["items"]) for c in data["categories"])
    owned_count = sum(1 for c in data["categories"] for i in c["items"] if i.get("owned"))
    rules = load_shopping_rules()
    rules_count = sum(len(v) for v in rules.get("aliases", {}).values())
    backup_exists, backup_mtimes = _backup_slot_info(SHOPPING_FILE)
    return render_template(
        "shopping.html",
        data=data,
        total=total,
        owned_count=owned_count,
        last_synced_at=data.get("last_synced_at"),
        intake_available=INTAKE_DB.exists(),
        ollama_available=_ollama_available(),
        rules_count=rules_count,
        rules_generated_at=rules.get("rules_generated_at"),
        backup_slots_exist=backup_exists,
        backup_slots_mtime=backup_mtimes,
    )


@app.post("/shopping/add-category")
def shopping_add_category():
    name = request.form.get("category", "").strip()
    if not name:
        flash("Category name cannot be empty.", "error")
        return redirect(url_for("shopping"))
    data = load_shopping()
    if _find_shopping_category(data, name):
        flash(f'Category "{name}" already exists.', "error")
        return redirect(url_for("shopping"))
    data["categories"].append({"name": name, "items": []})
    save_shopping(data)
    _log_event(SHOPPING_HISTORY_FILE, "add-category", category=name)
    return redirect(url_for("shopping"))


@app.post("/shopping/delete-category")
def shopping_delete_category():
    name = request.form.get("category", "").strip()
    data = load_shopping()
    data["categories"] = [c for c in data["categories"] if c["name"] != name]
    save_shopping(data)
    _log_event(SHOPPING_HISTORY_FILE, "delete-category", category=name)
    return redirect(url_for("shopping"))


@app.post("/shopping/add-item")
def shopping_add_item():
    category = request.form.get("category", "").strip()
    item_name = request.form.get("item", "").strip()
    if not item_name:
        return redirect(url_for("shopping"))
    data = load_shopping()
    cat = _find_shopping_category(data, category)
    if cat is None:
        flash(f'Category "{category}" not found.', "error")
        return redirect(url_for("shopping"))
    if any(i["name"].lower() == item_name.lower() for i in cat["items"]):
        flash(f'"{item_name}" is already in {category}.', "error")
        return redirect(url_for("shopping"))
    cat["items"].append({"name": item_name, "owned": False})
    save_shopping(data)
    _log_event(SHOPPING_HISTORY_FILE, "add", item=item_name, category=category)
    return redirect(url_for("shopping") + f"#{_slug(category)}")


@app.post("/shopping/toggle-item")
def shopping_toggle_item():
    category = request.form.get("category", "").strip()
    item_name = request.form.get("item", "").strip()
    data = load_shopping()
    cat = _find_shopping_category(data, category)
    new_state = None
    if cat:
        for item in cat["items"]:
            if item["name"] == item_name:
                item["owned"] = not item.get("owned", False)
                new_state = item["owned"]
                break
    save_shopping(data)
    if new_state is not None:
        _log_event(
            SHOPPING_HISTORY_FILE,
            "owned" if new_state else "wanted",
            item=item_name,
            category=category,
        )
    return redirect(url_for("shopping") + f"#{_slug(category)}")


@app.post("/shopping/delete-item")
def shopping_delete_item():
    category = request.form.get("category", "").strip()
    item_name = request.form.get("item", "").strip()
    data = load_shopping()
    cat = _find_shopping_category(data, category)
    if cat:
        cat["items"] = [i for i in cat["items"] if i["name"] != item_name]
    save_shopping(data)
    _log_event(SHOPPING_HISTORY_FILE, "remove", item=item_name, category=category)
    return redirect(url_for("shopping") + f"#{_slug(category)}")


@app.post("/shopping/reset")
def shopping_reset():
    data = load_shopping()
    for cat in data["categories"]:
        for item in cat["items"]:
            item["owned"] = False
    save_shopping(data)
    _log_event(SHOPPING_HISTORY_FILE, "reset")
    flash("All items marked as wanted.", "success")
    return redirect(url_for("shopping"))


@app.post("/shopping/sync-intake")
def shopping_sync_intake():
    since = request.form.get("since", "").strip() or None
    data = load_shopping()
    count, marked = _sync_shopping_from_intake(INTAKE_DB, data, since=since)
    if not INTAKE_DB.exists():
        flash("Intake DB not found — set CLOCKWORK_INTAKE_DB.", "error")
        return redirect(url_for("shopping"))
    if count == 0:
        flash("No new matches found in recent receipts.", "success")
    else:
        data["last_synced_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_shopping(data)
        _log_event(SHOPPING_HISTORY_FILE, "sync", detail=f"owned {count}: {', '.join(marked)}")
        flash(
            f"Marked {count} item{'s' if count != 1 else ''} as owned: {', '.join(marked)}.",
            "success",
        )
    return redirect(url_for("shopping"))


@app.post("/shopping/ai-categorize")
def shopping_ai_categorize():
    if not _ollama_available():
        flash("Ollama is not reachable — start crew-chief before using AI features.", "error")
        return redirect(url_for("shopping"))
    data = load_shopping()
    try:
        count, model = _ai_categorize_shopping(data)
    except Exception as exc:
        flash(f"AI categorize failed: {exc}", "error")
        return redirect(url_for("shopping"))
    if count == 0:
        flash("All items already have types — nothing to categorize.", "success")
    else:
        save_shopping(data)
        flash(f"Assigned types to {count} item{'s' if count != 1 else ''} via {model}.", "success")
    return redirect(url_for("shopping"))


@app.post("/shopping/ai-rules")
def shopping_ai_rules():
    if not _ollama_available():
        flash("Ollama is not reachable — start crew-chief before using AI features.", "error")
        return redirect(url_for("shopping"))
    if not INTAKE_DB.exists():
        flash("Intake DB not found — set CLOCKWORK_INTAKE_DB.", "error")
        return redirect(url_for("shopping"))
    data = load_shopping()
    try:
        added, model = _ai_generate_shopping_rules(data, INTAKE_DB)
    except Exception as exc:
        flash(f"AI rules generation failed: {exc}", "error")
        return redirect(url_for("shopping"))
    if added == 0:
        flash("No new alias tokens generated — try after more receipts are scanned.", "success")
    else:
        flash(f"Added {added} new alias token{'s' if added != 1 else ''} via {model}.", "success")
    return redirect(url_for("shopping"))


@app.post("/shopping/restore")
def shopping_restore():
    slot = request.form.get("slot", "").strip()
    if slot not in ("hot", "cold", "archive"):
        flash("Invalid backup slot.", "error")
        return redirect(url_for("shopping"))
    hot, cold, archive = _backup_slots(SHOPPING_FILE)
    slot_file = {"hot": hot, "cold": cold, "archive": archive}[slot]
    if not slot_file.exists():
        flash(f"No {slot} backup found.", "error")
        return redirect(url_for("shopping"))
    _rotate_backup(SHOPPING_FILE)
    shutil.copy2(slot_file, SHOPPING_FILE)
    _log_event(SHOPPING_HISTORY_FILE, "restore", detail=f"from {slot}")
    flash(f"Restored shopping list from {slot} backup.", "success")
    return redirect(url_for("shopping"))


@app.get("/shopping/history")
def shopping_history():
    entries = _read_history(SHOPPING_HISTORY_FILE)
    counts: dict[str, int] = {}
    for e in entries:
        if e.get("action") in ("add", "remove") and e.get("item"):
            counts[e["item"]] = counts.get(e["item"], 0) + 1
    recurring = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:20]
    return render_template("shopping-history.html", entries=entries, recurring=recurring)


# ---------------------------------------------------------------------------
# Routes — to-watch
# ---------------------------------------------------------------------------

# Quality/format tokens stripped before fuzzy-matching filenames.
_QUALITY_RE = re.compile(
    r"\b(?:1080p|720p|2160p|4k|uhd|blu.?ray|bdrip|brrip|webrip|web.?dl|web|hdtv"
    r"|dvdrip|dvdscr|hdrip|hevc|h\.?264|h\.?265|x\.?264|x\.?265|avc|aac|dts"
    r"|dd[p]?5|atmos|10bit|remux|remastered|extended|theatrical|criterion"
    r"|amzn|pmntp|heve|mpeg2|divx|xvid|hd|cam|ts|r5|scr|dsr|pdvd|hq"
    r"|dolby|s\d{2}e\d{2}|season\s*\d+|s\d{2}|bone|ethel|etrg|yts\.\w+|yify"
    r"|sartre|galax[a-z]+|handjob|syncup|byndr|kyogo|yt[a-z]+)\b",
    re.IGNORECASE,
)


def _normalize_media_title(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[\._]", " ", s)  # dots/underscores → space
    s = re.sub(r"\[.*?\]", " ", s)  # [1080p], [YTS.MX], ...
    s = re.sub(r"\([^)]*\)", " ", s)  # (1999), (2008), (2007-2009)
    s = _QUALITY_RE.sub(" ", s)
    s = re.sub(r"\b\d{4}\b", " ", s)  # bare year tokens
    s = re.sub(r"\bwww\.\S+", " ", s)  # www.UIndex.org prefixes
    s = re.sub(r"[^a-z0-9 ]", " ", s)  # strip remaining punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _titles_match(norm_title: str, norm_entry: str) -> bool:
    """Return True if every significant word in norm_title appears in norm_entry."""
    title_words = norm_title.split()
    sig = {w for w in title_words if len(w) >= 3}
    if not sig:
        sig = set(title_words)
    entry_words = set(norm_entry.split())
    return bool(sig) and sig <= entry_words


# Common public announce trackers appended to every constructed magnet link.
_ANNOUNCE_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
)


def _build_magnet(infohash: str, name: str) -> str:
    import urllib.parse as _up

    dn = _up.quote_plus(name)
    tr = "&".join(f"tr={_up.quote_plus(t)}" for t in _ANNOUNCE_TRACKERS)
    return f"magnet:?xt=urn:btih:{infohash}&dn={dn}&{tr}"


def _extract_quality(name: str) -> str:
    for q in ("2160p", "4K", "1080p", "720p", "480p"):
        if q.lower() in name.lower():
            return "4K" if q == "4K" else q
    return ""


def _search_torrents_csv(query: str, limit: int = 20) -> list[dict]:
    """Search torrents-csv.com and return results sorted by seeders."""
    import urllib.parse as _up
    import urllib.request as _ur

    qs = _up.urlencode({"q": query, "size": min(limit, 100)})
    try:
        with _ur.urlopen(f"https://torrents-csv.com/service/search?{qs}", timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []
    results = []
    for t in data.get("torrents") or []:
        ih = str(t.get("infohash") or "").strip()
        name = str(t.get("name") or "").strip()
        if not ih or not name:
            continue
        results.append(
            {
                "name": name,
                "infohash": ih,
                "magnet": _build_magnet(ih, name),
                "seeders": int(t.get("seeders") or 0),
                "leechers": int(t.get("leechers") or 0),
                "size_bytes": int(t.get("size_bytes") or 0),
                "quality": _extract_quality(name),
                "source": "torrents-csv",
            }
        )
    return sorted(results, key=lambda r: r["seeders"], reverse=True)[:limit]


def _search_nyaa(query: str, limit: int = 20) -> list[dict]:
    """Search Nyaa.si RSS and return anime results sorted by seeders."""
    import urllib.parse as _up
    import urllib.request as _ur
    import xml.etree.ElementTree as _ET

    qs = _up.urlencode({"page": "rss", "q": query, "c": "1_0", "f": "0"})
    try:
        with _ur.urlopen(f"https://nyaa.si/?{qs}", timeout=10) as resp:
            raw = resp.read()
    except Exception:
        return []
    try:
        root = _ET.fromstring(raw)
    except _ET.ParseError:
        return []
    ns = {"nyaa": "https://nyaa.si/xmlns/nyaa"}
    channel = root.find("channel")
    if channel is None:
        return []
    results = []
    for item in channel.findall("item")[:limit]:
        title = str(item.findtext("title") or "").strip()
        infohash = str(item.findtext("nyaa:infoHash", "", ns)).strip()
        if not title or not infohash:
            continue
        results.append(
            {
                "name": title,
                "infohash": infohash,
                "magnet": _build_magnet(infohash, title),
                "seeders": int(item.findtext("nyaa:seeders", "0", ns) or 0),
                "leechers": int(item.findtext("nyaa:leechers", "0", ns) or 0),
                "size_bytes": 0,
                "size_str": str(item.findtext("nyaa:size", "", ns)),
                "quality": _extract_quality(title),
                "source": "nyaa",
            }
        )
    return sorted(results, key=lambda r: r["seeders"], reverse=True)


def _proxy_to_magneto(magnet: str) -> tuple[bool, str]:
    """Submit a magnet link to the local Magneto instance via its web UI."""
    import http.cookiejar as _cj
    import urllib.parse as _up
    import urllib.request as _ur

    try:
        jar = _cj.CookieJar()
        opener = _ur.build_opener(_ur.HTTPCookieProcessor(jar))
        resp = opener.open(f"{MAGNETO_URL}/", timeout=8)
        html = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        if not m:
            return False, "No CSRF token found in Magneto response."
        data = _up.urlencode({"magnet": magnet, "csrf_token": m.group(1)}).encode()
        req = _ur.Request(
            f"{MAGNETO_URL}/torrents",
            data=data,
            headers={"Referer": f"{MAGNETO_URL}/"},
        )
        opener.open(req, timeout=10)
        return True, ""
    except Exception as exc:
        return False, str(exc)


@app.get("/to-watch")
def to_watch():
    return render_template("to-watch.html", watch_available=WATCH_DIR.exists())


@app.get("/to-read")
def to_read():
    return render_template("to-read.html")


@app.get("/to-listen")
def to_listen():
    return render_template("to-listen.html")


@app.get("/to-do")
def to_do():
    return render_template("to-do.html")


@app.post("/api/watch-check")
def watch_check():
    """Fuzzy-match a list of titles against entries in WATCH_DIR.

    Request body: {"titles": [{"title": "...", "year": "..."|null}, ...]}
    Response: {"available": bool, "matches": {"<title>": {"found": bool, "match": "<entry name>"|null}}}
    """
    if not WATCH_DIR.exists():
        return jsonify({"available": False, "matches": {}})

    try:
        payload = request.get_json(force=True) or {}
        items = payload.get("titles", [])
    except Exception:
        return jsonify({"available": False, "matches": {}}), 400

    # Build normalised entry index once.
    entries: list[tuple[str, str]] = []
    try:
        for entry in WATCH_DIR.iterdir():
            entries.append((entry.name, _normalize_media_title(entry.name)))
    except OSError:
        return jsonify({"available": False, "matches": {}}), 500

    matches: dict[str, dict] = {}
    for item in items:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        norm = _normalize_media_title(title)
        found_name: str | None = None
        for raw_name, norm_name in entries:
            if _titles_match(norm, norm_name):
                found_name = raw_name
                break
        matches[title] = {"found": found_name is not None, "match": found_name}

    return jsonify({"available": True, "matches": matches})


@app.get("/api/torrent-search")
def torrent_search():
    """Search for torrents matching a title.

    Query params: q (required), cat (optional category name)
    Response: {"results": [{name, infohash, magnet, seeders, leechers, size_bytes, quality, source}]}
    """
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip().lower()
    if not q:
        return jsonify({"results": []})
    if cat == "anime":
        results = _search_nyaa(q)
    else:
        results = _search_torrents_csv(q)
    return jsonify({"results": results})


@app.post("/api/torrent-add")
def torrent_add():
    """Proxy a magnet link to the local Magneto instance.

    Request body: {"magnet": "magnet:?..."}
    Response: {"ok": bool, "error": str}
    """
    try:
        payload = request.get_json(force=True) or {}
        magnet = str(payload.get("magnet") or "").strip()
    except Exception:
        return jsonify({"ok": False, "error": "Invalid request body."}), 400
    if not magnet.startswith("magnet:"):
        return jsonify({"ok": False, "error": "Not a valid magnet link."}), 400
    ok, err = _proxy_to_magneto(magnet)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err}), 502


# ---------------------------------------------------------------------------
# Routes — home portal
# ---------------------------------------------------------------------------

_NAV_CATEGORY_GROUPS: tuple[str, ...] = ("Monitoring", "Pit Box", "Infrastructure", "AI")

_SERVICE_ICONS: dict[str, str] = {
    "clockwork-web": "⏰",
    "tachometer-dashboard": "📊",
    "intake-reports": "📬",
    "snowbridge-filebrowser": "📁",
    "pit-box-webterm": "💻",
    "pit-box-cockpit": "🔧",
    "pit-box-rdp": "🖥️",
    "pit-box-remote-desktop": "🖥️",
    "nordility-web": "🛡️",
    "magneto-web": "🧲",
    "session-control": "🤖",
}

_REPO_GROUP: dict[str, str] = {
    "./util-repos/clockwork": "Clockwork",
    "./util-repos/tachometer": "Monitoring",
    "./util-repos/intake": "Monitoring",
    "./util-repos/snowbridge": "Infrastructure",
    "./util-repos/pit-box": "Pit Box",
    "./util-repos/nordility": "Infrastructure",
    "./util-repos/magneto": "Infrastructure",
    "./util-repos/session-control": "AI",
}

_GROUP_ORDER = ["Clockwork", "Monitoring", "Pit Box", "Infrastructure", "AI"]


def _svc_url(svc: dict) -> str:
    scheme = str(svc.get("url_scheme", "https"))
    host = str(svc.get("hostname", ""))
    port = svc.get("port") or svc.get("port_default")
    mode = str(svc.get("access_mode", ""))
    if scheme == "rdp":
        return f"rdp://{host}:{port}"
    if mode in ("shared-mtls", "snowbridge-mtls"):
        return f"https://{host}"
    return f"https://{host}:{port}" if port and port not in (80, 443) else f"https://{host}"


def _group_anchor(name: str) -> str:
    return name.lower().replace(" ", "-")


def _load_nav_categories() -> list[dict]:
    """Return {label, href} for non-empty portal groups shown in the global nav."""
    local = WIRING_HARNESS_DIR / "services.local.toml"
    base = WIRING_HARNESS_DIR / "services.toml"
    path = local if local.exists() else base
    raw: list = list(tomlkit.loads(path.read_text()).get("services", [])) if path.exists() else []
    populated = {_REPO_GROUP.get(str(s.get("owner_repo", ""))) for s in raw}
    return [
        {"label": g, "href": f"/home#{_group_anchor(g)}"}
        for g in _NAV_CATEGORY_GROUPS
        if g in populated
    ]


def _load_portal_groups() -> list[tuple[str, list[dict]]]:
    local = WIRING_HARNESS_DIR / "services.local.toml"
    base = WIRING_HARNESS_DIR / "services.toml"
    path = local if local.exists() else base
    raw: list = list(tomlkit.loads(path.read_text()).get("services", [])) if path.exists() else []

    # Pre-extract the native RDP URL so it can be merged into the Guacamole card.
    rdp_url = next((_svc_url(s) for s in raw if str(s.get("name", "")) == "pit-box-rdp"), "")

    groups: dict[str, list[dict]] = {g: [] for g in _GROUP_ORDER}
    for svc in raw:
        name = str(svc.get("name", ""))
        if name == "pit-box-rdp":
            continue  # merged into pit-box-remote-desktop card
        group = _REPO_GROUP.get(str(svc.get("owner_repo", "")), "Other")
        card: dict = {
            "name": name,
            "display": str(svc.get("description", name)),
            "url": _svc_url(svc),
            "hostname": str(svc.get("hostname", "")),
            "access_mode": str(svc.get("access_mode", "")),
            "icon": _SERVICE_ICONS.get(name, "🔗"),
        }
        if name == "pit-box-remote-desktop":
            card["rdp_url"] = rdp_url
        groups.setdefault(group, []).append(card)

    groups["Clockwork"].append(
        {
            "name": "clockwork-groceries",
            "display": "Groceries",
            "url": "/groceries",
            "hostname": "",
            "access_mode": "shared-mtls",
            "icon": "🛒",
        }
    )
    groups["Clockwork"].append(
        {
            "name": "clockwork-shopping",
            "display": "Shopping",
            "url": "/shopping",
            "hostname": "",
            "access_mode": "shared-mtls",
            "icon": "🛍️",
        }
    )
    groups["Clockwork"].append(
        {
            "name": "clockwork-to-watch",
            "display": "To Watch",
            "url": "/to-watch",
            "hostname": "",
            "access_mode": "shared-mtls",
            "icon": "📺",
        }
    )
    groups["Clockwork"].append(
        {
            "name": "clockwork-to-read",
            "display": "To Read",
            "url": "/to-read",
            "hostname": "",
            "access_mode": "shared-mtls",
            "icon": "📚",
        }
    )
    groups["Clockwork"].append(
        {
            "name": "clockwork-to-listen",
            "display": "To Listen",
            "url": "/to-listen",
            "hostname": "",
            "access_mode": "shared-mtls",
            "icon": "🎧",
        }
    )
    groups["Clockwork"].append(
        {
            "name": "clockwork-to-do",
            "display": "To Do",
            "url": "/to-do",
            "hostname": "",
            "access_mode": "shared-mtls",
            "icon": "✅",
        }
    )

    ordered = _GROUP_ORDER + sorted(set(groups) - set(_GROUP_ORDER))
    return [(g, cards) for g in ordered if (cards := groups.get(g))]


@app.get("/home")
def home():
    return render_template("home.html", groups=_load_portal_groups())


@app.post("/rebuild/guacamole")
def rebuild_guacamole():
    rc, out = _run("sudo", "-n", "/usr/local/bin/pit-box-rebuild-guacamole", timeout=120)
    if rc == 0:
        flash("Guacamole rebuilt successfully.", "success")
    else:
        flash(f"Rebuild failed (exit {rc}): {out}", "error")
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# SSL / entry point
# ---------------------------------------------------------------------------


def _make_ssl_context() -> ssl.SSLContext | None:
    cert = os.environ.get("CLOCKWORK_WEB_CERT", "")
    key = os.environ.get("CLOCKWORK_WEB_KEY", "")
    ca = os.environ.get("CLOCKWORK_WEB_CA", "")
    if not (cert and key):
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    if ca:
        ctx.load_verify_locations(ca)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = False
    return ctx


# ---------------------------------------------------------------------------
# Startup: auto-generate missing cron sections
# ---------------------------------------------------------------------------

if os.environ.get("CLOCKWORK_WEB_AUTOGENERATE_CRON", "1").lower() in {"1", "true", "yes", "on"}:
    _cron_modified = ensure_cron_sections(EXAMPLES_DIR)
    if _cron_modified:
        print(f"clockwork-web  auto-generated cron sections in: {', '.join(_cron_modified)}")


if __name__ == "__main__":
    host = os.environ.get("CLOCKWORK_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("CLOCKWORK_WEB_PORT", "5000"))
    ssl_context = _make_ssl_context()
    try:
        validate_remote_bind(host, ssl_context)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    scheme = "https" if ssl_context else "http"
    debug = os.environ.get("CLOCKWORK_WEB_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    print(f"clockwork-web  {scheme}://{host}:{port}")
    if ssl_context:
        print("mTLS: clients must present a certificate signed by the configured CA.")
    app.run(
        debug=debug,
        use_reloader=debug,
        host=host,
        port=port,
        ssl_context=ssl_context,
    )
