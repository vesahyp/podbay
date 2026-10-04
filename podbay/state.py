"""Podbay's own persisted state: seen_at per session.

Stored at ~/.local/state/podbay/state.json, written atomically
(tmp file + rename) so a crash mid-write never corrupts it. A write the
OS refuses is logged and skipped, never raised: the in-memory state stays
and the next save() (every table refresh) writes it out.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

STATE_VERSION = 1
STATE_PATH = Path.home() / ".local" / "state" / "podbay" / "state.json"
PRUNE_AFTER_DAYS = 14


@dataclass
class SessionState:
    # When you last looked at this session (its tab is the current one in
    # iTerm2, or its transcript pane is open in podbay); answers finished
    # after this are unread.
    seen_at: datetime | None = None
    updated_at: datetime | None = None


def _migrate(data: dict) -> dict:
    """Upgrade an on-disk payload to STATE_VERSION. No-op today; the hook
    exists so a future schema change has one place to land."""
    version = data.get("version", 1)
    if version == STATE_VERSION:
        return data
    data["version"] = STATE_VERSION
    return data


class StateStore:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self._sessions: dict[str, SessionState] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._sessions = {}
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("state file %s unreadable, starting empty: %s", self.path, exc)
            self._sessions = {}
            return
        raw = _migrate(raw)
        sessions = {}
        for sid, entry in raw.get("sessions", {}).items():
            sessions[sid] = SessionState(
                seen_at=_parse_iso(entry.get("seen_at")),
                updated_at=_parse_iso(entry.get("updated_at")),
            )
        self._sessions = sessions

    def save(self) -> bool:
        """Write the state file atomically. Returns False when the OS refused
        the write; the in-memory state is kept and the next save() retries."""
        payload = {
            "version": STATE_VERSION,
            "sessions": {
                sid: {
                    "seen_at": s.seen_at.isoformat() if s.seen_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for sid, s in self._sessions.items()
            },
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=self.path.parent, prefix=".state-", suffix=".json.tmp"
            )
        except OSError as exc:
            log.warning("state save skipped, cannot create a temp file next to %s: %s", self.path, exc)
            return False
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp_path, self.path)
        except OSError as exc:
            log.warning("state save skipped, %s keeps its previous content: %s", self.path, exc, exc_info=True)
            return False
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError as exc:
                    log.warning("could not remove temp file %s: %s", tmp_path, exc)
        return True

    def get(self, session_id: str) -> SessionState:
        return self._sessions.get(session_id, SessionState())

    def set_seen(self, session_id: str, when: datetime) -> None:
        s = self._sessions.setdefault(session_id, SessionState())
        s.seen_at = when
        s.updated_at = when
        self.save()

    def prune(self, live_session_ids: set[str], now: datetime | None = None) -> int:
        """Drop entries for sessions that are both no longer live and stale.
        Returns the number of entries removed."""
        now = now or datetime.now()
        cutoff = now - timedelta(days=PRUNE_AFTER_DAYS)
        keep = {}
        removed = 0
        for sid, s in self._sessions.items():
            if sid in live_session_ids:
                keep[sid] = s
                continue
            if s.updated_at is None or s.updated_at > cutoff:
                keep[sid] = s
                continue
            removed += 1
        if removed:
            self._sessions = keep
            self.save()
        return removed


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
