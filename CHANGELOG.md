# Changelog

## Unreleased

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
