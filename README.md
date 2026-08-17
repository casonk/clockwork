# clockwork

Shared cron, systemd, and macOS launchd scheduling helpers for the portfolio.

`clockwork` exists to pull scheduler concerns out of repo-local shell snippets and
unit-file templates. Repositories should keep their workload logic, env files,
and domain-specific wrappers. `clockwork` owns the declarative job manifest,
rendered scheduler artifacts, and install-time guidance for cron, systemd, and
user LaunchAgents.

## Scope

`clockwork` currently handles:

- concrete `systemd` services and timers for user or system scope
- owner-only macOS user LaunchAgents with deterministic labels and plist output
- interval timers (`OnBootSec` / `OnUnitActiveSec`) and calendar timers
- cron examples rendered from the same manifest shape
- safe file installation into a unit directory or crontab snippet file
- a Flask web UI for browsing, enabling, and disabling jobs across all example manifests
- mTLS provisioning via `scripts/setup-mtls.sh` and `scripts/setup_caddy.py`

`clockwork` does not yet try to own:

- repo-specific workload wrappers such as notification hooks or env bootstrap
- instance-template units such as `name@.service`

Repos with parameterized template units can either render concrete units through
`clockwork` or keep the template local until `clockwork` grows first-class
template support.

The tracked manifests under `examples/` use placeholder paths, usernames, and
config locations on purpose. They show the scheduler shape without publishing
machine-specific local paths or host identities.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Web UI

A Flask app (`web/app.py`) provides a browser interface for enabling, disabling,
and monitoring all jobs defined under `examples/`.  It is served behind Caddy
with mTLS and proxied over WireGuard. The standalone app now binds to
`127.0.0.1` by default; non-loopback exposure is an explicit opt-in.

```bash
# Development
python3 web/app.py

# Production (systemd user service, managed by its own clockwork manifest)
systemctl --user start clockwork-web.service
```

On macOS, the web UI automatically uses `launchctl` for user-scope status and
control instead of invoking Linux-only `systemctl`/`journalctl`. System-scope
launchd operations fail closed because Clockwork does not install LaunchDaemons.
The macOS profile uses loopback port `5001`; port `5000` is commonly occupied by
AirPlay Receiver. Per-job controls install only the exact selected job, including
when a manifest contains multiple jobs, and bulk controls honor a sibling
`*.local.toml` override just like individual controls do. The UI records an
enabled-state change only after every required scheduler command succeeds.
Switching one enabled job between native scheduling and cron is transactional:
Clockwork restores the old target if the new one fails, and marks the job off
if that rollback also fails.

A loaded LaunchAgent is never treated as refreshed merely because its plist on
disk matches the current manifest; launchd may still hold older in-memory
state. Disable it first and then enable it to perform the reviewed
reinstall/bootstrap flow. A systemd-rich
downstream manifest that intentionally fails launchd rendering can be enabled
only when an owner-only, exact-label runtime adapter is already installed;
Traction Control adapters must also identify the exact job through their
reviewed runtime wrapper. When Clockwork wraps that command to load private
environment files, validation accepts only Clockwork's exact four-argument
loader envelope and inspects the nested runner arguments.

If you intentionally expose the Flask app beyond loopback, set
`CLOCKWORK_WEB_ALLOW_REMOTE=1`. Remote exposure without client-authenticated
TLS is blocked unless you also set `CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS=1`.

For agentic maintenance manifests that carry provider/model defaults in
`[jobs.environment]`, the edit modal now surfaces those defaults directly so
they can be changed without manually editing TOML. Optional local-only
credential/profile overrides still belong in `environment_files`. Interval
timers expose their boot delay and repeat cadence in the same edit modal.

System-scope jobs (`scope = "system"`) require elevated writes.  The app uses a
sudoers drop-in (`config/sudoers/clockwork-web`) and a wrapper script
(`config/scripts/clockwork-system-install`) so only the specific commands needed
run as root.

## mTLS and Caddy

mTLS provisioning, Caddyfile generation, and DNS setup live in the
`wiring-harness` repo (`../wiring-harness`).  See its README for the full
provisioning sequence.

## CLI

Render planned scheduler artifacts to stdout:

```bash
clockwork render --manifest examples/archility/archility-weekly.toml --target systemd-user
clockwork render --manifest examples/fedora-debugg/crash-snapshot.toml --target systemd-user
clockwork render --manifest examples/example-scheduler/intraday-snapshots.toml --target systemd-user
clockwork render --manifest examples/example-scheduler/monthly-controller.toml --target cron
clockwork render --manifest examples/shock-relay/gmail-digest.toml --target systemd-user
clockwork render --manifest examples/traction-control/bug-sweep-agentic.toml --target systemd-user
clockwork render --manifest examples/traction-control/template-consolidation-agentic.toml --target systemd-user
clockwork render --manifest examples/traction-control/ci-repair-agentic.toml --target systemd-user
clockwork render --manifest examples/clockwork/clockwork-web.local.toml --target launchd-user
```

Write scheduler artifacts to a target directory or file:

