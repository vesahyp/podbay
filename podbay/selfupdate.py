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


def restart(argv: list[str], root: Path = ROOT, prefix: str = sys.prefix) -> None:
    """Replace this process with a fresh screen on the installed code.

    The screen runs as the child of a `uv run`, which waits for it. Exec'ing
    `uv run` again replaced only the child: the old `uv run` stayed, and every
    release added one more process to the chain, each holding the uv cache
    lock (38 deep on 2026-11-01). So the venv is synced first, in a child that
    ends, and the venv's own entry script takes this process's place: the
    parent `uv run` now waits on the new screen, and the chain stays one deep."""
    args = [a for a in argv if a != "--no-splash"] + ["--no-splash"]
    try:
        subprocess.run(["uv", "sync", "--project", str(root), "--quiet"], timeout=300, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass  # the old dependencies most likely still do; start anyway
    script = str(Path(prefix) / "bin" / "podbay")
    os.execv(script, [script, *args])
