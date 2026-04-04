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

`clockwork` does not yet try to own:

- repo-specific workload wrappers such as notification hooks or env bootstrap
- direct `systemctl enable --now` or `crontab` mutation
- instance-template units such as `name@.service`

The last item is intentional for the first cut. Repos with parameterized
template units can either render concrete units through `clockwork` or keep the
template local until `clockwork` grows first-class template support.

The tracked manifests under `examples/` use placeholder paths, usernames, and
config locations on purpose. They show the scheduler shape without publishing
machine-specific local paths or host identities.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## CLI

Render planned scheduler artifacts to stdout:

```bash
clockwork render --manifest examples/traction-control/archility-weekly.toml --target systemd-user
clockwork render --manifest examples/example-scheduler/monthly-controller.toml --target cron
```

Write scheduler artifacts to a target directory or file:

```bash
clockwork install --manifest examples/traction-control/archility-weekly.toml --target systemd-user
clockwork install --manifest examples/snowbridge/wireguard-endpoint-monitor.toml --target systemd-system --unit-dir /etc/systemd/system
clockwork install --manifest examples/example-scheduler/monthly-controller.toml --target cron --output /tmp/example-scheduler.crontab
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
- `[jobs.environment]`: service environment variables
- `[jobs.timer]`: optional systemd timer metadata
- `[jobs.cron]`: optional cron rendering metadata

See `examples/` for mappings from current portfolio repos.

## Portfolio Mapping

The first migration targets are the repos where scheduling is already explicit:

- `example-scheduler`: cron examples and systemd-backed refresh flows
- `intake`: generated user-level daemon and report timer units
- `snowbridge`: installed system service + interval timer
- `traction-control`: tracked weekly `archility` timer
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
