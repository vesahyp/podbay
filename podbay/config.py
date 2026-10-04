"""Podbay's own settings: a small JSON file at ~/.config/podbay/config.json,
written by `podbay config <key> <value>` and read once at start.

Keys:
  home_repo   the repo under ~/Repositories every session is launched from.
              `o` offers it as the directory, and a tool call that only reads
              there says nothing about where the work is, so it counts in the
              Repos column only when edited. Empty when no repo plays that role.
  voice       on | off: whether HAL remarks on what changed (a session
              finished, asked, stalled, ended, a quota ran hot) as toasts.
              Default on.
  head_jeeves  on | off: whether podbay keeps a Head Jeeves session running
              (a Claude Code session named head-jeeves, started from the home
              repo, see skills/head-jeeves) and sends it work: every event
              HAL toasts, and a checkup when a session turns heated.
              Default off: it is a session that spends tokens.
  review_model  the model Head Jeeves runs on (`claude --model`). Empty, the
              default, means the account's own default model: he carries
              every conversation with you, so he gets the brains.
  head_jeeves_account  the account label he runs as (see accounts.py), so
              he is the session your phone shows when that account bridges
              at startup. Default: the default account.
  head_jeeves_compact_at  the context use, in percent, at which podbay sends
              Head Jeeves `/compact` once he is idle (his turn ended, nothing
              podbay sent him is unanswered), at most once per 30 minutes, so
              the session the phone is bridged to never fills up and never
              has to be replaced. Default 60; 0 or off never compacts.
  notify_command  the command `podbay notify <text>` runs with the text as
              its last argument, to push one line to the user's phone (a
              script with fixed arguments, split like a shell line). Empty,
              the default, means off: nothing is sent.

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
    "voice": "voice",
    "head-jeeves": "head_jeeves",
    "head-jeeves-account": "head_jeeves_account",
    "head-jeeves-compact-at": "head_jeeves_compact_at",
    "review-model": "review_model",
    "notify-command": "notify_command",
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


def voice_mode(path: Path | None = None) -> str:
    return "off" if str(read(path).get("voice") or "on") == "off" else "on"


def head_jeeves_on(path: Path | None = None) -> bool:
    return str(read(path).get("head_jeeves") or "off") == "on"


def review_model(path: Path | None = None) -> str | None:
    return str(read(path).get("review_model") or "") or None


def head_jeeves_account(path: Path | None = None) -> str | None:
    return str(read(path).get("head_jeeves_account") or "") or None


HEAD_JEEVES_COMPACT_AT = 60


def head_jeeves_compact_at(path: Path | None = None) -> int:
    """The percent of context use that triggers a compact; 0 means never.
    An unreadable value is the default, never a crash at startup."""
    raw = str(read(path).get("head_jeeves_compact_at", "")).strip().lower()
    if raw == "":
        return HEAD_JEEVES_COMPACT_AT
    if raw == "off":
        return 0
    try:
        return max(0, min(100, int(float(raw))))
    except ValueError:
        return HEAD_JEEVES_COMPACT_AT


def notify_command(path: Path | None = None) -> str:
    return str(read(path).get("notify_command") or "")


def home_repo(path: Path | None = None) -> str:
    env = os.environ.get("PODBAY_HOME_REPO")
    if env is not None:
        return env
    return str(read(path).get("home_repo") or "")
