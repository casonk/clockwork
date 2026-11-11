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

- Never aim a working-tree-changing git command at a checkout that holds someone else's
  uncommitted work. `git apply` without `--cached` or `--check`, `reset --hard`,
  `checkout -- <path>`, `restore`, `clean -f` and `stash` all rewrite it, and `git -C <path>`
  runs them against whichever repository you name, not the directory you are standing in. To
  split a mixed diff into a commit, build the result on a copy (or in a `git worktree` off
  the target ref), hash it with `git hash-object -w`, and stage it with
  `git update-index --cacheinfo`; then export the staged tree (`git checkout-index -a
  --prefix=<dir>/`) and run the tests there. `scripts/guard_destructive_git.py` (wired in
  `.claude/settings.json`) blocks these commands while the tree is dirty.
- A commit made in a clone without the git hooks installed skips every check silently, and CI
  then fails on style the hooks would have fixed. Run `scripts/install-git-hooks.sh` once per
  clone. Black and ruff-format disagree on some layouts (a nested conditional expression is
  one); write a small function instead of fighting both, and verify with the versions pinned
  in `.pre-commit-config.yaml`, not whichever formatter is on the machine.
- A test that publishes or renders through `themes_admin` reads the developer's own rendered
  themes and passes in CI but fails locally. Stub `themes_admin.asset` (or point the themes
  directory at `tmp_path`) in any test that compares output with the vendored files.
- A control that can switch off the way back in must refuse to. The Grandmaster Clock never
  disables or restarts its own web UI, never hides its own Home card, makes critical apps
  (the remote-desktop path) need their name typed, skips them in bulk changes, runs one change
  at a time, spaces bulk changes apart and writes every change to
  `config/grandmaster-audit.jsonl`.
- Pace Wikidata to at least one request a second and TMDB to at least one
  every 0.3 seconds, with jitter. On HTTP 429 or 503, pause that provider for
  at least 15 minutes (honor a longer Retry-After, cap at six hours), store
  the pause in the catalog, and keep filing. Leave `metadata_checked_at` unset
  so the title is tried again after the pause. `HTTPError` is an `OSError`, so
  a blanket connection retry sends the 429 again after a short sleep. Leave
  Magneto and the local genre model unpaced; they are on localhost.
- A user-scope interval timer that only has `OnBootSec` and `OnUnitActiveSec`
  goes idle after the user manager restarts, and also after an `ExecCondition`
  skip. `Persistent=` does not catch up monotonic timers, and `OnUnitActiveSec`
  has no base until the service has actually become active. Give those jobs
  `OnStartupSec` (same delay as `OnBootSec`) and `OnUnitInactiveSec` (same
  interval as `OnUnitActiveSec`). After changing a timer that is already stuck,
  start the service once; a past `OnStartupSec` does not fire on its own.
- A finished download that Transmission is still seeding is registered by
  linking episode files into `shows/<Title>/Season NN/`. Do not rename or move
  the torrent data. Skip any destination that already exists.
- A portfolio portal should federate application-owned UIs rather than become
  the implementation home for every application's domain page. Give each app a
  complete loopback UI and stable API, then let the portal link or reverse
  proxy it and provide optional shared capabilities; retain an in-process page
  only as a bounded migration fallback.
- When a mutation endpoint returns the accepted object's canonical ID but a
  collection is fed by a later scheduled export, merge the accepted object
  into the current view immediately, label it as awaiting synchronization,
  and de-duplicate it by that ID when the export catches up. Guard the async
  merge with the item key that initiated it so navigating meanwhile cannot
  attach the result to a different detail view.
- An AI batch action that normally fills missing values must make replacement
  of user-authored values an explicit, confirmed scope. Enforce that scope both
  when building the model prompt and when applying its response; filtering only
  the prompt can still overwrite an excluded record when item names repeat or
  the model returns an over-complete mapping.
- A test for a view that merges multiple local data sources must redirect every
  source, including read-only companion exports. Redirecting only the writable
  store can leak live owner data into failures and make the suite host-dependent.
  Stub external probe commands in route tests when binary availability is not
  the behavior under test.
- When tests import repo-root modules such as `web` or sibling modules under
  `tests`, include the repository root in pytest's declared `pythonpath`; an
  editable install covers only packages under `src`, and console-entry-point
  path behavior can change across pytest releases and hosted runner images.