```bash
clockwork install --manifest examples/archility/archility-weekly.toml --target systemd-user
clockwork install --manifest examples/fedora-debugg/crash-snapshot.toml --target systemd-user
clockwork install --manifest examples/snowbridge/wireguard-endpoint-monitor.toml --target systemd-system
clockwork install --manifest examples/shock-relay/gmail-digest.toml --target systemd-user
clockwork install --manifest examples/example-scheduler/monthly-controller.toml --target cron --output /tmp/example-scheduler.crontab
clockwork install --manifest examples/traction-control/bug-sweep-agentic.toml --target systemd-user
clockwork install --manifest examples/traction-control/template-consolidation-agentic.toml --target systemd-user
clockwork install --manifest examples/traction-control/ci-repair-agentic.toml --target systemd-user
clockwork install --manifest examples/clockwork/clockwork-web.local.toml --target launchd-user
# Select one exact job when a manifest contains multiple jobs.
clockwork install --manifest examples/intake/report-and-daemon.toml --target systemd-user --job intake-daemon
```

### macOS Clockwork web LaunchAgent

The tracked macOS manifest is deliberately a non-installable template: it has
no host path or secret. Prepare the private host-specific files first:

```bash
bash scripts/bootstrap_clockwork_web_macos.sh
cp config/macos/clockwork-web.toml.example examples/clockwork/clockwork-web.local.toml
# Replace every /absolute/path placeholder in the ignored local manifest.
clockwork render \
  --manifest examples/clockwork/clockwork-web.local.toml \
  --target launchd-user
clockwork install \
  --manifest examples/clockwork/clockwork-web.local.toml \
  --target launchd-user
```

The bootstrap creates `.venv`, installs the web dependencies, and generates an
owner-only `~/.config/clockwork/clockwork-web.env` without printing its secret.
The runtime wrapper refuses an old/missing Python, missing Flask or tomlkit, a
missing/short secret, or a non-loopback bind. The launchd environment loader
also refuses symlinks, non-regular files, files owned by another user, and any
group/world-accessible secret file. Startup cron autogeneration is disabled by
default so importing or launching the web process cannot mutate tracked
manifests; set `CLOCKWORK_WEB_AUTOGENERATE_CRON=1` only for an explicit,
reviewed migration.

`install` writes an owner-only plist to `~/Library/LaunchAgents` and prints the
exact `launchctl bootstrap` command for review. It does not activate the job.
LaunchAgent stdout/stderr go to `~/Library/Logs/Clockwork` so the web UI can
provide read-only logs without `journalctl`.

Launchd rendering intentionally rejects fields that cannot be mapped without
changing behavior: user/group identity changes, systemd ordering dependencies,
delayed-login `on_boot_sec`, timer randomization/accuracy, non-default install
targets, system scope, and unsupported restart policies. Supported calendar
forms are daily, weekday, and stepped-hour schedules. Supported interval jobs
use `on_unit_active_sec` without `on_boot_sec`.

## Manifest Shape

```toml
[[jobs]]
name = "archility-weekly"
description = "Archility weekly architecture audit and diagram render"
scope = "user"
service_type = "oneshot"
working_directory = "/path/to/portfolio/util-repos/traction-control"
exec_start = "/path/to/portfolio/util-repos/traction-control/scripts/archility-weekly.sh"
after = ["network.target"]
start_limit_interval_sec = "0"
environment_files = ["-/path/to/local-only.env"]

[jobs.environment]
PORTFOLIO_ROOT = "/path/to/portfolio"
ARCHILITY_CMD = "/path/to/venv/bin/archility"

[jobs.timer]
kind = "calendar"
on_calendar = "Sun *-*-* 02:00:00"
persistent = true
```

Supported manifest sections:

- `[[jobs]]`: one logical scheduled job
- service keys support standard execution metadata such as `working_directory`,
  `after`, `wants`, restart policy, environment variables, and
  `start_limit_interval_sec`
- `launchd_label`: optional explicit downstream-owned label. It must be
  dot-qualified and end with the exact job name; rendering and web controls use
  the same resolver.
- `launchd_run_at_load`: optional explicit launchd login-start behavior. It
  does not make `on_boot_sec` portable; that field remains fail-closed.
- `environment_files`: optional systemd `EnvironmentFile=` paths; use `-...`
  when the file is local-only and may not exist on every machine
- `poll_interval`: optional UI hint for long-running daemons with an internal poll loop
- `[jobs.environment]`: service environment variables
- `[jobs.timer]`: optional scheduler metadata; launchd accepts only the strict
  portable subset described above. Ordering dependencies, delayed login, and
  accuracy/randomized delay are never silently dropped.
- `[jobs.cron]`: optional cron rendering metadata

See `examples/` for mappings from current portfolio repos.

## Portfolio Mapping

The first migration targets are the repos where scheduling is already explicit:

- `example-scheduler`: cron examples and systemd-backed refresh flows
  - `examples/example-scheduler/intraday-snapshots.toml` covers the daily intraday snapshot timers plus matching cron snippets
  - `examples/example-scheduler/monthly-controller.toml` keeps the monthly all-accounts cron example
- `intake`: generated user-level daemon and report timer units
- `fedora-debugg`: recurring snapshot workflow that refreshes the tachometer sidecar
- `shock-relay`: shared Gmail notification digest timer
- `snowbridge`: installed system service + interval timer
- `traction-control`: daily governance audit plus daily bug sweep and every-other-day agentic template consolidation and CI repair
- `example-orchestrator`: repo-local service/orchestrator conventions

## Development

```bash
ruff check .
ruff format --check .
black --check --diff .
pytest -q
```

## Contributing

See `CONTRIBUTING.md`.

## License

MIT. See `LICENSE`.
