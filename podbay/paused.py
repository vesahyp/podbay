"""The sessions Head Jeeves paused with `podbay pause`, so he can tell later
which ones wait for a resume message, and so an idle paused session raises
no "finished" or "stalled" event (hal.py).

Its own file, ~/.local/state/podbay/paused.json, for the reason opened.py
gives: the TUI holds state.json in memory and writes it back on every
refresh, so a record the CLI added there would be lost. One entry per paused
session: session_id, name, title, paused_at (ISO) and by (the session that
ran `podbay pause`, None from a plain shell). `podbay resume` removes it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

PAUSED_PATH = Path.home() / ".local" / "state" / "podbay" / "paused.json"
# A pause nobody resumed in this long belongs to a session that is gone.
KEEP = timedelta(days=7)


def read(path: Path | None = None) -> list[dict]:
    path = path or PAUSED_PATH
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("paused file %s unreadable: %s", path, exc)
        return []
    return [e for e in data if isinstance(e, dict) and e.get("session_id")] if isinstance(data, list) else []


def _write(entries: list[dict], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".paused-", suffix=".json.tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(entries, fh, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("paused file %s not written: %s", path, exc)


def by_id(path: Path | None = None) -> dict[str, dict]:
    return {e["session_id"]: e for e in read(path)}


def ids(path: Path | None = None) -> set[str]:
    return set(by_id(path))


def record(session_id: str, name: str, title: str, by: str | None, at: datetime, path: Path | None = None) -> None:
    """Mark one session paused; a second pause of the same session replaces
    the first, and pauses older than KEEP are dropped on the way."""
    path = path or PAUSED_PATH
    cutoff = at - KEEP
    kept = []
    for e in read(path):
        if e["session_id"] == session_id:
            continue
        try:
            if datetime.fromisoformat(e.get("paused_at", "")) < cutoff:
                continue
        except ValueError:
            continue
        kept.append(e)
    kept.append({"session_id": session_id, "name": name, "title": title, "paused_at": at.isoformat(), "by": by})
    _write(kept, path)


def forget(session_id: str, path: Path | None = None) -> bool:
    """Remove the session's pause. True when there was one."""
    path = path or PAUSED_PATH
    entries = read(path)
    kept = [e for e in entries if e["session_id"] != session_id]
    if len(kept) == len(entries):
        return False
    _write(kept, path)
    return True