- A declared backport dependency does not provide the standard-library module
  name automatically. Every module that uses `tomllib` must fall back to
  `tomli` while Python 3.10 remains in the supported and tested matrix.

- A dashboard reload is not a data refresh. Load an existing generated artifact
  automatically when the page opens instead of presenting reread and regenerate
  as sibling actions. One manual update control should route through the same
  reviewed systemd workload that owns automatic refreshes, show elapsed state,
  poll a cheap artifact-version/status endpoint, and replace the view only after
  a new atomic artifact appears. Bound startup and completion waits and preserve
  the last good view when an update fails.

- A placeholder Home card is portal content, not a theme. Store it in the gitignored
  `config/placeholders.json` (`CLOCKWORK_PLACEHOLDERS_FILE`) and register it from the Themes page,
  which also adds a To Code item with an explicit repository group and repository. Keep it out of
  the published services menu: `_site_service_groups` skips cards marked `placeholder`, because the
  path is not a page yet. A later rename goes through `[labels]`, the same as any other card.

- **Keep the navigation menus in one place when a second consumer appears.** The top bar for
  other services is built from `web/site_chrome.py`, which mirrors `_site_nav.html`;
  `tests/test_site_chrome.py` compares the two so adding a page to one without the other fails.
  A Home card in the Clockwork group does not show up in that menu: `_site_service_groups` skips
  the Clockwork group. A Clockwork page needs its own `SERVICES_MENU` entry and the matching link.

- **Style pages with the system theme tokens; pass category hues through `--hue`.** A literal
  colour ignores the theme rotator and goes unreadable in the light theme. For data that needs
  its own hue (grocery type badges, heat maps), set `--hue` and mix it with tokens
  (`color-mix(in srgb, var(--hue) 20%, var(--surface))` for the fill, `... 55%, var(--text)` for
  the text) so it adapts to every theme. `tests/test_system_theme.py` rejects other literals.

- Keep measured portfolio allocation and user-authored allocation targets as
  separate state. Holdings sync may refresh current percentages, but it must
  preserve targets; calculate percentages against the complete aggregate value
  even when cash or other positions are intentionally omitted from the visible
  ticker list, state that denominator explicitly in the UI, and surface the
  same current/target/delta rollup at both portfolio and category levels. When
  supporting basket-relative targets, keep the category's global basket budget
  explicit and derive each global ticker target from that budget rather than
  persisting a second, drift-prone copy.
- When a page invites editing many related values before committing them, do
  not render each field as an independent reload-on-submit form. Provide one
  visible batch action, validate and resolve every row before mutating any of
  them, keep that action in the sticky header when the edit surface is long,
  route Enter from any field through that same batch form, and warn about
  navigation while unsaved edits remain. After a full-page batch submit,
  restore a stable row anchor and its viewport offset rather than only a raw
  scroll position, because banners and recalculated summaries can change the
  document height.
- When a public-code privacy refactor replaces a private checkout path with a
  neutral placeholder and an environment override, migrate every ignored
  host manifest and reinstall/restart the live service in the same rollout.
  Validate the consumer endpoint against the real generated file; changing the
  tracked default alone can leave a healthy service returning missing-file
  errors indefinitely.
- When consolidating a dedicated page into a shared server-backed list
  template, preserve each action's behavior rather than replacing a mutation
  button with a link to its diagnostic JSON endpoint. List updates should use a
  CSRF-protected POST through the canonical load/save adapter, with regression
  coverage for both the rendered control and the resulting stored state.
- Keep the shared To Code list as an operational view of repo backlogs. Require
  an explicit repository family and repository for each entry; reject unknown
  families in the API, and reconcile each repo's tracked `BACKLOG.md` when work
  is completed because the local list does not write back.
- Separate book identity from download availability: use a catalog API for
  title, author, and edition context, then merge magnet search sources by info
  hash. Keep Magneto handoff limited to magnet links it can accept, and retain
  the existing search source when an optional provider is unavailable.
- Treat archive item discovery as distinct from file availability. Link to the
  item's access page when borrowing or download permissions vary; link a direct
  PDF only when the source itself exposes it.

- Optional personal-media libraries need explicit readable roots. Serve only
  supported files inside those roots, reject hidden or symlinked paths, and
  disable Magneto adds when the root is unavailable because duplicate checks
  cannot be trusted.

