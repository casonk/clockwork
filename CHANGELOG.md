# Changelog

## Unreleased

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
