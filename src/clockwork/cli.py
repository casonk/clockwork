"""CLI entry point for clockwork."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .manifest import load_manifest
from .model import Manifest
from .render import (
    render_target,
    write_launchd_files,
    write_rendered_files,
    write_windows_files,
)

TARGETS = ["systemd-user", "systemd-system", "launchd-user", "windows-user", "cron"]


def default_target(platform: str | None = None) -> str | None:
    """Pick the scheduler this machine actually runs, or None if there is none.

    Requiring --target meant every caller hard-coded one, and a command copied
    from a Linux README installed nothing on macOS -- or worse, was reworded to
    launchd-user against a manifest that could not render for it. Detecting the
    platform makes the common case correct by default; --target still overrides
    it, and cron stays opt-in because no platform runs it by default.

    A platform with no native scheduler returns None rather than falling back to
    systemd. Guessing systemd on such a host would write units into
    ~/.config/systemd/user with no systemd to read them: files that look
    installed, never run, and report no error.
    """
    name = sys.platform if platform is None else platform
    if name == "darwin":
        return "launchd-user"
    if name.startswith("linux"):
        return "systemd-user"
    if name in {"win32", "cygwin", "msys"}:
        return "windows-user"
    return None


def resolve_target(explicit: str | None) -> str:
    """Return the target to use, or explain why the platform cannot supply one."""
    if explicit is not None:
        return explicit
    detected = default_target()
    if detected is None:
        raise ValueError(
            f"no scheduler target is known for platform {sys.platform!r}; "
            "pass --target explicitly. clockwork renders systemd, launchd, "
            "Windows Task Scheduler and cron."
        )
    return detected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clockwork",
        description=(
            "Render or install cron, systemd, and macOS launchd scheduler artifacts "
            "from TOML manifests."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser(
        "render", help="Render a manifest to stdout for one target."
    )
    render_parser.add_argument("--manifest", required=True, help="Path to the manifest TOML file.")
    render_parser.add_argument(
        "--target",
        default=None,
        choices=TARGETS,
        help="Scheduler target to render (default: detected from this platform).",
    )
    render_parser.add_argument(
        "--job",
        help="Render only the job whose name exactly matches this value.",
    )
    render_parser.set_defaults(handler=handle_render)

    install_parser = subparsers.add_parser(
        "install", help="Write rendered artifacts to a target directory or file."
    )
    install_parser.add_argument("--manifest", required=True, help="Path to the manifest TOML file.")
    install_parser.add_argument(
        "--target",
        default=None,
        choices=TARGETS,
        help="Scheduler target to install (default: detected from this platform).",
    )
    install_parser.add_argument(
        "--job",
        help="Install only the job whose name exactly matches this value.",
    )
    install_parser.add_argument(
        "--unit-dir",
        help="Override the systemd unit or launchd LaunchAgents directory.",
    )
    install_parser.add_argument(
        "--output",
        help="Output path for cron installs. Defaults to <manifest>.crontab next to the manifest.",
    )
    install_parser.set_defaults(handler=handle_install)
    return parser


def _select_job(manifest: Manifest, job_name: str | None) -> Manifest:
    if job_name is None:
        return manifest
    matches = tuple(job for job in manifest.jobs if job.name == job_name)
    if len(matches) != 1:
        raise ValueError(
            f"manifest must contain exactly one job named {job_name!r}; found {len(matches)}"
        )
    return Manifest(path=manifest.path, jobs=matches)


def _default_unit_dir(target: str) -> Path:
    if target == "systemd-user":
        return Path.home() / ".config" / "systemd" / "user"
    if target == "systemd-system":
        return Path("/etc/systemd/system")
    if target == "launchd-user":
        return Path.home() / "Library" / "LaunchAgents"
    if target == "windows-user":
        # Task Scheduler has no drop-in directory: a task exists once schtasks
        # has registered it. This is a staging directory for the XML, and the
        # install output prints the registration command for each file.
        return Path.home() / "AppData" / "Local" / "Clockwork" / "tasks"
    raise ValueError(f"Unsupported scheduler target: {target!r}")


def handle_render(args: argparse.Namespace) -> int:
    args.target = resolve_target(args.target)
    manifest = _select_job(load_manifest(args.manifest), args.job)
    rendered = render_target(manifest, args.target)
    if args.target == "cron":
        print(next(iter(rendered.values())), end="")
        return 0

    first = True
    for name, content in rendered.items():
        if not first:
            print()
        print(f"# --- {name} ---")
        print(content, end="")
        first = False
    return 0


def handle_install(args: argparse.Namespace) -> int:
    args.target = resolve_target(args.target)
    manifest = _select_job(load_manifest(args.manifest), args.job)
    rendered = render_target(manifest, args.target)

    if args.target == "cron":
        output_path = (
            Path(args.output) if args.output else Path(args.manifest).with_suffix(".crontab")
        )
        output_path.write_text(next(iter(rendered.values())), encoding="utf-8")
        print(f"Wrote cron snippet -> {output_path}")
        print(f"Next step: crontab {output_path}")
        return 0

    if not rendered:
        print(
            f"error: manifest contains no jobs supported by target {args.target}",
            file=sys.stderr,
        )
        return 1

    unit_dir = Path(args.unit_dir) if args.unit_dir else _default_unit_dir(args.target)
    if args.target == "launchd-user" and unit_dir == _default_unit_dir(args.target):
        log_dir = Path.home() / "Library" / "Logs" / "Clockwork"
        if log_dir.is_symlink():
            print(f"error: refusing symlinked log directory: {log_dir}", file=sys.stderr)
            return 1
        try:
            log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except PermissionError:
            print(f"error: cannot create launchd log directory: {log_dir}", file=sys.stderr)
            return 1
    try:
        if args.target == "launchd-user":
            written = write_launchd_files(unit_dir, rendered)
        elif args.target == "windows-user":
            written = write_windows_files(unit_dir, rendered)
        else:
            written = write_rendered_files(unit_dir, rendered)
    except PermissionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    artifact_kind = {
        "launchd-user": "LaunchAgent",
        "windows-user": "task XML",
    }.get(args.target, "unit")
    print(f"Wrote {len(written)} {artifact_kind} file(s) -> {unit_dir}")
    if args.target == "launchd-user":
        print("Next steps (review before running):")
        for path in written:
            print(f"  launchctl bootstrap gui/$(id -u) {path}")
        return 0
    if args.target == "windows-user":
        print("Next steps (review before running):")
        for path in written:
            job_name = path.stem
            print(f'  schtasks /Create /TN "\\Clockwork\\{job_name}" /XML "{path}"')
        return 0
    if args.target == "systemd-user":
        print("Next steps:")
        print("  systemctl --user daemon-reload")
    else:
        print("Next steps:")
        print("  systemctl daemon-reload")

    service_units = [path.name for path in written if path.suffix == ".service"]
    timer_units = [path.name for path in written if path.suffix == ".timer"]
    if args.target == "systemd-user":
        if timer_units:
            print(f"  systemctl --user enable --now {' '.join(timer_units)}")
        elif service_units:
            print(f"  systemctl --user enable --now {' '.join(service_units)}")
    else:
        if timer_units:
            print(f"  systemctl enable --now {' '.join(timer_units)}")
        elif service_units:
            print(f"  systemctl enable --now {' '.join(service_units)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
