#!/usr/bin/env python3
"""Claude Code PreToolUse hook: stop git commands that rewrite a working tree holding uncommitted work.

A repository here often carries uncommitted edits from the person who owns it. Commands that
rewrite the working tree (``git apply`` without ``--cached``, ``reset --hard``, ``checkout -- <path>``,
``restore``, ``clean -f``, ``stash``, a forced ``switch``) can destroy or scramble that work, and
``git -C <path>`` makes it easy to aim one at a repository other than the one you are standing in.

The hook blocks such a command only when the target repository has uncommitted changes, and tells
the agent to do the work on a copy or in a ``git worktree`` instead. To run one deliberately, put
``ALLOW_DESTRUCTIVE_GIT=1`` in front of the command.

Reads the hook JSON on stdin; exit 2 blocks the command and sends stderr back to the agent. Any
unexpected problem allows the command, so a bug here never stops work.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable

OVERRIDE = "ALLOW_DESTRUCTIVE_GIT=1"
SAFE_APPLY_FLAGS = {"--cached", "--check", "--stat", "--numstat", "--summary"}
# global options that take a value as the next argument
GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
SEPARATORS = re.compile(r"&&|\|\||[;|\n]")


def _segments(command: str) -> list[list[str]]:
    parts = []
    for chunk in SEPARATORS.split(command):
        try:
            tokens = shlex.split(chunk)
        except ValueError:
            tokens = chunk.split()
        if tokens:
            parts.append(tokens)
    return parts


def _parse_git(tokens: list[str]) -> tuple[str | None, str, list[str]] | None:
    """(directory given with -C, subcommand, arguments) for a ``git`` invocation, else None."""
    index = 0
    while index < len(tokens) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]):
        index += 1  # leading VAR=value assignments
    if index >= len(tokens) or os.path.basename(tokens[index]) != "git":
        return None
    index += 1
    directory = None
    while index < len(tokens) and tokens[index].startswith("-"):
        option = tokens[index]
        if option in GLOBAL_WITH_VALUE and index + 1 < len(tokens):
            if option == "-C":
                directory = tokens[index + 1]
            index += 2
        else:
            index += 1
    if index >= len(tokens):
        return None
    return directory, tokens[index], tokens[index + 1 :]


def _risk(subcommand: str, args: list[str]) -> str:
    """What this git command does to the working tree, or an empty string when it is harmless."""
    flags = {a for a in args if a.startswith("-")}
    if subcommand == "apply":
        return "" if flags & SAFE_APPLY_FLAGS else "applies a patch to the working tree"
    if subcommand == "reset":
        return "discards changes (reset --hard)" if "--hard" in flags else ""
    if subcommand == "checkout":
        if "--" in args or "." in args or flags & {"-f", "--force", "--discard-changes"}:
            return "overwrites files in the working tree (checkout)"
        return ""
    if subcommand == "switch":
        return (
            "discards changes (switch --force)"
            if flags & {"-f", "--force", "--discard-changes"}
            else ""
        )
    if subcommand == "restore":
        staged_only = "--staged" in flags and "--worktree" not in flags
        return "" if staged_only else "overwrites files in the working tree (restore)"
    if subcommand == "clean":
        force = any(a.startswith("-") and not a.startswith("--") and "f" in a for a in args)
        return "deletes untracked files (clean)" if force or "--force" in flags else ""
    if subcommand == "stash":
        verb = next((a for a in args if not a.startswith("-")), "push")
        harmless = {"list", "show"}
        return "" if verb in harmless else f"moves uncommitted changes away (stash {verb})"
    return ""


def evaluate(command: str, is_dirty: Callable[[str | None], bool]) -> str:
    """The message to send back when ``command`` must be blocked, else an empty string."""
    if OVERRIDE in command:
        return ""
    for tokens in _segments(command):
        parsed = _parse_git(tokens)
        if parsed is None:
            continue
        directory, subcommand, args = parsed
        risk = _risk(subcommand, args)
        if risk and is_dirty(directory):
            target = f"the repository at {directory}" if directory else "this repository"
            return (
                f"Blocked: `git {subcommand}` {risk}, and {target} has uncommitted changes that "
                "are not yours to rewrite.\n"
                "Do the work on a copy or in a separate worktree instead "
                "(`git worktree add <dir> <ref>`, or copy the file and patch the copy), and stage "
                "with `git apply --cached` / `git add -p` rather than touching the working tree.\n"
                f"If this really is intended, ask the user, then rerun with {OVERRIDE} in front."
            )
    return ""


def _repo_is_dirty(directory: str | None) -> bool:
    """True when the repository (``directory`` or the project) has uncommitted changes."""
    base = directory or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    result = subprocess.run(
        ["git", "-C", base, "status", "--porcelain"], capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = str((payload.get("tool_input") or {}).get("command") or "")
        message = evaluate(command, _repo_is_dirty)
    except Exception:  # never let a bug here block work
        return 0
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
