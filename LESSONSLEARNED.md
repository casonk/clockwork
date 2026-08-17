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

- Per-job scheduler controls must select exactly one manifest job; invoking a
  whole-manifest installer can overwrite loaded sibling artifacts and leave
  their in-memory scheduler definitions stale.
- Treat web-control state as a commit record: update job and repository toggles
  only when every required scheduler operation succeeds. Resolve private
  `*.local.toml` shadows consistently in individual, repository, and bulk paths.
- Treat an enabled scheduler-target switch as a transaction. If the new target
  fails, re-enable the old target; if rollback also fails, preserve the old
  target identity but record the job as disabled instead of claiming it is live.
- Never overwrite a loaded LaunchAgent and assume launchd noticed. Disk equality
  cannot prove launchd's in-memory definition, so refuse refresh until the job
  is unloaded. Reuse a downstream adapter only after validating its exact label,
  ownership, permissions, executable arguments, and job identity. If Clockwork's
  environment loader is present, require its exact envelope and validate the
  nested argv from its final canonical JSON object.
- A downstream-owned launchd label must be explicit manifest data resolved by
  the same function in both plist rendering and web controls. Parallel label
  prefixes let two control surfaces load duplicate schedules for one workload.
- Keep systemd-only ordering, delayed-login, and randomized-delay fields
  fail-closed. A downstream launchd adapter must expose its replacement delay,
  jitter, and readiness gates in reviewable arguments rather than relying on
  Clockwork to discard unsupported fields.

- Treat launchd as a distinct scheduler rather than transliterating every
  systemd field. Reject delayed-login timers, randomized/accuracy timing,
  ordering dependencies, and system scope when no exact safe mapping exists.
- Never source scheduler environment files as shell code. Load only strict
  `KEY=VALUE` records from regular, owner-owned, owner-only files; reject
  symlinks and permissive modes before executing the workload.
- A tracked macOS service manifest must not assume `/usr/bin/python3` satisfies
  the project runtime. Use a private host-path manifest plus a checked virtual
  environment wrapper, and keep persistent Flask secrets in a required local
  environment file.
- A web process launched from a private virtual environment must invoke
  Clockwork installs through its absolute `sys.executable -m clockwork.cli`;
  launchd's default `PATH` does not include the virtual environment's console
  scripts.

- For compact card grids in the Clockwork web UI, use shrinkable grid tracks
  such as `repeat(2, minmax(0, 1fr))` and put `min-width: 0` on cards and
  nested flex rows. Plain `1fr 1fr` can still overflow on mobile because grid
  items keep intrinsic min-content widths from long labels, hostnames, badges,
  or split action buttons.
- Mobile toolbar links should stay icon-first and single-row. If a top bar has
  more than a few actions, hide long text labels at mobile widths and preserve
  them as `title`/`aria-label` text instead of allowing the header to wrap into
  a tall multi-row toolbar.
- When one downstream repo schedules multiple unattended agentic maintenance
  jobs, keep them as separate manifests and stagger their boot delays instead of
  hiding unrelated workflows behind one timer.
- Admin UIs that rely on Caddy, mTLS, or WireGuard as the real trust boundary
  must still default to loopback in the app itself so a missing proxy layer does
  not silently widen exposure.
- When a Flask UI uses plain HTML forms for state-changing routes, add
  same-origin request checks in the app even if the primary deployment is
  loopback-only.

- Keep tracked example manifests generic: use placeholder paths, usernames, and
  local config locations instead of publishing a real host's filesystem layout
  or service-account names.
- For unattended agentic maintenance jobs, keep provider selection, prompt
  text, dirty-worktree preflight, and run logging in a downstream repo wrapper;
  `clockwork` should schedule that wrapper instead of absorbing agent-specific
  execution logic itself.
- When a tracked manifest is the live control surface for an unattended agentic
  job, surface its provider/model defaults in the web editor instead of hiding
  them in TOML-only environment tables.
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

### 2026-06-07 — User-configurable timing needs interval fields in the web editor

- If a scheduled workflow is meant to be tuned from the Clockwork web UI, expose `OnBootSec` and `OnUnitActiveSec` for interval timers, not only `OnCalendar` for calendar timers.
- Keep workload behavior in the downstream repo; Clockwork should own the schedule manifest, install path, and web-editable cadence.

### 2026-06-12 — Externalized UI state must be seeded before switching service env

- When a Clockwork web tool moves mutable JSON state out of `config/` via an
  environment variable such as `CLOCKWORK_GROCERIES_FILE`, seed the target file
  before restarting the live service.
- If the target lives in a downstream repo's `data/` directory, make that
  runtime state explicitly ignored in the downstream repo so UI edits do not
  become accidental source changes.

### 2026-06-12 — Same-origin POST checks must account for reverse proxies

- When Clockwork runs behind Caddy, Flask may see `request.host` as the
  loopback backend while the browser sends `Origin` or `Referer` for the public
  proxy host.
- Same-origin checks for form POSTs should compare against trusted forwarded
  host headers such as `X-Forwarded-Host`, not only the backend host, or
  legitimate UI actions will fail with `403 Forbidden`.
- If the proxy also sets `Referrer-Policy: no-referrer`, plain HTML form posts
  may arrive without both `Origin` and `Referer`. Accept explicit browser
  `Sec-Fetch-Site: same-origin` metadata as the fallback signal for those
  requests, or Safari and similar clients can still hit false `403` responses.

### 2026-06-12 — Plain HTML form flows need CSRF tokens, not only header heuristics

- Reverse-proxy-aware `Origin` and `Referer` checks are still not enough for
  all browsers: some plain form submits omit both headers and may also omit
  fetch metadata.
- For Clockwork's grocery, shopping, and web-control forms, render a session
  CSRF token into every POST form and accept that token server-side before
  falling back to same-origin header checks.

### 2026-08-16 — Scheduler translations need native-value and state-commit tests

- launchd `Weekday` uses Sunday as `0` or `7`, Monday as `1`, through Saturday
  as `6`; never reuse an unchecked one-based lookup table. Cross-repository
  composition tests should compare the final plist values to the scheduling
  authority, not only compare labels.
- A scheduler UI's stored enabled/target state is a commit record. Update it
  only after every required scheduler action succeeds, and roll back a target
  swap when the new install fails.
- Importing a web/control module must not rewrite tracked manifests by default.
  Keep migration helpers explicit opt-ins so read-only tests and diagnostics
  remain read-only.
