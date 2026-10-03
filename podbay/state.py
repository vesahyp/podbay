"""Podbay's own persisted state: parked_until / note / seen_at per session,
plus the last-known working set for Resume after a reboot.

Stored at ~/.local/state/podbay/state.json, written atomically
(tmp file + rename) so a crash mid-write never corrupts it. A write the
OS refuses is logged and skipped, never raised: the in-memory state stays
and the next save() (every table refresh) writes it out.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

STATE_VERSION = 1
STATE_PATH = Path.home() / ".local" / "state" / "podbay" / "state.json"
PRUNE_AFTER_DAYS = 14
DEFAULT_HOUR = 9  # default time-of-day for a bare date/weekday

_WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


@dataclass
class SessionState:
    parked_until: datetime | None = None
    note: str | None = None
    # When you last looked at this session (tab focused, or chat log opened
    # in podbay); answers finished after this are unread.
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
        # Last-known working set of live Claude sessions, for Resume after a
        # reboot; a plain list of dicts, not SessionState -- prune() must
        # never touch it (see prune's docstring).
        self._snapshot: list[dict] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._sessions = {}
            self._snapshot = []
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("state file %s unreadable, starting empty: %s", self.path, exc)
            self._sessions = {}
            self._snapshot = []
            return
        raw = _migrate(raw)
        sessions = {}
        for sid, entry in raw.get("sessions", {}).items():
            sessions[sid] = SessionState(
                parked_until=_parse_iso(entry.get("parked_until")),
                note=entry.get("note"),
                seen_at=_parse_iso(entry.get("seen_at")),
                updated_at=_parse_iso(entry.get("updated_at")),
            )
        self._sessions = sessions
        self._snapshot = raw.get("snapshot", [])

    def save(self) -> bool:
        """Write the state file atomically. Returns False when the OS refused
        the write; the in-memory state is kept and the next save() retries."""
        payload = {
            "version": STATE_VERSION,
            "sessions": {
                sid: {
                    "parked_until": s.parked_until.isoformat() if s.parked_until else None,
                    "note": s.note,
                    "seen_at": s.seen_at.isoformat() if s.seen_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for sid, s in self._sessions.items()
            },
            "snapshot": self._snapshot,
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

    def set_parked(self, session_id: str, when: datetime | None) -> None:
        s = self._sessions.setdefault(session_id, SessionState())
        s.parked_until = when
        s.updated_at = datetime.now()
        self.save()

    def set_seen(self, session_id: str, when: datetime) -> None:
        s = self._sessions.setdefault(session_id, SessionState())
        s.seen_at = when
        s.updated_at = when
        self.save()

    def set_note(self, session_id: str, note: str | None) -> None:
        s = self._sessions.setdefault(session_id, SessionState())
        s.note = note
        s.updated_at = datetime.now()
        self.save()

    def set_snapshot(self, entries: list[dict], now: datetime) -> None:
        """Replace the working-set snapshot wholesale (not merged) with
        `entries`; `now` is accepted for symmetry with the other setters but
        the caller stamps per-entry timestamps itself."""
        self._snapshot = list(entries)
        self.save()

    def get_snapshot(self) -> list[dict]:
        return list(self._snapshot)

    def prune(self, live_session_ids: set[str], now: datetime | None = None) -> int:
        """Drop entries for sessions that are both no longer live and stale.
        Returns the number of entries removed. Only touches self._sessions
        (park/note/seen state) -- the snapshot is what must survive a reboot,
        so it is never in scope here."""
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


# The + is optional and the unit may be spelled out: "+30m", "30 min" and
# "2 hours" all reach here.
_RELATIVE_RE = re.compile(r"^\+?\s*(\d+)\s*(m|mins?|minutes?|h|hrs?|hours?|d|days?)$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}):(\d{2}))?$")
_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")
_TODAY_RE = re.compile(r"^today(?:\s+(\d{1,2})(?::(\d{2}))?)?$")
_WEEKDAY_RE = re.compile(
    r"^(mon|tue|wed|thu|fri|sat|sun)(?:day)?(?:\s+(\d{1,2})(?::(\d{2}))?)?$"
)
_TOMORROW_RE = re.compile(r"^tomorrow(?:\s+(\d{1,2})(?::(\d{2}))?)?$")


class ParseError(ValueError):
    pass


def parse_when(text: str, now: datetime | None = None) -> datetime:
    """Parse a human park-until expression into a datetime.

    Supported forms: +2h, +3d, +30m (the + is optional, and units may be
    spelled out: "30 min", "2 hours"), today 14, today 14:30, tomorrow,
    tomorrow 9, tomorrow 9:30, fri 14, fri 14:30, fri, 2026-09-12,
    2026-09-12 09:00, 14:30, 14. A bare time already passed rolls to
    tomorrow; an explicit "today" time already passed is an error.
    """
    try:
        return _parse_when(text, now)
    except ParseError:
        raise
    except (ValueError, OverflowError) as exc:  # out-of-range hour, day, month
        raise ParseError(f"invalid park-until expression {text!r}: {exc}") from exc


def _parse_when(text: str, now: datetime | None) -> datetime:
    now = now or datetime.now()
    s = text.strip().lower()
    if not s:
        raise ParseError("empty input")

    m = _RELATIVE_RE.match(s)
    if m:
        amount, unit = int(m.group(1)), m.group(2)[0]
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        return now + delta

    m = _TODAY_RE.match(s)
    if m:
        hour = int(m.group(1)) if m.group(1) else DEFAULT_HOUR
        minute = int(m.group(2)) if m.group(2) else 0
        result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if result <= now:
            raise ParseError(f"today {hour:02d}:{minute:02d} has already passed")
        return result

    m = _TOMORROW_RE.match(s)
    if m:
        hour = int(m.group(1)) if m.group(1) else DEFAULT_HOUR
        minute = int(m.group(2)) if m.group(2) else 0
        target_date = (now + timedelta(days=1)).date()
        return datetime(target_date.year, target_date.month, target_date.day, hour, minute)

    m = _WEEKDAY_RE.match(s)
    if m:
        target_wd = _WEEKDAYS[m.group(1)]
        hour = int(m.group(2)) if m.group(2) else DEFAULT_HOUR
        minute = int(m.group(3)) if m.group(3) else 0
        days_ahead = (target_wd - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).date()
        result = datetime(candidate.year, candidate.month, candidate.day, hour, minute)
        if result <= now:
            result += timedelta(days=7)
        return result

    m = _DATE_RE.match(s)
    if m:
        date_part = m.group(1)
        hour = int(m.group(2)) if m.group(2) else DEFAULT_HOUR
        minute = int(m.group(3)) if m.group(3) else 0
        year, month, day = (int(x) for x in date_part.split("-"))
        return datetime(year, month, day, hour, minute)

    m = _TIME_RE.match(s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    raise ParseError(f"unrecognised park-until expression: {text!r}")
