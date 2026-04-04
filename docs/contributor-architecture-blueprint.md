# Contributor Architecture Blueprint

## Purpose

`clockwork` centralizes scheduler description and artifact rendering for the
portfolio. It converts a declarative TOML manifest into reviewable cron and
`systemd` text artifacts without owning the downstream repo's workload logic.

## Main Flow

1. A downstream repo describes one or more jobs in a `clockwork` manifest.
2. `clockwork.manifest` parses the TOML file into validated job models.
3. `clockwork.render` converts those models into concrete `systemd` unit text
   or a cron snippet.
4. `clockwork.cli` either prints the rendered artifacts or writes them into a
   target unit directory or output file.
5. The downstream repo activates those artifacts through its normal operational
   process (`systemctl`, `crontab`, Ansible, host setup docs, and so on).

## Design Boundaries

- Keep repo-specific wrappers, environment bootstrapping, notifications, and
  domain behavior in the downstream repo.
- Keep `clockwork` focused on manifest shape, rendering, and install guidance.
- Favor deterministic text rendering over runtime mutation or hidden side
  effects.

## Downstream Examples

- `examples/traction-control/archility-weekly.toml`
- `examples/intake/report-and-daemon.toml`
- `examples/snowbridge/wireguard-endpoint-monitor.toml`
- `examples/example-scheduler/monthly-controller.toml`

## Validation

- unit tests assert manifest parsing and rendered scheduler output
- CLI tests verify install paths write the expected files
- CI runs lint, formatting checks, and `pytest`
