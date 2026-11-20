"""Where other local services keep the files this app reads.

The tracked code names no sibling repository: each location comes from an environment variable,
else from the gitignored ``config/integrations.local.toml`` (see ``integrations.example.toml``).
A location that is set nowhere is simply not configured, and the page that needs it says so.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

LOCAL_FILE = Path(__file__).resolve().parent.parent / "config" / "integrations.local.toml"


def _local_table() -> dict:
    try:
        with LOCAL_FILE.open("rb") as handle:
            return tomllib.load(handle).get("paths", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def configured_path(key: str, env: str = "") -> Path | None:
    """The path set by ``env`` or by ``[paths] key`` in the local file; ``None`` when unset."""
    value = (os.environ.get(env) if env else None) or _local_table().get(key)
    return Path(str(value)).expanduser() if value else None
