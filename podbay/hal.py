"""HAL remarks when something changed: a session finished and waits, asked a
question, stalled, came due, a quota window ran hot; and, at long intervals,
when the ship is quiet. Never at random, and only on screen: `remarks`
compares the last refresh with this one and returns the lines, app.py shows
them as toasts. `podbay config voice off` silences him.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import voice
from .model import DUE, NEEDS_YOU, STALLED, Session

# A quota window at or past this is hot: HAL warns once per window.
QUOTA_HOT_PCT = 85.0
# The quiet-ship remark, at most this often, and only when nothing needs you.
QUIET_INTERVAL = timedelta(hours=2)
# Voice setting values: on (toasts, the default) or off.
VOICE_ON, VOICE_OFF = "on", "off"


@dataclass
class Memory:
    """What HAL already said, so a refresh every 3 s does not repeat it."""

    statuses: dict[str, str] = field(default_factory=dict)  # session id -> derived status
    hot_windows: set[str] = field(default_factory=set)  # "<account>:5H" etc. already warned
    last_quiet_at: datetime | None = None
    primed: bool = False  # the first refresh only sets the baseline


def remarks(memory: Memory, sessions: list[Session], limits: dict[str, dict], now: datetime) -> list[str]:
    """The lines HAL says this refresh, updating `memory` in place."""
    lines: list[str] = []
    current: dict[str, str] = {}
    attention = False
    for s in sessions:
        if s.is_shell:
            continue
        derived = s.derive_status(now)
        current[s.session_id] = derived
        if derived in (NEEDS_YOU, STALLED, DUE):
            attention = True
        previous = memory.statuses.get(s.session_id)
        if not memory.primed or previous == derived:
            continue
        if derived == NEEDS_YOU and s.unread:
            kind = (s.waiting_on or {}).get("kind")
            if kind in ("ask_user_question", "prompt", "question_text"):
                lines.append(voice.hal_question(s.title))
            else:
                lines.append(voice.hal_finished(s.title))
        elif derived == STALLED:
            minutes = int((now - s.last_turn_ts).total_seconds() // 60) if s.last_turn_ts else 0
            lines.append(voice.hal_stalled(s.title, minutes))
        elif derived == DUE:
            lines.append(voice.hal_due(s.title))
    memory.statuses = current

    for account, figures in sorted(limits.items()):
        for window, key in (("five_pct", "5H"), ("week_pct", "7D")):
            pct = figures.get(window)
            mark = f"{account}:{key}"
            if pct is None:
                continue
            if pct >= QUOTA_HOT_PCT:
                if memory.primed and mark not in memory.hot_windows:
                    lines.append(voice.hal_quota_hot(account, key, pct, len(limits) > 1))
                memory.hot_windows.add(mark)
            else:
                memory.hot_windows.discard(mark)  # the window reset: warn again next time

    if not memory.primed:
        memory.primed = True
        memory.last_quiet_at = now
        return []

    if not attention and not lines and current:
        if memory.last_quiet_at is None or now - memory.last_quiet_at >= QUIET_INTERVAL:
            memory.last_quiet_at = now
            lines.append(voice.hal_quiet())
    elif attention:
        memory.last_quiet_at = now  # the quiet clock restarts once there was something to do
    return lines

