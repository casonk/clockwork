"""CLI entry point for clockwork."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .manifest import load_manifest
from .render import render_target, write_rendered_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clockwork",
        description="Render or install cron and systemd scheduler artifacts from TOML manifests.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser(
        "render", help="Render a manifest to stdout for one target."
    )
    render_parser.add_argument("--manifest", required=True, help="Path to the manifest TOML file.")
    render_parser.add_argument(
        "--target",
        required=True,
        choices=["systemd-user", "systemd-system", "cron"],
        help="Scheduler target to render.",
    )
    render_parser.set_defaults(handler=handle_render)

    install_parser = subparsers.add_parser(
        "install", help="Write rendered artifacts to a target directory or file."
    )
    install_parser.add_argument("--manifest", required=True, help="Path to the manifest TOML file.")
    install_parser.add_argument(
        "--target",
        required=True,
        choices=["systemd-user", "systemd-system", "cron"],
        help="Scheduler target to install.",
    )
    install_parser.add_argument(
        "--unit-dir",
        help="Override the systemd unit directory. Required only when not using the default.",
    )
    install_parser.add_argument(
        "--output",
        help="Output path for cron installs. Defaults to <manifest>.crontab next to the manifest.",
    )
    install_parser.set_defaults(handler=handle_install)
    return parser


def _default_unit_dir(target: str) -> Path:
    if target == "systemd-user":
        return Path.home() / ".config" / "systemd" / "user"
    if target == "systemd-system":
        return Path("/etc/systemd/system")
    raise ValueError(f"Unsupported systemd target: {target!r}")


def handle_render(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
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
    manifest = load_manifest(args.manifest)
    rendered = render_target(manifest, args.target)

    if args.target == "cron":
        output_path = (
            Path(args.output) if args.output else Path(args.manifest).with_suffix(".crontab")
        )
        output_path.write_text(next(iter(rendered.values())), encoding="utf-8")
        print(f"Wrote cron snippet -> {output_path}")
        print(f"Next step: crontab {output_path}")
        return 0

    unit_dir = Path(args.unit_dir) if args.unit_dir else _default_unit_dir(args.target)
    written = write_rendered_files(unit_dir, rendered)
    print(f"Wrote {len(written)} unit file(s) -> {unit_dir}")
    if args.target == "systemd-user":
        print("Next steps:")
        print("  systemctl --user daemon-reload")
    else:
        print("Next steps:")
        print("  systemctl daemon-reload")

    service_units = [path.stem for path in written if path.suffix == ".service"]
    timer_units = [path.stem for path in written if path.suffix == ".timer"]
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
