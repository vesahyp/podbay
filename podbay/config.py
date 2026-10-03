"""Podbay's own settings: a small JSON file at ~/.config/podbay/config.json,
written by `podbay config <key> <value>` and read once at start.

Keys:
  home_repo   the repo under ~/Repositories every session is launched from.
              `o` offers it as the directory, and a tool call that only reads
              there says nothing about where the work is, so it counts in the
              Repos column only when edited. Empty when no repo plays that role.

PODBAY_HOME_REPO in the environment overrides the file, for a one-off run.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "podbay" / "config.json"
KEYS = {
    "home-repo": "home_repo",
}


def read(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write(data: dict, path: Path | None = None) -> None:
    """Atomic, like every other file podbay writes."""
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_value(key: str, value: str | None, path: Path | None = None) -> dict:
    """Set a key (by its command-line name) or clear it with None."""
    field = KEYS[key]
    data = read(path)
    if value is None or value == "":
        data.pop(field, None)
    else:
        data[field] = value
    write(data, path)
    return data


def home_repo(path: Path | None = None) -> str:
    env = os.environ.get("PODBAY_HOME_REPO")
    if env is not None:
        return env
    return str(read(path).get("home_repo") or "")
