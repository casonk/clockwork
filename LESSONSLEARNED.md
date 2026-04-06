# LESSONSLEARNED.md

Tracked durable lessons for `clockwork`.
Unlike `CHATHISTORY.md`, this file should keep only reusable lessons that
should change how future sessions work in this repo.

## How To Use

- Read this file after `AGENTS.md` and before `CHATHISTORY.md` when resuming
  work.
- Add lessons that generalize beyond a single session.
- Keep entries concise and action-oriented.
- Do not use this file for transient status updates or full session logs.

## Lessons

- Keep tracked example manifests generic: use placeholder paths, usernames, and
  local config locations instead of publishing a real host's filesystem layout
  or service-account names.
- Document the repository around its real execution, curation, or integration
  flow instead of only the top-level folder list.
- Keep local-only, private, reference-only, or generated boundaries explicit so
  published or runtime behavior is not confused with offline material or
  non-committable inputs.
- Re-run repo-appropriate validation after changing generated artifacts,
  diagrams, workflows, or other CI-facing files so formatting and compatibility
  issues are caught before push.
- Caddy caches TLS cert files in memory on startup. `systemctl reload` or
  `reload-or-restart` sends SIGHUP which does a graceful config reload but does
  NOT re-read cert files from disk. Always use `systemctl restart caddy` after
  rotating or replacing certs.
- `systemctl` read-only queries (`is-active`, `is-enabled`, `show`) work for
  system-scope units without elevation — regular users can query system units.
  Only write operations (`enable`, `disable`, `daemon-reload`, etc.) need sudo.
- Editable pip installs (`pip install -e .`) place the package in the invoking
  user's site-packages. When the same command is run via `sudo`, root's Python
  environment does not include the user's site-packages. Use a wrapper script
  that sets `PYTHONPATH` to the source directory explicitly, and add that
  wrapper to the sudoers rule rather than the original entry point.
- `toggle_all` in the web UI must exclude the self unit (detected via
  `/proc/self/cgroup`) to prevent the server from disabling itself mid-request.
  Repo-level state must be updated asymmetrically: always mark repos enabled
  during enable-all (even if all jobs were skipped), but only mark disabled
  during disable-all if at least one job was actually toggled.
