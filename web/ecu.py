"""Small client for central-ecu's token-based foreground activity API."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def _command() -> tuple[list[str], dict[str, str]] | None:
    installed = shutil.which("ecu")
    if installed:
        return [installed], os.environ.copy()
    source = os.environ.get("CENTRAL_ECU_SOURCE")
    if not source:
        source = str(Path(__file__).resolve().parents[2] / "central-ecu" / "src")
    source_path = Path(source)
    if not (source_path / "central_ecu" / "__main__.py").is_file():
        return None
    environment = os.environ.copy()
    current = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(source_path) + (os.pathsep + current if current else "")
    return [sys.executable, "-m", "central_ecu"], environment


def start_activity(name: str, *, token: str | None = None) -> str | None:
    """Start a configured activity, or return None when ECU is not installed."""
    command = _command()
    if command is None:
        return None
    token = token or uuid.uuid4().hex
    argv, environment = command
    result = subprocess.run(
        [
            *argv,
            "activity",
            "start",
            "--name",
            name,
            "--token",
            token,
            "--owner-pid",
            str(os.getpid()),
            "--wait",
        ],
        capture_output=True,
        text=True,
        timeout=None,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "ECU activity start failed"
        )
    return token


def stop_activity(token: str | None) -> None:
    if not token:
        return
    command = _command()
    if command is None:
        return
    argv, environment = command
    result = subprocess.run(
        [*argv, "activity", "stop", "--token", token],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "ECU activity stop failed"
        )
