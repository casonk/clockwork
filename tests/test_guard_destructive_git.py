"""The agent hook that keeps git from rewriting a working tree holding uncommitted work."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import guard_destructive_git as guard  # noqa: E402


def blocked(command: str, dirty: bool = True) -> bool:
    return bool(guard.evaluate(command, lambda directory: dirty))


@pytest.mark.parametrize(
    "command",
    [
        "git -C /repo apply -R --unidiff-zero p.patch",  # the mistake this hook exists for
        "git apply fix.patch",
        "git reset --hard",
        "git reset --hard origin/main",
        "git checkout -- web/app.py",
        "git checkout .",
        "git checkout -f main",
        "git switch --discard-changes main",
        "git restore web/app.py",
        "git restore --worktree --staged x",
        "git clean -fd",
        "git clean --force",
        "git stash",
        "git stash pop",
        "git stash drop",
        "cd /repo && git reset --hard",
        "echo hi; git apply p.patch",
        "GIT_X=1 git apply p.patch",
        "git -c core.x=1 apply p.patch",
    ],
)
def test_working_tree_rewrites_are_blocked_when_there_are_uncommitted_changes(command):
    assert blocked(command)
    assert "worktree" in guard.evaluate(command, lambda directory: True)


@pytest.mark.parametrize(
    "command",
    [
        "git apply --cached p.patch",
        "git apply --check p.patch",
        "git apply --stat p.patch",
        "git checkout -b new-branch",
        "git switch -c new-branch",
        "git switch main",
        "git restore --staged web/app.py",
        "git clean -n",
        "git clean --dry-run",
        "git stash list",
        "git stash show -p",
        "git status",
        "git diff",
        "git add -p",
        "git commit -m 'git reset --hard is blocked'",
        "echo git reset --hard",
        "grep -r 'git apply' docs",
        "git worktree add /tmp/x origin/main",
    ],
)
def test_harmless_commands_are_allowed_even_in_a_dirty_tree(command):
    assert not blocked(command)


def test_a_clean_tree_is_not_guarded():
    assert not blocked("git reset --hard", dirty=False)


def test_the_override_lets_a_deliberate_command_through():
    assert not blocked("ALLOW_DESTRUCTIVE_GIT=1 git reset --hard")


def test_the_target_repository_is_the_one_given_with_dash_c():
    seen = []

    def dirty(directory):
        seen.append(directory)
        return directory == "/dirty"

    assert guard.evaluate("git -C /clean reset --hard", dirty) == ""
    assert "/dirty" in guard.evaluate("git -C /dirty reset --hard", dirty)
    assert seen == ["/clean", "/dirty"]


def _clean_env() -> dict[str, str]:
    """The environment without GIT_*: git hooks export GIT_DIR and friends, which would point
    these test repositories at the real one."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run_hook(command: str, project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "guard_destructive_git.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


def test_the_hook_exits_2_in_a_dirty_repository_and_0_otherwise(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=_clean_env())
    (tmp_path / "f.txt").write_text("x")
    dirty = run_hook("git reset --hard", tmp_path)
    assert dirty.returncode == 2 and "Blocked" in dirty.stderr
    assert run_hook("git status", tmp_path).returncode == 0
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
        env=_clean_env(),
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=_clean_env())
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "-m",
            "y",
        ],
        check=True,
        env=_clean_env(),
    )
    assert run_hook("git reset --hard", tmp_path).returncode == 0  # clean now


def test_bad_input_never_blocks():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "guard_destructive_git.py")],
        input="not json",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_the_project_settings_wire_the_hook():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    assert "guard_destructive_git.py" in entry["hooks"][0]["command"]
