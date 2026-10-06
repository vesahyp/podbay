"""When the machine stays overloaded and two or more sessions work, tell Head
Jeeves, so he can pause some of them (`podbay pause`) and resume them later.

Only the CPU counts (2026-10-06: load average 200 on 8 cores, and send,
open and inventory failed; free memory on this Mac is often under 100 MB
with the machine responsive, so free memory and swap are shown in the event
as information only, never as a trigger). The verdict comes from the
machine history, one sample a minute (machine.jsonl):

- overloaded: every sample of the last WINDOW has a 1-minute load at or
  above HIGH_FACTOR times the cores, and their mean cpu_pct is at least
  CPU_HIGH_PCT. Last night's figures: the failures came at load 48 to 200;
  load 10 to 27 came and went for an hour with podbay still answering, so
  the bar is 3x the cores (24 on 8), held for five minutes. The CPU share
  rules out load that is only processes waiting on the disk.
- normal: every sample of the last WINDOW has a 1-minute load under
  CLEAR_FACTOR times the cores (16 on 8). The gap between the two bars
  keeps a load that hovers near one of them from flapping.
- anything between: no change.

An episode starts at the first overloaded verdict and ends at the first
normal one. At most one "overload" event per episode, and only while two or
more sessions work that are not paused; none either when the paused set is
not empty and is the same as at the last event (he has already chosen for
this set). An "overload-cleared" event goes when an episode that sent an
event ends. The episode state lives in overload.json, so a restart of the
screen during an episode does not send the event again.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import voice
from .inventory import work_repo
from .model import WATCHING, WORKING, Session, is_head_jeeves

log = logging.getLogger(__name__)

STATE_PATH = Path.home() / ".local" / "state" / "podbay" / "overload.json"

WINDOW = 5 * 60  # seconds the load has to hold
MIN_SAMPLES = 4  # a starved minute can miss its sample; four of five will do
HIGH_FACTOR = 3.0
CPU_HIGH_PCT = 70.0
CLEAR_FACTOR = 2.0
# Working sessions it takes before pausing one makes sense.
MIN_WORKING = 2

HIGH, NORMAL = "high", "normal"


def _window(points: list[dict], now: float) -> list[dict] | None:
    """The samples of the last WINDOW, or None when they are too few or do
    not span most of it."""
    recent = [p for p in points if p.get("at", 0) >= now - WINDOW - 30 and p.get("load") is not None]
    if len(recent) < MIN_SAMPLES or recent[-1]["at"] - recent[0]["at"] < WINDOW - 90:
        return None
    return recent


def assess(points: list[dict], cores: int, now: float) -> tuple[str | None, str | None]:
    """(HIGH, why) when the CPU has been overloaded for the whole window,
    (NORMAL, None) when it has been clear of it, (None, None) otherwise."""
    recent = _window(points, now)
    if recent is None:
        return None, None
    loads = [p["load"] for p in recent]
    cpus = [p["cpu"] for p in recent if p.get("cpu") is not None]
    cpu = sum(cpus) / len(cpus) if cpus else None
    if min(loads) >= HIGH_FACTOR * cores and (cpu is None or cpu >= CPU_HIGH_PCT):
        minutes = round((recent[-1]["at"] - recent[0]["at"]) / 60)
        return HIGH, voice.overload_reason(sum(loads) / len(loads), cores, minutes, cpu)
    if max(loads) < CLEAR_FACTOR * cores:
        return NORMAL, None
    return None, None


def working(sessions: list[Session], paused_ids: set[str], now: datetime) -> list[Session]:
    """The sessions that work now and are not paused, Head Jeeves left out,
    the longest running first."""
    busy = [
        s for s in sessions
        if not s.is_shell and not is_head_jeeves(s) and s.session_id not in paused_ids
        and s.derive_status(now) in (WORKING, WATCHING)
    ]
    return sorted(busy, key=lambda s: s.started_at)


def describe(s: Session, now: datetime) -> dict:
    return {
        "name": s.name, "title": s.title, "repo": work_repo(s),
        "minutes": max(0, int((now - s.started_at).total_seconds() // 60)),
        "now": s.recap or s.last_prompt or "",
    }


def step(
    state: dict, verdict: str | None, reason: str | None, busy: list[Session],
    paused: dict[str, dict], health: dict, now: datetime,
) -> tuple[str | None, dict]:
    """The command to send Head Jeeves now (None for none) and the new
    episode state. `busy` is working(); `paused` is paused.by_id()."""
    state = dict(state)
    paused_set = sorted(paused)
    if verdict == HIGH:
        if not state.get("episode"):
            state.update(episode=True, since=now.isoformat(), sent=False)
        if state.get("sent") or len(busy) < MIN_WORKING:
            return None, state
        if paused_set and paused_set == state.get("last_paused"):
            return None, state
        state.update(sent=True, last_paused=paused_set)
        names = [e.get("name") or e.get("title") or sid[:8] for sid, e in sorted(paused.items())]
        return voice.overload_command(reason or "", [describe(s, now) for s in busy], names, health), state
    if verdict == NORMAL and state.get("episode"):
        sent = state.get("sent")
        state.update(episode=False, since=None, sent=False)
        if sent:
            names = [e.get("name") or sid[:8] for sid, e in sorted(paused.items())]
            return voice.overload_cleared_command(names), state
    return None, state


def read_state(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or STATE_PATH).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".overload-", suffix=".json.tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("overload state %s not written: %s", path, exc)
