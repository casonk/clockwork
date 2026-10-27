"""Run one render under the ECU's GPU lease and priority policy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REQUEUED = 75
FALLBACK_RETRY_S = 60.0  # when an installed ECU cannot say: wait rather than spin


def _sibling_source(root: Path) -> Path | None:
    """A sibling checkout's ``src`` that holds the ECU package, if one exists."""
    try:
        checkouts = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return None
    return next(
        (c / "src" for c in checkouts if (c / "src" / "central_ecu" / "__main__.py").is_file()),
        None,
    )


def _command() -> tuple[list[str], dict[str, str]] | None:
    installed = shutil.which("ecu")
    if installed:
        return [installed], os.environ.copy()
    source = os.environ.get("CENTRAL_ECU_SOURCE")
    source_path = Path(source) if source else _sibling_source(Path(__file__).resolve().parents[2])
    if source_path is None or not (source_path / "central_ecu" / "__main__.py").is_file():
        return None
    environment = os.environ.copy()
    current = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(source_path) + (os.pathsep + current if current else "")
    return [sys.executable, "-m", "central_ecu"], environment


def run_render(
    priority: str,
    root: Path,
    db: Path,
    min_free_gb: float,
    source: Path,
) -> int:
    """Run one resumable render; ECU preemption and queue yield request a retry."""
    render = [
        sys.executable,
        str(Path(__file__).with_name("upscale.py")),
        "--root",
        str(root),
        "--db",
        str(db),
        "--min-free-gb",
        str(min_free_gb),
        "render-one",
        str(source),
    ]
    command = _command()
    if command is None:
        if priority == "batch":
            ionice = shutil.which("ionice")
            nice = shutil.which("nice")
            if ionice:
                render = [ionice, "-c", "3", *render]
            if nice:
                render = [nice, "-n", "19", *render]
        return subprocess.run(render, check=False).returncode
    ecu, environment = command
    result = subprocess.run(
        [
            *ecu,
            "run",
            "--gpu",
            "quiet",
            "--skip-consumer",
            "clockwork-upscale",
            "--priority",
            priority,
            "--preemptible",
            "--cancel-before-start-file",
            os.environ.get(
                "CLOCKWORK_UPSCALE_PAUSE_FILE",
                str(Path.home() / ".cache" / "clockwork" / "upscale-pause"),
            ),
            "--max-wait",
            "24h",
            "--wait",
            "--",
            *render,
        ],
        env=environment,
        check=False,
    )
    # ECU uses the child's signal exit code if it needed to force-kill after
    # the graceful preemption window. The saved chunk remains resumable.
    return REQUEUED if result.returncode == 241 else result.returncode


def retry_after() -> dict[str, Any]:
    """Ask the ECU when a requeued render may try again; it owns the backoff.

    Without an ECU, renders run unleased and are only requeued for a render
    request or the disk floor, so they go on at once.
    """
    command = _command()
    if command is None:
        return {"retry_after_s": 0.0}
    ecu, environment = command
    try:
        result = subprocess.run(
            [*ecu, "retry-after", "--json"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        answer = json.loads(result.stdout)
        answer["retry_after_s"] = max(float(answer["retry_after_s"]), 0.0)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return {"retry_after_s": FALLBACK_RETRY_S}
    return answer
