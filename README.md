# clockwork

Shared cron and systemd scheduling helpers for the portfolio.

`clockwork` exists to pull scheduler concerns out of repo-local shell snippets and
unit-file templates. Repositories should keep their workload logic, env files,
and domain-specific wrappers. `clockwork` owns the declarative job manifest,
rendered scheduler artifacts, and install-time guidance for cron plus systemd.

## Scope

`clockwork` currently handles:

- concrete `systemd` services and timers for user or system scope
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

If you intentionally expose the Flask app beyond loopback, set
`CLOCKWORK_WEB_ALLOW_REMOTE=1`. Remote exposure without client-authenticated
TLS is blocked unless you also set `CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS=1`.

For agentic maintenance manifests that carry provider/model defaults in
`[jobs.environment]`, the edit modal now surfaces those defaults directly so
they can be changed without manually editing TOML. Optional local-only
credential/profile overrides still belong in `environment_files`.

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
clockwork render --manifest examples/personal-finance/intraday-snapshots.toml --target systemd-user
clockwork render --manifest examples/personal-finance/monthly-controller.toml --target cron
clockwork render --manifest examples/traction-control/bug-sweep-agentic.toml --target systemd-user
clockwork render --manifest examples/traction-control/template-consolidation-agentic.toml --target systemd-user
clockwork render --manifest examples/traction-control/ci-repair-agentic.toml --target systemd-user
```

Write scheduler artifacts to a target directory or file:

```bash
clockwork install --manifest examples/archility/archility-weekly.toml --target systemd-user
clockwork install --manifest examples/fedora-debugg/crash-snapshot.toml --target systemd-user
clockwork install --manifest examples/snowbridge/wireguard-endpoint-monitor.toml --target systemd-system
clockwork install --manifest examples/personal-finance/monthly-controller.toml --target cron --output /tmp/personal-finance.crontab
clockwork install --manifest examples/traction-control/bug-sweep-agentic.toml --target systemd-user
clockwork install --manifest examples/traction-control/template-consolidation-agentic.toml --target systemd-user
clockwork install --manifest examples/traction-control/ci-repair-agentic.toml --target systemd-user
```

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
- `environment_files`: optional systemd `EnvironmentFile=` paths; use `-...`
  when the file is local-only and may not exist on every machine
- `poll_interval`: optional UI hint for long-running daemons with an internal poll loop
- `[jobs.environment]`: service environment variables
- `[jobs.timer]`: optional systemd timer metadata
- `[jobs.cron]`: optional cron rendering metadata

See `examples/` for mappings from current portfolio repos.

## Portfolio Mapping

The first migration targets are the repos where scheduling is already explicit:

- `personal-finance`: cron examples and systemd-backed refresh flows
  - `examples/personal-finance/intraday-snapshots.toml` covers the daily intraday snapshot timers plus matching cron snippets
  - `examples/personal-finance/monthly-controller.toml` keeps the monthly all-accounts cron example
- `intake`: generated user-level daemon and report timer units
- `fedora-debugg`: recurring snapshot workflow that refreshes the tachometer sidecar
- `snowbridge`: installed system service + interval timer
- `traction-control`: daily governance audit plus daily bug sweep and every-other-day agentic template consolidation and CI repair
- `doseido`: repo-local service/orchestrator conventions

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
