# Contributor Architecture Blueprint

## Purpose

`clockwork` centralizes scheduler description and artifact rendering for the
portfolio. It converts a declarative TOML manifest into reviewable cron,
`systemd`, and macOS launchd artifacts without owning the downstream repo's
workload logic.

## Main Flow

1. A downstream repo describes one or more jobs in a `clockwork` manifest.
2. `clockwork.manifest` parses the TOML file into validated job models.
3. `clockwork.render` converts those models into concrete `systemd` unit text,
   an owner-only macOS LaunchAgent plist, or a cron snippet. Launchd uses a
   strict portable subset and rejects lossy mappings.
4. `clockwork.cli` either prints the rendered artifacts or writes them into a
   target unit directory or output file.
5. The downstream repo activates those artifacts through its normal operational
   process (`systemctl`, `launchctl`, `crontab`, Ansible, host setup docs, and
   so on). Clockwork installation never implicitly activates launchd jobs.

## macOS Runtime Boundary

- `launchd-user` defaults to stable `io.github.casonk.clockwork.<job>` labels.
  Downstream jobs may declare an exact label; rendering and web controls share
  one resolver and require it to end with the exact job name.
- Local environment files are loaded at runtime without shell evaluation and
  must be regular, owner-owned, and mode `0600` (or stricter).
- Plists are atomically written with mode `0600`; logs remain in the user's
  `~/Library/Logs/Clockwork` directory.
- The web UI selects launchd on Darwin and systemd elsewhere. Launchd status and
  controls stay in the current GUI user domain; system scope is refused.
- Per-job web actions pass an exact job selector to the CLI, so a change cannot
  rewrite sibling artifacts from a multi-job manifest. State changes are
  committed only after every required scheduler operation succeeds. Enabled
  target switches restore the old target after a failed new-target install;
  failed rollback preserves the old target but records the job as disabled.
- A loaded job is never refreshed in place: disk equality cannot prove
  launchd's in-memory definition. The web action asks the operator to disable
  first. Failed inactive installs restore the prior plist when one existed.
- Systemd ordering, randomized delay, and delayed-login fields remain rejected
  unless a downstream supplies a separate launchd-safe runtime adapter. The web
  UI may reuse such an adapter only from an owner-only exact-label plist; it
  does not synthesize or overwrite the downstream adapter. A private
  environment-file adapter must use Clockwork's exact loader envelope; control
  validation unwraps only its final canonical JSON object before checking the
  downstream runtime wrapper and job identity.
- Caddy/mTLS and mesh exposure remain outside this repo. The Flask process binds
  only to loopback.

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