- Match external episode guides by exact normalized show title and premiere
  year; ask for a selection when multiple editions remain. Compare only aired
  numbered episodes with playable local files, and recheck library overlap
  when a Magneto add is submitted, not only when search results are rendered.

- Build series library entries from completed Transmission status, then link
  episode files into show/season folders without moving seeding sources.
  De-duplicate by season and episode number so separate releases do not create
  duplicate playable episodes; follow each torrent's current RPC directory
  because post-download filing can move it. Keep search bundles explicit.

- Configure the ECU to recognize active HLS `ffmpeg` processes at live
  priority; it observes process activity rather than requiring an app token
  API. Give each episode render its own ECU lease so unattended work can use
  batch priority and a requested render can use interactive priority. Keep the
  queue coordinator out of stop/start consumers; a pause-file consumer can
  acknowledge only after its render releases the GPU. All queue waits must wake
  for pause requests, including ECU admission, idle, throttled, and disk-floor
  waits. Promote
  on-demand requests while
  preserving title episode order.
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
- A web UI button that restarts the web service hosting the current request
  should return the HTTP response first, then schedule a short delayed
  `systemctl --user restart ...` out of process. A direct blocking restart can
  kill the handler before the browser receives useful feedback.
- When one downstream repo schedules multiple unattended agentic maintenance
  jobs, keep them as separate manifests and stagger their boot delays instead of
  hiding unrelated workflows behind one timer.
- Admin UIs that rely on Caddy, mTLS, or WireGuard as the real trust boundary
  must still default to loopback in the app itself so a missing proxy layer does
  not silently widen exposure.
- When a Clockwork action delegates to a service on another mesh host, accept a
  named target only from a rendered allow-list and require an explicit mTLS
  client identity for remote URLs. Keep the current-host backend on loopback;
  never infer a target from browser input or silently downgrade remote HTTPS.
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
- Portfolio-general WireGuard/VPN and TLS/Caddy lessons live in
  `traction-control/LESSONSLEARNED.md` (agents read it first); the entries here
  are repo-specific. The Caddy "reload does not re-read certs; restart after
  rotation" rule was up-integrated there.
- `systemctl` read-only queries (`is-active`, `is-enabled`, `show`) work for
  system-scope units without elevation — regular users can query system units.
  Only write operations (`enable`, `disable`, `daemon-reload`, etc.) need sudo.
- Do not infer workload inactivity from the exit status of `systemctl
  is-active` alone. A running `Type=oneshot` service reports `activating` while
  its command executes but `systemctl` exits 3; interpret the textual state.
- A portable example manifest may use the portfolio's conventional `%h/git`
  checkout, but deployment must verify `WorkingDirectory` on the actual host.
  Keep a divergent host path in an owner-local systemd drop-in so reinstalling
  the tracked unit does not erase it or publish private topology.
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
- Treat a storage-backend switch as an explicit operator configuration, not an
  import-time default. A compatibility UI may preserve its JSON shape, but it
  must require an installed authority dependency and a named trusted origin
  before routing writes away from the local file store.

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
- Keep dense chart series out of collection endpoints. Persist them with the
  producer artifact, strip them from card/list responses, and expose an exact
  item-detail endpoint so the browser pays the history cost only after a user
  opens that item.
- Treat verbose qualitative profiles like dense history: retain the sourced
  record and freshness in the producer artifact, strip them from collection
  responses, and render them only from the exact-item detail endpoint. Label a
  missing official mission/vision as unavailable instead of deriving one from
  marketing or business-description prose.
- Treat ISO date-only market values as calendar labels, not local instants.
  Format them in UTC in browser charts; otherwise negative-offset time zones
  display the prior trading date.

### 2026-10-18 — Wikidata lookups for media titles

- Ask the SPARQL label service for `"en,mul"`, not `"en"`: many items now keep
  their name only as a multilingual (`mul`) label, and an `en`-only query
  returns the bare QID (Forrest Gump came back as `Q134773`).
- `P577` (publication date) usually includes festival premieres, so the
  earliest year is often one before the wide release. Trust the year in the
  release name and use Wikidata's year only when the name has none.
- `wbsearchentities` matches aliases (searching "Napoleon Dynamite" returns a
  musician); use the full-text `Search` API through `mwapi` with
  `haswbstatement:P31=...` filters, then score candidates by title and year.

