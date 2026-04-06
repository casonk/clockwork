# AGENTS.md — clockwork

## Purpose

`clockwork` is the shared scheduling repo for the portfolio. It owns the
declarative scheduler abstraction used to render or install:

- user-level `systemd` service + timer units
- system-level `systemd` service + timer units
- cron example snippets derived from the same job definitions

Keep the repository focused on scheduler description, rendering, and install
guidance. Workload logic stays in the downstream repo that actually runs the
job.

## Repository Layout

- `src/clockwork/manifest.py`: TOML manifest loading and validation
- `src/clockwork/model.py`: scheduler dataclasses and invariants
- `src/clockwork/render.py`: cron and `systemd` rendering helpers
- `src/clockwork/cli.py`: CLI entry point for render/install flows
- `web/app.py`: Flask web UI for browsing, enabling, and disabling jobs
- `web/templates/`: Jinja2 templates for the web UI
- `examples/`: current portfolio mappings that show how existing repos fit the
  shared scheduler model
- `scripts/setup-mtls.sh`: generates CA, server cert, client cert, and iOS mobileconfig
- `scripts/setup_caddy.py`: installs Caddyfile, copies certs, enables lingering and clockwork-web
- `scripts/export_clockwork_mtls_profile.py`: generates per-device mobileconfigs
- `config/downstream-repos.toml`: known repos with scheduler patterns targeted
  for `clockwork` migration
- `config/sudoers/clockwork-web`: sudoers drop-in granting the web app least-privilege elevation for system-scope jobs
- `config/scripts/clockwork-system-install`: wrapper script used by sudo to invoke clockwork with the correct Python environment as root
- `tests/`: unit coverage for manifest loading, rendering, and install helpers

## Setup And Commands

Recommended repo-root workflow:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
pytest -q
```

Useful commands:

```bash
clockwork render --manifest examples/archility/archility-weekly.toml --target systemd-user
clockwork install --manifest examples/example-scheduler/monthly-controller.toml --target cron --output /tmp/example-scheduler.crontab
```

## Operating Rules

1. Keep scheduler output deterministic. Manifest input should fully explain the
   rendered unit or cron text.
2. Do not move repo-specific env bootstrapping, notification hooks, or domain
   workflow logic into `clockwork` unless they are truly shared scheduler
   concerns.
3. The CLI renders and writes files; the web UI manages live scheduler state via
   `systemctl` (user scope directly, system scope via `sudo -n` with the
   sudoers drop-in).
4. When a downstream repo's scheduler pattern changes, update its example
   manifest or migration note here in the same change.
5. Run repo-appropriate validation after schema or renderer changes.
6. System-scope jobs require the sudoers drop-in and wrapper script to be
   installed.  See `config/sudoers/clockwork-web` and
   `config/scripts/clockwork-system-install`.  Re-run
   `sudo python3 scripts/setup_caddy.py --system-install` after a fresh clone
   to restore the full stack.

## Portfolio References

Portfolio-wide standards live in `./util-repos/traction-control` from the
portfolio root.

Shared implementation repos available portfolio-wide are:

- `./util-repos/archility` for architecture toolchain bootstrap/rendering and
  architecture-documentation drift checks
- `./util-repos/auto-pass` for KeePassXC-backed password management
- `./util-repos/nordility` for VPN switching
- `./util-repos/shock-relay` for external messaging
- `./util-repos/short-circuit` for WireGuard VPN setup and configuration
- `./util-repos/snowbridge` for SMB-based file sharing
- `./util-repos/dyno-lab` for shared test fixtures and helpers
- `./util-repos/clockwork` for shared cron and `systemd` scheduling

When another repo needs portable scheduler rendering or install guidance,
prefer integrating with `clockwork` instead of adding another repo-local unit
template or cron snippet generator.

## Agent Memory

Use `./LESSONSLEARNED.md` as the tracked durable lessons file for this repo.
Use `./CHATHISTORY.md` as the local-only handoff file for this repo.

- `LESSONSLEARNED.md` is tracked and should capture reusable lessons only.
- `CHATHISTORY.md` is gitignored and must not be committed.
- Read `LESSONSLEARNED.md` and `CHATHISTORY.md` after `AGENTS.md` when resuming
  work.
