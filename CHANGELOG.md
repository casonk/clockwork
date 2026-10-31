# Changelog

## Unreleased

- Add the missing **Tradility** card to the Home page, and a test that every page in the top bar's Lists and Media menus has a Home card.

- Also publish the navigation into traction-control's portfolio site store (`~/.config/traction-control/sites/_shared`,
  `PORTFOLIO_STORE` to move it) when it exists, so pages served without a Caddy in front still get the top bar's menus.

- Refresh the vendored portfolio theme: adds the Hulk (purple and green) and Neon themes.

- Add the High contrast white and Liberty themes, rename High contrast to High contrast dark, and
  give the top bar's theme control a drop-down menu of every theme beside the rotator button. The
  bar shows the theme's stripe under it (Liberty has a red, white and blue one). The vendored theme
  and top bar files are refreshed from traction-control.

- Also publish `theme.css` and `theme.js` into the shared directory, so services can use the
  shared colour tokens and apply the saved theme before first paint.
- Refresh the vendored top bar script: it now isolates itself from host-page CSS and sits outside the page body.

- Publish the top bar for other services: Clockwork writes `nav.json` (Home, Clockwork,
  Lists, Media, Services from the wiring-harness registry) and `chrome.js` into the shared
  directory the Caddy serves at `/_portfolio/` (`CLOCKWORK_CHROME_DIR`, default
  `/srv/portfolio-chrome`), refreshed every five minutes while the web service runs.
  `GET /api/site-nav` returns the same document. Services load the bar with
  `<script src="/_portfolio/chrome.js" defer></script>`.

- Adopt the portfolio system themes on every page (Midnight, Daylight, Ember, High
  contrast). Pages use the shared colour tokens instead of per-page palettes, and the
  top bar has a rotator button that steps to the next theme on each click; the choice
  is saved in the browser and follows the system light/dark setting until chosen.
  Grocery and shopping category badges, the Tradility heat map and the nav menus
  adapt to the active theme. The theme files in `web/static/` are vendored from
  traction-control (`docs/system-theme.md`).

- Link **Machine load** from the Home page's Clockwork group.
- Add a **Schedule** page (`/schedule`, JSON at `/api/schedule`): all user and
  system timers in one list ordered by next run, with time until it fires, the
  last run and the last outcome of each job, plus a Home badge and nav link.
- Add a **Machine load** page (`/ecu`, JSON at `/api/ecu`) showing the held lease
  (command, priority, mode, GPU size), GPU memory and processes, and each
  timer job's run/defer verdict from `ecu status --json`. It degrades to an
  explanation when the `ecu` command is missing (`CLOCKWORK_ECU_BIN` overrides
  the lookup).
- Refer to the engine control unit generically in the upscaler README and
  `upscale.env.example` instead of naming its private repository.

- Stop naming a private repository in the To Code source: the add form's extra
  repository suggestions come from `CLOCKWORK_CODE_EXTRA_REPOS` (comma-separated,
  empty by default), and the placeholder and tests use synthetic names.

- Add `start_condition` and `time_limit` job fields, rendered as systemd
  `ExecCondition=` and `TimeoutStartSec=`/`RuntimeMaxSec=`, so a start guard and
  run limit travel with the rendered unit. launchd and Windows refuse them until
  a per-target override clears them.

- Add a P1-P4 priority rating and "in person", "needs a human" and "needs sudo"
  flags to To Code items. Items sort by priority within each repository, can be
  filtered by priority or flag, and are editable through `/to-code/update-item`
  and `/api/code/update-item`. A "Ready for an agent" filter shows items with
  none of the flags.

- Keep the To Code filters across the reload after any row action, and have its
  add form follow the active repo-group and repository filter, narrowing the
  repository suggestions.

- Add a To Code portfolio backlog list grouped by repository family and
  repository, with explicit family selection, local history, and a Home link.
- Cancel a waiting per-render ECU lease when an external pause request arrives,
  allowing the queue coordinator to acknowledge without waiting for admission.
- Wake the upscaler queue for ECU pause requests even while idle, throttled,
  or waiting for disk space, so a waiting lease receives acknowledgment.

- Cooperatively yield the AI upscaler to ECU leases after saving its current
  chunk, release the render subprocess's GPU memory, then resume when the lease
  ends.

- Pin registry services to the `/home` top bar with `top_bar = true` in their
  wiring-harness registry entry; an entry may also set its own `icon`. The
  link URL comes from the registry, and pins stay in the ignored local
  registry instead of this repository.

- Render stepped-minute calendars such as `*:0/5` for `launchd-user` (one
  `Minute` entry per step, no hour) and `windows-user` (a daily trigger that
  repeats every step). A five-minute sampler previously could not be
  installed on macOS at all.
- Add the shared `install-check` workflow to CI, so the package is installed
  from a clean runner on Linux, macOS and Windows and the `clockwork` console
  script is actually executed. `python-ci` imports the source tree on ubuntu
  only, which cannot catch an entry point that fails to resolve once installed.
  Pinned to Python 3.10 and 3.14 — the floor pulls the `tomli` dependency
  marker and the ceiling must not.
- Add a native `windows-user` target rendering Windows Task Scheduler XML, so
  the three desktop platforms are all first-class. Paths are judged with
  `ntpath` rather than the rendering host's rules, `%h` becomes `%USERPROFILE%`
  for Task Scheduler to expand at run time, and files are written as UTF-16
  because `schtasks /Create /XML` rejects UTF-8. `randomized_delay_sec` maps to
  `RandomDelay`, which launchd cannot express.
- Add `[jobs.windows]` overrides, the same mechanism as `[jobs.launchd]`.
- `--target` now detects Windows as well as macOS and Linux.

- Add `[jobs.launchd]` per-target overrides so one manifest can carry
  systemd-only settings and still install on macOS. Keys there replace the
  job's values when rendering `launchd-user` only; empty values clear a field.
  Previously a job declaring `after` or `randomized_delay_sec` could not render
  for launchd at all, so the settings had to be deleted for every platform.
- `--target` now defaults to the scheduler detected from the platform
  (`launchd-user` on macOS, `systemd-user` on Linux, `windows-user` on Windows)
  instead of being required. Platforms with no native target refuse to pick one
  and say so rather than falling back to systemd and writing units nothing will
  read. An explicit `--target` still works everywhere, which is how any host
  renders `cron` or systemd units for a container.
- Initial `clockwork` scaffold for declarative cron and systemd rendering.
