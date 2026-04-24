"""Clockwork Configuration Web UI."""

from __future__ import annotations

import json
import os
import re
import secrets
import ssl
import subprocess
from pathlib import Path

import tomlkit
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

try:
    from web.security import same_origin_host, validate_remote_bind
    from web.status_helpers import (
        build_repo_next_run_candidate,
        build_unit_status,
        parse_show_properties,
        select_next_run,
    )
except ModuleNotFoundError:
    from security import same_origin_host, validate_remote_bind
    from status_helpers import (
        build_repo_next_run_candidate,
        build_unit_status,
        parse_show_properties,
        select_next_run,
    )

BASE_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = BASE_DIR / "examples"
STATE_FILE = BASE_DIR / "config" / "web-state.json"


def _resolve_manifest_path(canonical_rel: str) -> Path:
    """Return *.local.toml if it exists alongside the canonical *.toml, else the canonical."""
    canonical = EXAMPLES_DIR / canonical_rel
    local = canonical.with_name(canonical.stem + ".local.toml")
    return local if local.exists() else canonical


def _journal_timestamp(line: str) -> str:
    return line[:25].strip()


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


app = Flask(__name__)
app.secret_key = os.environ.get("CLOCKWORK_WEB_SECRET") or secrets.token_hex(32)


@app.before_request
def _protect_state_changing_requests() -> None:
    """Reject cross-origin state-changing requests."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if source and same_origin_host(source, request.host):
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
                if not job.get("cron") or job.get("timer"):
                    statuses[key] = unit_status(primary_unit(job), scope=job.get("scope", "user"))
                else:
                    statuses[key] = {
                        "active": None,
                        "enabled": None,
                        "active_state": "cron",
                        "enabled_state": "cron",
                    }
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

        if job.get("timer") and request.form.get("on_calendar", "").strip():
            job["timer"]["on_calendar"] = request.form["on_calendar"].strip()
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
        "ExecMainStatus",
        "ExecMainExitTimestamp",
        "NRestarts",
    ]
    # Execution properties live on the service unit, not the timer
    show_unit = unit[:-6] + ".service" if unit.endswith(".timer") else unit
    rc, show_out = _systemctl("show", show_unit, f"--property={','.join(props)}", scope=scope)
    info: dict[str, str] = {}
    for line in show_out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v.strip()

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
            "last_active": info.get("ActiveEnterTimestamp", ""),
            "last_inactive": info.get("InactiveEnterTimestamp", ""),
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
