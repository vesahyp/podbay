"""HAL remarks when something changed: a session finished and waits, asked a
question, stalled, came due, a quota window ran hot; and, at long intervals,
when the ship is quiet. Never at random, and only on screen: `remarks`
compares the last refresh with this one and returns the lines, app.py shows
them as toasts. `podbay config voice off` silences him.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import mood, voice
from .model import DUE, NEEDS_YOU, STALLED, Session, is_head_jeeves

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
    seen: dict[str, Session] = field(default_factory=dict)  # session id -> the session as last seen
    hot_windows: set[str] = field(default_factory=set)  # "<account>:5H" etc. already warned
    heated: set[str] = field(default_factory=set)  # sessions whose prompts read heated last time
    newly_heated: list[str] = field(default_factory=list)  # ids that turned hot this refresh (for a checkup)
    # (session name, line) for each session event this refresh: finished,
    # question, stalled, due, ended. What podbay forwards to Head Jeeves.
    events: list[tuple[str, str]] = field(default_factory=list)
    last_quiet_at: datetime | None = None
    primed: bool = False  # the first refresh only sets the baseline


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # someone else's process: it exists
    return True


def remarks(memory: Memory, sessions: list[Session], limits: dict[str, dict], now: datetime) -> list[str]:
    """The lines HAL says this refresh, updating `memory` in place."""
    lines: list[str] = []
    memory.events = []
    current: dict[str, str] = {}
    attention = False
    for s in sessions:
        if s.is_shell or is_head_jeeves(s):
            continue
        derived = s.derive_status(now)
        current[s.session_id] = derived
        if derived in (NEEDS_YOU, STALLED, DUE):
            attention = True
        previous = memory.statuses.get(s.session_id)
        if not memory.primed or previous is None or previous == derived:
            continue  # a session seen for the first time has no change to report
        line = None
        if derived == NEEDS_YOU and s.unread:
            kind = (s.waiting_on or {}).get("kind")
            if kind == "subagents_running":
                continue  # the turn ended but its agents have not: nothing is finished yet
            if kind in ("ask_user_question", "prompt", "question_text"):
                line = voice.hal_question(s.title)
            else:
                line = voice.hal_finished(s.title)
        elif derived == STALLED:
            minutes = int((now - s.last_turn_ts).total_seconds() // 60) if s.last_turn_ts else 0
            line = voice.hal_stalled(s.title, minutes)
        elif derived == DUE:
            line = voice.hal_due(s.title)
        if line:
            lines.append(line)
            memory.events.append((s.name, line))
    if memory.primed:
        # A session whose claude exited drops out of the registry; its
        # terminal goes back to being a shell. That is a finish too, and
        # the last thing it said is what the user wants to hear.
        for session_id in memory.statuses.keys() - current.keys():
            gone = memory.seen.get(session_id)
            if gone is not None and not pid_alive(gone.pid):
                line = voice.hal_ended(gone.title, gone.recap)
                lines.append(line)
                memory.events.append((gone.name, line))
    memory.statuses = current
    memory.seen = {s.session_id: s for s in sessions if s.session_id in current}

    heated_now = {s.session_id for s in sessions if not s.is_shell and not is_head_jeeves(s) and mood.is_hot(s.recent_prompts)}
    memory.newly_heated = []
    if memory.primed:
        for s in sessions:
            if s.session_id in heated_now and s.session_id not in memory.heated:
                lines.append(voice.hal_heated(s.title))
                memory.newly_heated.append(s.session_id)
    memory.heated = heated_now

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

