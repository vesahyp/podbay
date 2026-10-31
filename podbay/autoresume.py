"""Continue a session that stopped on a usage limit when the limit resets.

A stop has three forms in the transcript (sources.read_limit_stop):

- message: Claude Code's own answer, "You've hit your 5-hour limit · resets
  1:20pm (Europe/Helsinki)". Also "session limit", "weekly limit" and a
  model's limit. It comes with a reset time.
- wrap_up: Claude Code tells the model that the limit is reached and a short
  grace allowance remains (a meta record with usageLimitNote). The model then
  ends its turn with a report in its own words, so the last text is no limit
  message and carries no reset time. 2026-10-31: one of two sessions stopped
  on the same limit this way.
- agent: a subagent of the session stopped on the limit, seen in its own
  transcript or in the failed task notification that reports it.

A stop stays open until something new happens in the session: a typed prompt,
Esc, or an answer that does not speak of the limit. The check runs once every
CHECK_SECONDS. One stop is sent one message, RESUME_DELAY after its reset,
when all of these hold:

- the session still sits on that stop, read again from the transcript;
- it is not paused (`podbay pause`), not busy, and no agent of it writes;
- the account's window really reset: a /usage run from after the reset time,
  or the snapshots, show no window still open (window_reset);
- its input box is empty and no dialog is open. iTerm2 gives no colours, so a
  dim suggestion in the box reads as typed text; that holds the send, and the
  event after GIVE_UP names the session. A typed line is never overwritten.

A send that is held is tried on each check; after GIVE_UP Head Jeeves gets an
event that names the session and why. A stop still open more than MAX_LATE
after its reset is old news and is only recorded.

The record of stops lives in autoresume.json, written before each send and
after, so a restart of the screen neither forgets a stop nor sends twice. A
send that timed out may have arrived and is never repeated.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import voice

log = logging.getLogger(__name__)

STATE_PATH = Path.home() / ".local" / "state" / "podbay" / "autoresume.json"

CHECK_SECONDS = 30
RESUME_DELAY = timedelta(seconds=90)
GIVE_UP = timedelta(minutes=30)
MAX_LATE = timedelta(hours=12)
KEEP = timedelta(days=14)
AGENT_QUIET = timedelta(minutes=2)
# A window at or above this counts as the one the session stopped on, and as
# not reset while its reset time is ahead. A stop can come at 99%.
HOT_PCT = 90.0

FIVE_HOUR, SEVEN_DAY = "five_hour", "seven_day"
RESUMED, UNCERTAIN, MOVED_ON, TOO_LATE, GAVE_UP = "resumed", "uncertain", "moved_on", "too_late", "gave_up"

# "1:20pm (Europe/Helsinki)", "9pm", "Nov 3 at 1pm (...)", "Nov 3, 1:20pm (...)"
_RESET_RE = re.compile(
    r"^(?:(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2})(?:,| at) )?"
    r"(?P<hour>\d{1,2})(?::(?P<min>\d{2}))?(?P<ampm>am|pm)(?: \((?P<tz>[^)]+)\))?$"
)


def reset_time(text: str | None, stopped_at: datetime) -> datetime | None:
    """The naive local time a limit message's reset phrase means: the first
    such moment after the stop, since the phrase has no year and often no
    date. None when the phrase has an unknown shape."""
    m = _RESET_RE.match((text or "").strip())
    if not m:
        return None
    tz = None
    if m["tz"]:
        try:
            tz = ZoneInfo(m["tz"])
        except (ZoneInfoNotFoundError, ValueError):
            return None
    base = stopped_at.astimezone(tz)
    hour = int(m["hour"]) % 12 + (12 if m["ampm"] == "pm" else 0)
    try:
        if m["mon"]:
            month = datetime.strptime(m["mon"], "%b").month
            when = base.replace(month=month, day=int(m["day"]), hour=hour, minute=int(m["min"] or 0), second=0, microsecond=0)
            if when < base - timedelta(minutes=1):
                when = when.replace(year=when.year + 1)
        else:
            when = base.replace(hour=hour, minute=int(m["min"] or 0), second=0, microsecond=0)
            if when < base - timedelta(minutes=1):
                when += timedelta(days=1)
    except ValueError:
        return None
    return when.astimezone().replace(tzinfo=None)


def window_kind(label: str | None) -> str | None:
    """Which quota window a limit's label names: "5-hour" and "session" the
    five-hour one, "weekly" the seven-day one, "Fable" that model's weekly
    one. None for no label (wrap_up)."""
    if not label:
        return None
    word = label.strip().lower()
    if word in ("5-hour", "session", "five-hour"):
        return FIVE_HOUR
    if word.startswith("week") or word in ("7-day", "seven-day"):
        return SEVEN_DAY
    return f"model:{word.split()[0]}"


def _window(entry: dict | None, kind: str | None) -> dict | None:
    if entry is None:
        return None
    if kind and kind.startswith("model:"):
        alias = kind.removeprefix("model:")
        found = next((m["seven_day"] for m in entry.get("models", []) if m.get("alias") == alias), None)
        return found or entry.get(SEVEN_DAY)
    return entry.get(kind) if kind else None


def _ts(iso: str | None) -> datetime | None:
    """A quota window's reset time (ISO with zone) as naive local."""
    return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None) if iso else None


