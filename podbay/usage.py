"""Per-model weekly usage, from `claude -p /usage` -- the account-wide 5h/week
figures come from the status-line snapshot (see sources.py) and never carry
a per-model breakdown; this is the only place that figure exists at all.
Every account has its own quota, so the run and the cache are per account:
the run inherits the account's CLAUDE_CONFIG_DIR (see accounts.Account.env)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .accounts import Account

CACHE_PATH = Path.home() / ".local" / "state" / "podbay" / "usage.json"


def cache_path_for(account: Account | None) -> Path:
    """usage.json for the default account (and for callers that name none),
    usage-<label>.json for every other one."""
    if account is None or account.is_default:
        return CACHE_PATH
    return CACHE_PATH.with_name(f"usage-{account.label}.json")

# "Current session: 29% used · resets Sep 18 at 11:20am (...)" and the same
# shape for "week (all models)" / "week (<model>)"; the label is whatever
# sits between "Current " and the first ":", so a model name with a space
# in it still matches.
_LINE_RE = re.compile(r"^Current ([^:]+): ([\d.]+)% used(?: · resets (.+))?$")
_WEEK_MODEL_RE = re.compile(r"^week \((.+)\)$")
# "Sep 24 at 9am (Europe/Helsinki)", "Sep 18 at 11:20am (Europe/Helsinki)"
_RESETS_RE = re.compile(r"^(\w{3}) (\d{1,2}) at (\d{1,2})(?::(\d{2}))?(am|pm) \(([^)]+)\)$")


def _parse_resets_at(text: str | None, now: float) -> float | None:
    """Epoch seconds for a /usage reset phrase, or None when the shape is
    unknown. The phrase carries no year: it is the next occurrence of that
    calendar time on the account's clock, so a date that already lies well
    behind `now` rolls into the next year."""
    if not text:
        return None
    m = _RESETS_RE.match(text.strip())
    if not m:
        return None
    mon, day, hour, minute, ampm, tz_name = m.groups()
    try:
        tz = ZoneInfo(tz_name)
        month = datetime.strptime(mon, "%b").month
    except (ZoneInfoNotFoundError, ValueError):
        return None
    hour = int(hour) % 12 + (12 if ampm == "pm" else 0)
    local_now = datetime.fromtimestamp(now, tz)
    try:
        when = datetime(local_now.year, month, int(day), hour, int(minute or 0), tzinfo=tz)
    except ValueError:
        return None
    if when.timestamp() < now - 2 * 86400:
        when = when.replace(year=when.year + 1)
    return when.timestamp()


def _parse_entries(text: str, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    entries: list[dict] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        raw_label, pct_str, resets_text = m.groups()
        raw_label = raw_label.strip()
        if raw_label == "session":
            key, label = "session", None
        elif raw_label == "week (all models)":
            key, label = "week_all", None
        else:
            wm = _WEEK_MODEL_RE.match(raw_label)
            if not wm:
                continue  # not a shape we know: skip rather than guess
            key, label = "week_model", wm.group(1)
        entries.append(
            {
                "key": key,
                "label": label,
                "pct": float(pct_str),
                "resets_text": resets_text,
                "resets_at": _parse_resets_at(resets_text, now),
            }
        )
    return entries


def fetch(timeout: float = 30.0, account: Account | None = None) -> dict | None:
    """Run `claude -p /usage` as `account` (the default one when None) and
    parse its usage lines. None on anything that isn't a clean parse --
    non-zero exit, timeout, no `claude` on PATH, or output with nothing
    recognisable in it. Never raises."""
    env = {**os.environ, **(account.env() if account is not None else {})}
    try:
        result = subprocess.run(
            ["claude", "-p", "/usage"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fetched_at = time.time()
    entries = _parse_entries(result.stdout, fetched_at)
    if not entries:
        return None
    return {"fetched_at": fetched_at, "entries": entries}


def write_cache(data: dict, path: Path | None = None) -> None:
    """Atomic (temp file + rename) because the status line reads this file
    from another process and must never see a half-written one."""
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_cache(path: Path | None = None, max_age: float | None = None) -> dict | None:
    """None when the file is missing, malformed, or (with max_age set) older
    than that many seconds."""
    path = path or CACHE_PATH
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if max_age is not None:
        fetched_at = data.get("fetched_at")
        if fetched_at is None or time.time() - fetched_at > max_age:
            return None
    return data
