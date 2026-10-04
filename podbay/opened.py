"""The sessions `podbay open` started, who started them, and what an agent
typed into a session through podbay.

Two files of its own, ~/.local/state/podbay/opened.json and sent.json, and
not part of state.json: the TUI holds state.json in memory and writes it
back on every refresh, so a launch the CLI added there would be lost. Every
function here reads its file fresh and writes it atomically.

opened.json has one entry per launch: tty, name, opened_at (ISO), by (the
name of the Claude session that ran `podbay open`, None from a plain
shell), prompt (the first prompt it handed claude, None without one) and
session_id once the new session is in the registry.

sent.json has one entry per `podbay send` run from inside a Claude session:
session_id (the target), text, by and sent_at (ISO). A send from a plain
shell or the TUI is the user's own and is not recorded.

Both exist so the mood gauge (mood.py) scores only what the user typed: the
first prompt of a launched session and anything an agent sent are marked
here, at the source, and gather_sessions drops them from a session's
prompts. The text is matched, not its position, so a tail read that no
longer holds the first prompt loses nothing.

closed.json has one entry per `podbay close`: session_id and closed_at
(ISO). HAL reads it so a session closed on purpose does not come back as a
"has ended" event.
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
SENT_PATH = OPENED_PATH.with_name("sent.json")
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


def record(
    tty: str, name: str | None, by: str | None, opened_at: datetime, path: Path | None = None, prompt: str | None = None,
) -> None:
    """Add one launch; launches older than KEEP are dropped on the way."""
    path = path or OPENED_PATH
    cutoff = opened_at - KEEP
    entries = [e for e in read(path) if _opened_at(e) and _opened_at(e) > cutoff]
    entries.append({
        "tty": tty, "name": name, "by": by, "opened_at": opened_at.isoformat(), "prompt": prompt or None, "session_id": None,
    })
    _write(entries, path)


def record_sent(session_id: str, text: str, by: str, sent_at: datetime, path: Path | None = None) -> None:
    """Add one `podbay send` an agent ran; sends older than KEEP are dropped
    on the way."""
    path = path or SENT_PATH
    cutoff = sent_at - KEEP
    entries = [e for e in read(path) if (at := _when(e, "sent_at")) and at > cutoff]
    entries.append({"session_id": session_id, "text": text, "by": by, "sent_at": sent_at.isoformat()})
    _write(entries, path)


def read_sent(path: Path | None = None) -> list[dict]:
    return read(path or SENT_PATH)


def _closed_path() -> Path:
    return OPENED_PATH.with_name("closed.json")


def record_closed(session_id: str, closed_at: datetime, path: Path | None = None) -> None:
    """Mark one session as closed by `podbay close`; entries older than KEEP
    are dropped on the way."""
    path = path or _closed_path()
    cutoff = closed_at - KEEP
    entries = [e for e in read(path) if (at := _when(e, "closed_at")) and at > cutoff]
    entries.append({"session_id": session_id, "closed_at": closed_at.isoformat()})
    _write(entries, path)


def closed_ids(path: Path | None = None) -> set[str]:
    """The ids of the sessions `podbay close` ended."""
    return {e["session_id"] for e in read(path or _closed_path()) if e.get("session_id")}


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


def _when(entry: dict, key: str) -> datetime | None:
    try:
        return datetime.fromisoformat(entry.get(key) or "")
    except ValueError:
        return None


def _opened_at(entry: dict) -> datetime | None:
    return _when(entry, "opened_at")


def launch(session: Session, entries: list[dict]) -> dict | None:
    """The `podbay open` launch that started `session`: the entry with its
    session id, else the newest launch on its tty that it started after.
    None when podbay open did not start it."""
    for e in entries:
        if e.get("session_id") == session.session_id:
            return e
    if not session.tty:
        return None
    launches = [
        (at, e) for e in entries
        if not e.get("session_id") and e.get("tty") == session.tty
        and (at := _opened_at(e)) is not None and at - START_SLACK <= session.started_at <= at + START_WITHIN
    ]
    if not launches:
        return None
    return max(launches, key=lambda pair: pair[0])[1]


def opener(session: Session, entries: list[dict]) -> str | None:
    """Who started `session` with `podbay open`. None when podbay open did
    not start it, or a plain shell did."""
    found = launch(session, entries)
    return found.get("by") if found else None


def agent_text(session: Session, entries: list[dict], sent: list[dict]) -> set[str]:
    """What podbay typed into `session` for an agent, as the transcript
    would hold it: the first prompt of its launch, and every `podbay send`
    a Claude session ran against it. The mood gauge leaves these out."""
    texts: set[str] = set()
    found = launch(session, entries)
    if found and found.get("prompt"):
        texts.add(str(found["prompt"]).strip())
    for e in sent:
        if e.get("session_id") == session.session_id and e.get("text"):
            texts.add(str(e["text"]).strip())
    texts.discard("")
    return texts


def user_prompts(prompts: list[str], typed_for_agent: set[str]) -> list[str]:
    """`prompts` without the ones an agent typed through podbay."""
    if not typed_for_agent:
        return list(prompts)
    return [p for p in prompts if p.strip() not in typed_for_agent]
