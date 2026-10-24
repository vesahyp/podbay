"""Pushes about Claude logins: a warning before one ends, and one line the
moment a session says it has ended.

A login ends at a time known in advance (accounts.login_deadline; the
finding is in docs/login-expiry.md). Two warnings go per login: one when
WARN_EARLY or less is left, one when WARN_LATE or less is left. None goes
between QUIET_FROM and QUIET_TO unless under QUIET_UNLESS_LEFT is left, so
a warning held at night goes at QUIET_TO. A login with less than WARN_LATE
left at the first look gets only the late one.

When a session answers "Login expired" (Session.login_expired_at), one push
names the account at once. One per account until a new login: the next
/login gives the account a new refresh token end, and that end is what
marks a login. An answer older than EXPIRED_MAX_AGE when podbay first sees
it is not pushed, so a restart does not send old news.

The state lives in logins.json, so a restart of the screen does not push
twice. Nothing here reads or writes a token.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import voice

log = logging.getLogger(__name__)

STATE_PATH = Path.home() / ".local" / "state" / "podbay" / "logins.json"

WARN_EARLY = 24 * 3600
WARN_LATE = 3600
QUIET_FROM, QUIET_TO = 23, 7  # local hours
QUIET_UNLESS_LEFT = 2 * 3600
EXPIRED_MAX_AGE = 15 * 60

EARLY, LATE = "early", "late"


def read_state(path: Path = STATE_PATH) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".logins.")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def quiet(now: datetime) -> bool:
    return now.hour >= QUIET_FROM or now.hour < QUIET_TO


def warning_due(deadline: float, sent: list[str], now: datetime) -> str | None:
    """The warning to send now for a login that ends at `deadline`, given the
    ones already sent for it; None when nothing is due."""
    left = deadline - now.timestamp()
    if left <= 0 or LATE in sent:
        return None
    if left <= WARN_LATE:
        return LATE
    if left <= WARN_EARLY and EARLY not in sent and (not quiet(now) or left < QUIET_UNLESS_LEFT):
        return EARLY
    return None


def step(
    state: dict, deadlines: dict[str, float], expired: dict[str, datetime], now: datetime
) -> tuple[list[str], dict]:
    """The lines to push and the new state. `deadlines`: account -> when its
    login ends. `expired`: account -> the newest "Login expired" answer in
    any of its sessions."""
    state = {"warned": dict(state.get("warned") or {}), "expired": dict(state.get("expired") or {})}
    lines = []
    for account, deadline in sorted(deadlines.items()):
        record = state["warned"].get(account) or {}
        sent = list(record.get("sent") or []) if record.get("deadline") == deadline else []
        due = warning_due(deadline, sent, now)
        if due is not None:
            lines.append(voice.login_ending(account, deadline - now.timestamp(), datetime.fromtimestamp(deadline)))
            sent = sent + ([EARLY, LATE] if due == LATE else [EARLY])
        state["warned"][account] = {"deadline": deadline, "sent": sent}
    for account, at in sorted(expired.items()):
        login = deadlines.get(account) or (state["warned"].get(account) or {}).get("deadline")
        done = state["expired"].get(account)
        if done is not None and done.get("login") == login:
            continue
        if (now - at).total_seconds() <= EXPIRED_MAX_AGE:
            lines.append(voice.login_expired_push(account))
        state["expired"][account] = {"at": at.isoformat(), "login": login}
    return lines, state


def newest_expired(sessions) -> dict[str, datetime]:
    """account -> the newest "Login expired" answer among its sessions."""
    found: dict[str, datetime] = {}
    for s in sessions:
        at = getattr(s, "login_expired_at", None)
        if at is not None and (s.account not in found or at > found[s.account]):
            found[s.account] = at
    return found


def check(
    deadlines: dict[str, float], sessions, now: datetime, send: Callable[[str], tuple[bool, str]],
    path: Path = STATE_PATH,
) -> list[str]:
    """One pass: push what is due through `send` and record it. A line that
    does not go out is not recorded as sent, so the next pass tries again.
    Returns the lines sent."""
    state = read_state(path)
    lines, new_state = step(state, deadlines, newest_expired(sessions), now)
    sent = []
    for line in lines:
        ok, reason = send(line)
        if not ok and reason != "off":
            log.warning("login push not sent (%s): %s", reason, line)
            return sent  # keep the old state: the next pass sends it again
        if ok:
            sent.append(line)
    if new_state != state:
        write_state(new_state, path)
    return sent