### 2026-10-18 — On-demand HLS transcoding

- `h264_nvenc` ignores `-force_key_frames` unless `-forced-idr 1` is set; without
  it segments follow the default GOP (about 10 s) instead of `-hls_time`.
- Seeking with `-ss` before `-i` plus `-output_ts_offset <start>` (not
  `-copyts`) keeps `-t`, `-force_key_frames expr:gte(t,n_forced*N)` and the
  segment timestamps consistent across runs that start at different points.
- Fedora's ffmpeg has no `scale_cuda`; decode with `-hwaccel cuda` and convert
  10-bit to 8-bit with `-vf format=yuv420p` before NVENC.
- Recent Chromium answers `canPlayType("application/vnd.apple.mpegurl")`, and
  its native HLS stalls on seeks into segments still being produced. Prefer
  hls.js whenever Media Source Extensions exist.
- When several transcoder runs share one segment directory, judge a run's
  progress from its own playlist only; counting segments left by earlier seeks
  made a fresh run look finished, so requests waited on a paused process.

### 2026-10-26 — Share portal navigation across web pages

- Keep the site-wide navigation in one template and stylesheet. Repeating icon
  rows in page headers makes destinations inconsistent and crowds small screens.
- Build service links from the wiring-harness registry, and keep its `top_bar`
  pins at the start of the grouped service menu. Do not copy private service
  names or URLs into tracked navigation code.
- Check the open menus at narrow viewport widths as well as the closed bar;
  dropdowns need viewport bounds, scrolling, and keyboard dismissal.
- **Commit after the last clean hook run, and re-stage first.** `pre-commit run` rewrites files after they were staged; committing without re-staging ships the unformatted versions, and CI (which runs the same hooks once) fails on a change a local "Passed" hid. Run the hooks until one run reports no change, then `git add -A`, then commit, and check `git status` is clean afterwards.
- **Write test code in a form `ruff-format` and `black` both leave alone.** They disagree on a long `assert cond, msg  # comment` line and on `code = A + """..."""` spread over lines (ruff wraps it in parentheses, black unwraps it), so the two hooks undo each other on every run. Put the comment on its own line above the assert, and build a long string in its own statement. Keep a `# noqa` on the line ruff reports (or avoid the construct, such as a `lambda` assigned to a name).
- **Dry-run a rule that creates rows on a copy of the real list before applying it.** The To Invest sync's first duplicate rule ("a ticker whose accounts carry two asset-class labels is two instruments") passed its tests and then added 15 rows on live data, because custodians label the same fund differently (VOO is ETFs at Schwab and Stocks at Fidelity). Run the new rule over a copy of `config/to-invest.json` and the holdings export, list exactly what it would add, back the live file up, and only then sync. A symbol is a true duplicate only when it is a different asset (crypto coin versus stock or fund: BTC, T); different labels for one instrument are folded into its single row.
- **A "service is down" symptom can be a dead `systemd --user` manager, not the unit.** Check `journalctl -u user@<uid>` and whether `/run/user/<uid>/bus` accepts connections before debugging the app; `Restart=` does nothing when the manager itself is gone. Caddy then returns an empty 502 for every upstream, which some browsers download instead of rendering.
- **Do not name a scratch script after a stdlib module.** `grp.py` re-ran itself recursively through Flask's `import grp` and fork-bombed the host (see traction-control `AGENTS.md`, Child Session Hygiene). `ecu watch` in central-ecu now logs the offending command lines.

### 2026-11-08 — Import external playlists without surrendering local list authority

- Key imported media by the provider's stable item identity, not its display
  title. Make the first synchronization additive: preserve local categories and
  completion state, do not mirror remote deletions, and leave title collisions
  explicit instead of guessing. This makes a read-only provider grant useful
  without turning an external playlist into the authority for local history.
- In a multi-account OAuth flow, force the provider's account chooser and
  verify the provider's stable account/channel ID after consent. Display names
  can collide, and an existing browser session can silently authorize the
  previously used account. Keep each account's sync failure isolated so an
  expired grant does not block healthy accounts.
- Make bidirectional playlist sync additive and identity-driven in both
  directions. Pull each account's stable item IDs before pushing, insert only
  explicit local provider IDs missing from that account, and never search by a
  display title to guess the remote object. This keeps retries idempotent and
  lets one local union safely synchronize across several accounts without
  mirroring deletions.
