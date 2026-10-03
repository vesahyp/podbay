"""The sessions `podbay open` started, and who started them.

Its own file, ~/.local/state/podbay/opened.json, and not part of
state.json: the TUI holds state.json in memory and writes it back on every
refresh, so a launch the CLI added there would be lost. Every function
here reads the file fresh and writes it atomically.

One entry per launch: tty, name, opened_at (ISO), by (the name of the
Claude session that ran `podbay open`, None from a plain shell) and
session_id once the new session is in the registry.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .model import Session

log = logging.getLogger(__name__)

OPENED_PATH = Path.home() / ".local" / "state" / "podbay" / "opened.json"
KEEP = timedelta(days=14)
# A session's registry start can stamp a little before the launch's own
# clock read, on a busy machine; and one that starts much later on the same
# tty is somebody else's.
START_SLACK = timedelta(seconds=5)
START_WITHIN = timedelta(minutes=10)


def read(path: Path | None = None) -> list[dict]:
    path = path or OPENED_PATH
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("opened file %s unreadable: %s", path, exc)
        return []
    return data if isinstance(data, list) else []


def _write(entries: list[dict], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".opened-", suffix=".json.tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(entries, fh, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("opened file %s not written: %s", path, exc)


def record(tty: str, name: str | None, by: str | None, opened_at: datetime, path: Path | None = None) -> None:
    """Add one launch; launches older than KEEP are dropped on the way."""
    path = path or OPENED_PATH
    cutoff = opened_at - KEEP
    entries = [e for e in read(path) if _opened_at(e) and _opened_at(e) > cutoff]
    entries.append({"tty": tty, "name": name, "by": by, "opened_at": opened_at.isoformat(), "session_id": None})
    _write(entries, path)


def set_session(tty: str, opened_at: datetime, session_id: str, path: Path | None = None) -> None:
    """Fill in the session id of the launch on `tty` at `opened_at`."""
    path = path or OPENED_PATH
    entries = read(path)
    for e in entries:
        if e.get("tty") == tty and e.get("opened_at") == opened_at.isoformat():
            e["session_id"] = session_id
    _write(entries, path)


def forget(session_id: str, path: Path | None = None) -> None:
    path = path or OPENED_PATH
    entries = read(path)
    kept = [e for e in entries if e.get("session_id") != session_id]
    if len(kept) != len(entries):
        _write(kept, path)


def _opened_at(entry: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(entry.get("opened_at") or "")
    except ValueError:
        return None


def opener(session: Session, entries: list[dict]) -> str | None:
    """Who started `session` with `podbay open`: the entry with its session
    id, else the newest launch on its tty that it started after. None when
    podbay open did not start it, or a plain shell did."""
    for e in entries:
        if e.get("session_id") == session.session_id:
            return e.get("by")
    if not session.tty:
        return None
    launches = [
        (at, e) for e in entries
        if not e.get("session_id") and e.get("tty") == session.tty
        and (at := _opened_at(e)) is not None and at - START_SLACK <= session.started_at <= at + START_WITHIN
    ]
    if not launches:
        return None
    return max(launches, key=lambda pair: pair[0])[1].get("by")
