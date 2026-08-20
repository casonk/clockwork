# Changelog

## Unreleased

- Add `[jobs.launchd]` per-target overrides so one manifest can carry
  systemd-only settings and still install on macOS. Keys there replace the
  job's values when rendering `launchd-user` only; empty values clear a field.
  Previously a job declaring `after` or `randomized_delay_sec` could not render
  for launchd at all, so the settings had to be deleted for every platform.
- `--target` now defaults to the scheduler detected from the platform
  (`launchd-user` on macOS, `systemd-user` on Linux) instead of being required.
  Platforms with no native target -- Windows -- refuse to pick one and say so
  rather than falling back to systemd and writing units nothing will read.
  An explicit `--target` still works everywhere, which is how a non-Linux host
  renders `cron` or systemd units for a container.
- Initial `clockwork` scaffold for declarative cron and systemd rendering.
