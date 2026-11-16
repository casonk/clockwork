#!/usr/bin/env python3
"""File completed episodes from the torrent dirs into existing library show folders.

Symlinks finished downloads (live in Transmission or left on disk after Magneto's
"Remove") into ``shows/<Title (Year)>/Season NN``. Safe to run repeatedly; it never
creates a new show folder.

    python3 scripts/file_completed_series.py
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_app():
    path = Path(__file__).resolve().parent.parent / "web" / "app.py"
    sys.path.insert(0, str(path.parent))
    spec = spec_from_file_location("clockwork_web_app", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    result = _load_app().auto_file_completed_series()
    for path in result["linked"]:
        print(f"linked {path}")
    for message in result["errors"]:
        print(f"error: {message}", file=sys.stderr)
    print(f"filed {len(result['linked'])} file(s); {len(result['skipped'])} skipped")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
