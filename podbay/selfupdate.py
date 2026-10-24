"""The screen runs the code it started with. `make install` moves .release/
to a new commit, but a screen already open keeps the old modules in memory
until it restarts, so a fix is not live until someone remembers to quit and
start podbay again. These helpers let the screen notice a new commit and
start itself again."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(root: Path, *args: str) -> str | None:
    """One git command's output under `root`, or None when it fails. Popen,
    not subprocess.run, which the tests replace with "binary not found"."""
    try:
        with subprocess.Popen(["git", "-C", str(root), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as git:
            out = git.communicate(timeout=5)[0].strip()
            return out if git.returncode == 0 and out else None
    except (OSError, subprocess.SubprocessError):
        return None


def code_version(root: Path = ROOT) -> str | None:
    """The commit the code under `root` is at, or None when that is unknown."""
    return _git(root, "rev-parse", "HEAD")


def release_lag(root: Path = ROOT) -> dict | None:
    """How far the code under `root` is behind origin/main, or None when it
    is not behind or that is unknown. A bare `git push` moves origin/main and
    leaves .release/ where it was, so the installed podbay runs the old code
    until someone remembers `make install` (22e1fa4, 2026-10-24)."""
    behind = _git(root, "rev-list", "--count", "HEAD..origin/main")
    if not behind or not behind.isdigit() or behind == "0":
        return None
    return {"behind": int(behind), "installed": _git(root, "rev-parse", "--short", "HEAD"), "origin_main": _git(root, "rev-parse", "--short", "origin/main")}


def changed(started_at: str | None, now: str | None) -> bool:
    return started_at is not None and now is not None and started_at != now


def restart(argv: list[str], root: Path = ROOT) -> None:
    """Replace this process with a fresh screen on the installed code."""
    args = [a for a in argv if a != "--no-splash"] + ["--no-splash"]
    os.execvp("uv", ["uv", "run", "--project", str(root), "podbay", *args])