def hot_window(entry: dict | None) -> tuple[str, datetime] | None:
    """For a stop with no reset time: the hot window that resets last, which
    is the one the session waits for. None when no window is hot."""
    if entry is None:
        return None
    candidates = [(FIVE_HOUR, entry.get(FIVE_HOUR)), (SEVEN_DAY, entry.get(SEVEN_DAY))]
    candidates += [(f"model:{m.get('alias')}", m.get("seven_day")) for m in entry.get("models", [])]
    hot = [(k, _ts(w["resets_at"])) for k, w in candidates if w and w["used_pct"] >= HOT_PCT and w.get("resets_at")]
    return max(hot, key=lambda item: item[1]) if hot else None


def window_reset(entry: dict | None, kind: str | None, reset_at: datetime) -> bool:
    """False when the quota still shows the window open: its reset time is
    still ahead and it is hot, or that reset time is the one the stop named.
    quota.window has already turned a reading whose reset time passed into 0
    used with no reset time. With no reading of the window, the clock decides."""
    w = _window(entry, kind)
    if w is None or not w.get("resets_at"):
        return True
    return not (w["used_pct"] >= HOT_PCT or _ts(w["resets_at"]) <= reset_at + timedelta(minutes=2))


_INPUT_RE = re.compile(r"^\s*❯(.*)$")


def input_box_empty(screen: str | None) -> bool:
    """Whether the screen's last Claude Code input box holds no text."""
    lines = [m.group(1) for m in map(_INPUT_RE.match, (screen or "").splitlines()) if m]
    return bool(lines) and not lines[-1].strip()


def stop_key(session_id: str, stop: dict) -> str:
    return f"{session_id}:{stop['at'].isoformat()}"


@dataclass
class Outcome:
    """What one check did, for Head Jeeves and the log."""

    resumed: list[str]
    uncertain: list[str]
    gave_up: list[tuple[str, str]]


def read_state(path: Path = STATE_PATH) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"stops": {}}
    return data if isinstance(data, dict) and isinstance(data.get("stops"), dict) else {"stops": {}}


def write_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".autoresume.")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


