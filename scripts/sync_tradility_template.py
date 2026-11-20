#!/usr/bin/env python3
"""Copy Tradility's page template into Clockwork (web/templates/to-tradility.html).

Tradility owns the page; Clockwork serves the same file with ``embedded=True``. Edit the page in
the Tradility repository, then run this and commit the result. ``--check`` only reports drift.

    scripts/sync_tradility_template.py [--source PATH] [--check]

The source defaults to ``$TRADILITY_REPO`` or a ``tradility`` checkout beside this repository.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COPY = REPO / "web" / "templates" / "to-tradility.html"
RELATIVE = Path("src") / "tradility" / "templates" / "index.html"


def source_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    root = os.environ.get("TRADILITY_REPO")
    return (Path(root) if root else REPO.parent / "tradility") / RELATIVE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", help="Tradility's index.html (default: the sibling checkout)")
    parser.add_argument("--check", action="store_true", help="report drift, change nothing")
    args = parser.parse_args(argv)
    source = source_path(args.source)
    if not source.is_file():
        print(f"Tradility template not found: {source}", file=sys.stderr)
        return 2
    same = COPY.is_file() and COPY.read_bytes() == source.read_bytes()
    if args.check:
        print("in sync" if same else f"{COPY.name} differs from {source}")
        return 0 if same else 1
    if not same:
        shutil.copyfile(source, COPY)
    print("already in sync" if same else f"updated {COPY.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
