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


def code_version(root: Path = ROOT) -> str | None:
    """The commit the code under `root` is at, or None when that is unknown."""
    try:
        with subprocess.Popen(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as git:
            sha = git.communicate(timeout=5)[0].strip()
            return sha if git.returncode == 0 and sha else None
    except (OSError, subprocess.SubprocessError):
        return None


def changed(started_at: str | None, now: str | None) -> bool:
    return started_at is not None and now is not None and started_at != now


def restart(argv: list[str], root: Path = ROOT) -> None:
    """Replace this process with a fresh screen on the installed code."""
    args = [a for a in argv if a != "--no-splash"] + ["--no-splash"]
    os.execvp("uv", ["uv", "run", "--project", str(root), "podbay", *args])