@contextmanager
def _locked(path: Path):
    """One check at a time across processes, so two screens cannot both send."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _record(session, stop: dict, entry: dict | None, now: datetime) -> dict:
    """The ledger entry for a stop seen for the first time. A limit message
    names its own time; a stop that does not waits for the hot window, or
    for nothing when no window is hot any more."""
    kind = window_kind(stop.get("label"))
    reset_at = reset_time(stop.get("reset_text"), stop["at"])
    if reset_at is None or kind is None:
        hot = hot_window(entry)
        if kind is None and hot is not None:
            kind = hot[0]
        if reset_at is None:
            reset_at = hot[1] if hot is not None else stop["at"]
    late = now - reset_at > MAX_LATE
    if late:
        log.info("auto-resume: %s stopped on a limit that reset at %s: too late to resume", session.name, reset_at)
    else:
        log.info("auto-resume: %s (%s) stopped on a limit (%s), reset at %s", session.name, session.account, stop["kind"], reset_at)
    return {
        "session_id": session.session_id,
        "name": session.name,
        "account": session.account,
        "kind": stop["kind"],
        "limit": stop.get("label"),
        "window": kind,
        "stopped_at": stop["at"].isoformat(),
        "reset_at": reset_at.isoformat(),
        "detected_at": now.isoformat(),
        "outcome": TOO_LATE if late else None,
        "resumed_at": None,
        "held_since": None,
        "held_because": None,
        "announced": False,
    }


def _hold_reason(session, stop_entry: dict, entries: dict[str, dict], now: datetime) -> str | None:
    """Why the session cannot be sent the message yet, from what is known
    without touching the terminal."""
    if session.status != "idle":
        return f"its status is {session.status}"
    written = session.subagent_written_at
    if written is not None and now - written < AGENT_QUIET:
        return "an agent of it is still writing"
    if not window_reset(entries.get(stop_entry["account"]), stop_entry["window"], datetime.fromisoformat(stop_entry["reset_at"])):
        return "the quota still shows the window open"
    return None


def check(
    sessions: list,
    entries_for: Callable[[dict[str, datetime]], dict[str, dict]],
    paused_ids: set[str],
    still_sits: Callable[[object, str], bool],
    read_screen: Callable[[object], str | None],
    screen_blocked: Callable[[str | None], bool],
    send: Callable[[object, str], str],
    now: datetime,
    path: Path = STATE_PATH,
) -> Outcome:
    """One pass: record new stops, send what is due, return what happened.
    `entries_for({account: time})` gives the quota entries by account, its
    /usage reading no older than that time where one is named. `send` returns
    what iterm.send_text_result returns."""
    outcome = Outcome([], [], [])
    with _locked(path):
        state = read_state(path)
        stops = state["stops"]
        before = json.dumps(stops, sort_keys=True)
        by_id = {s.session_id: s for s in sessions if not getattr(s, "is_shell", False)}

        fresh = {stop_key(s.session_id, s.limit_stop): s for s in by_id.values() if s.limit_stop is not None and stop_key(s.session_id, s.limit_stop) not in stops}
        if fresh:
            seen = entries_for({})
            for key, s in fresh.items():
                stops[key] = _record(s, s.limit_stop, seen.get(s.account), now)

        for key, e in stops.items():
            if e["outcome"] is not None:
                continue
            if now - datetime.fromisoformat(e["reset_at"]) > MAX_LATE:
                e["outcome"] = TOO_LATE
                continue
            s = by_id.get(e["session_id"])
            if s is None:
                continue  # a scan can miss a live session: this is no proof it left
            if s.limit_stop is None or stop_key(s.session_id, s.limit_stop) != key:
                e["outcome"] = MOVED_ON
                log.info("auto-resume: %s moved on from its limit stop", e["name"])

        due: dict[str, tuple[str, dict]] = {}
        for key, e in stops.items():
            if e["outcome"] is None and now >= datetime.fromisoformat(e["reset_at"]) + RESUME_DELAY:
                if e["session_id"] in by_id and e["session_id"] not in paused_ids:
                    due[e["session_id"]] = (key, e)
        entries = entries_for({e["account"]: datetime.fromisoformat(e["reset_at"]) for _key, e in due.values()}) if due else {}

        for session_id, (key, e) in due.items():
            s = by_id[session_id]
            reason = _hold_reason(s, e, entries, now)
            screen = None
            if reason is None and not still_sits(s, key):
                e["outcome"] = MOVED_ON
                continue
            if reason is None:
                screen = read_screen(s)
                if screen is None:
                    reason = "its screen cannot be read"
                elif screen_blocked(screen):
                    reason = "a dialog is open in it"
                elif not input_box_empty(screen):
                    reason = "its input box holds text"
            if reason is not None:
                e["held_since"] = e["held_since"] or now.isoformat()
                if e["held_because"] != reason:
                    log.info("auto-resume: %s held: %s", e["name"], reason)
                e["held_because"] = reason
                if now - datetime.fromisoformat(e["held_since"]) >= GIVE_UP:
                    e["outcome"] = GAVE_UP
                    log.warning("auto-resume: %s given up: %s", e["name"], reason)
                continue
            e["outcome"] = UNCERTAIN  # on disk before the send: a crash after it must not send again
            write_state(state, path)
            result = send(s, voice.LIMIT_RESET_MESSAGE)
            if result == "sent":
                e["outcome"], e["resumed_at"] = RESUMED, now.isoformat()
                log.info("auto-resume: %s sent the resume message", e["name"])
            elif result == "timeout":
                e["resumed_at"] = now.isoformat()  # may have arrived: never again
                log.warning("auto-resume: %s: the send timed out, the message may have arrived", e["name"])
            else:
                e["outcome"] = None  # nothing was typed: the next check tries again
                e["held_since"] = e["held_since"] or now.isoformat()
                e["held_because"] = f"the send answered {result}"
                log.warning("auto-resume: %s: the send answered %s", e["name"], result)
            write_state(state, path)

        for e in stops.values():
            if e["announced"]:
                continue
            if e["outcome"] == RESUMED:
                outcome.resumed.append(e["name"])
            elif e["outcome"] == UNCERTAIN:
                outcome.uncertain.append(e["name"])
            elif e["outcome"] == GAVE_UP:
                outcome.gave_up.append((e["name"], e["held_because"] or "unknown"))

        cutoff = now - KEEP
        state["stops"] = {k: e for k, e in stops.items() if e["outcome"] is None or datetime.fromisoformat(e["stopped_at"]) >= cutoff}
        if json.dumps(state["stops"], sort_keys=True) != before:
            write_state(state, path)
    return outcome


def mark_announced(path: Path = STATE_PATH) -> None:
    """Head Jeeves has the event: what it reported is not reported again."""
    with _locked(path):
        state = read_state(path)
        for e in state["stops"].values():
            if e["outcome"] in (RESUMED, UNCERTAIN, GAVE_UP):
                e["announced"] = True
        write_state(state, path)
