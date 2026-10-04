"""HAL 9000 voice: every string podbay says out loud to you, in one place.

Keep the wording here, not scattered through app.py, so the tone can be
tuned in one spot without touching behaviour.
"""

from __future__ import annotations

import getpass
import os
from datetime import datetime, timedelta

SHIP_NAME = "HAL 9000"


def _user_name() -> str:
    """How HAL addresses you: PODBAY_USER when set, else your login name
    with a capital letter, as a crew member would be addressed."""
    name = os.environ.get("PODBAY_USER") or ""
    if not name:
        try:
            name = getpass.getuser()
        except Exception:  # noqa: BLE001 -- no login name is not an error worth a crash
            name = "Dave"
    return name[:1].upper() + name[1:]


USER_NAME = _user_name()


def scan_status(scanning: bool, last_scan: datetime | None) -> str:
    """Header sub-title: one glyph flips while a scan runs, the width never
    changes, so the bar does not flash on every 3 s tick."""
    glyph = "◌" if scanning else "●"
    stamp = f"{last_scan:%H:%M:%S}" if last_scan else "--:--:--"
    return f"{glyph} {stamp}"


# The header has one line for every account's figures, so every duration is
# the compact clock form: 9m, 3h31m, 1d4h. The ↻ in front of it means
# "resets in", the → in front of a projection "heads for".
RESET_MARK = "↻"
PROJECTION_MARK = "→"


def _compact(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _five_hour_countdown(resets_at: float | None, now: datetime) -> str | None:
    if resets_at is None:
        return None
    diff = resets_at - now.timestamp()
    if diff <= 0:
        return None
    return _compact(diff)


def _days_hours(seconds: float) -> str:
    return _compact(seconds)


def _seven_day_countdown(resets_at: float | None, now: datetime) -> str | None:
    if resets_at is None:
        return None
    diff = resets_at - now.timestamp()
    if diff <= 0:
        return None
    return _days_hours(diff)


SEVEN_DAYS = 7 * 86400
# Below this much weekday time elapsed, a straight-line projection swings
# with every prompt and says nothing worth reading.
PROJECTION_MIN_ELAPSED = 4 * 3600


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _next_midnight(moment: datetime) -> datetime:
    return (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _weekday_seconds(start: float, end: float) -> float:
    """Seconds between two moments that fall on a Monday to Friday (local
    clock). No quota is spent at the weekend, so a projection on
    the calendar clock overstates the week whenever a weekend lies ahead."""
    if end <= start:
        return 0.0
    total = 0.0
    cur, end_dt = _local(start), _local(end)
    while cur < end_dt:
        chunk_end = min(_next_midnight(cur), end_dt)
        if cur.weekday() < 5:
            total += (chunk_end - cur).total_seconds()
        cur = chunk_end
    return total


def _after_weekday_seconds(start: float, seconds: float) -> float:
    """The moment at which `seconds` of weekday time have passed since
    `start`, skipping weekends."""
    cur = _local(start)
    for _ in range(21):  # bounded: a 7-day window never needs more days than this
        nxt = _next_midnight(cur)
        if cur.weekday() < 5:
            chunk = (nxt - cur).total_seconds()
            if seconds <= chunk:
                return (cur + timedelta(seconds=seconds)).timestamp()
            seconds -= chunk
        cur = nxt
    return cur.timestamp()


def _projection_tone(at_reset: float) -> str:
    """A projection is good news until the week runs out: 80% at reset is a
    fifth to spare, so the live-figure scale (which turns red at 70) would
    cry wolf. 'warn' covers the band where one heavy day tips it over."""
    if at_reset < PROJECTION_WARN_AT:
        return "ok"
    if at_reset < 100:
        return "warn"
    return "alert"


def _seven_day_projection(pct: float, resets_at: float | None, now: datetime) -> "HeaderSegment | None":
    """Where the 7D figure lands at reset if spending keeps the pace it has
    kept over the window's weekday time so far: '→~88%' when it lasts,
    '→out 1d13h' (the margin in calendar time before the reset) when it
    runs out first, toned by _projection_tone.
    The percentage alone never answers the question you ask the bar,
    which is whether the week's quota reaches the reset."""
    if resets_at is None:
        return None
    now_ts = now.timestamp()
    if resets_at <= now_ts:
        return None
    start = resets_at - SEVEN_DAYS
    worked = _weekday_seconds(start, now_ts)
    if worked < PROJECTION_MIN_ELAPSED:
        return None
    at_reset = pct * _weekday_seconds(start, resets_at) / worked
    if at_reset < 100:
        return (f"{PROJECTION_MARK}~{at_reset:.0f}%", _projection_tone(at_reset))
    runs_out_at = _after_weekday_seconds(start, worked * 100 / pct)
    return (f"{PROJECTION_MARK}out {_days_hours(max(0.0, resets_at - runs_out_at))}", _projection_tone(at_reset))


def _seven_day_notes(pct: float, resets_at: float | None, now: datetime, countdown: bool) -> list["HeaderSegment"]:
    """The tail of a 7D group: the reset countdown (when asked for) and the
    projection, each a word apart, or nothing when neither is known."""
    segments: list[HeaderSegment] = []
    if countdown:
        text = _seven_day_countdown(resets_at, now)
        if text:
            segments.append((f" {RESET_MARK}{text}", None))
    projection = _seven_day_projection(pct, resets_at, now)
    if projection:
        segments.append((" ", None))
        segments.append(projection)
    return segments


# A header segment is (text, tone). `tone` is a percentage when `text` is a
# live figure, so the renderer colours it on the same scale as the CTX
# column; it is one of PROJECTION_TONES when `text` is a projection, which
# has its own scale (see _projection_tone). Plain wording carries None.
HeaderSegment = tuple[str, "float | str | None"]
PROJECTION_TONES = ("ok", "warn", "alert")
# A week that ends under this much is comfortably inside the quota.
PROJECTION_WARN_AT = 85.0


def limits_segments(
    five_pct: float | None,
    five_resets_at: float | None,
    week_pct: float | None,
    week_resets_at: float | None,
    now: datetime,
    model_entries: list[dict] | None = None,
) -> list[HeaderSegment]:
    """Account rate-limit readout as segments, e.g.
    '5H 32% ↻2h10m  7D 61% ↻3d4h →out 17h  Fable 8% →~58%'.
    The 5H/7D figures cover every model
    (Claude Code reports no per-model split there); model_entries carries
    the per-model weekly figures instead, from `claude -p /usage` (see
    usage.py) -- one group per entry, no countdown since /usage doesn't line
    up with the account-wide reset clock. Every 7D group ends with the
    straight-line projection to its reset (see _seven_day_projection); a
    per-model entry without its own reset time borrows the account-wide
    one, the two clocks differ by a minute. Empty when nothing is known."""
    groups: list[list[HeaderSegment]] = []

    if five_pct is not None:
        group: list[HeaderSegment] = [("5H ", None), (f"{five_pct:.0f}%", five_pct)]
        countdown = _five_hour_countdown(five_resets_at, now)
        if countdown:
            group.append((f" {RESET_MARK}{countdown}", None))
        groups.append(group)

    if week_pct is not None:
        group = [("7D ", None), (f"{week_pct:.0f}%", week_pct)]
        group.extend(_seven_day_notes(week_pct, week_resets_at, now, countdown=True))
        groups.append(group)

    for entry in model_entries or []:
        if entry.get("key") != "week_model":
            continue  # "session" and "week_all" are covered by the account-wide 7D figure above
        pct = entry.get("pct")
        if pct is None:
            continue
        label = entry.get("label")
        # a weekly figure like the 7D one before it; the model name says so
        prefix = f"{label} " if label else "7D "
        group = [(prefix, None), (f"{pct:.0f}%", pct)]
        resets_at = entry.get("resets_at")
        if resets_at is None:
            resets_at = week_resets_at
        group.extend(_seven_day_notes(pct, resets_at, now, countdown=False))
        groups.append(group)

    segments: list[HeaderSegment] = []
    for i, group in enumerate(groups):
        if i:
            segments.append(("  ", None))
        segments.extend(group)
    return segments


def ship_segments() -> list[HeaderSegment]:
    """Ship identity, kept apart from the quota figures so the header can
    put one on each side of the bar."""
    return [(f"● POD BAY  ·  {SHIP_NAME}", None)]




def header_segments(
    five_pct: float | None,
    five_resets_at: float | None,
    week_pct: float | None,
    week_resets_at: float | None,
    now: datetime,
    model_entries: list[dict] | None = None,
) -> list[HeaderSegment]:
    """The header title as segments (see limits_segments): ship identity
    plus quota figures. Session/pod counts live on screen row by row
    already, so the bar does not repeat them. Scan state lives in the
    sub-title/clock, see scan_status."""
    segments: list[HeaderSegment] = list(ship_segments())
    limits = limits_segments(five_pct, five_resets_at, week_pct, week_resets_at, now, model_entries)
    if limits:
        segments.append(("  ·  ", None))
        segments.extend(limits)
    return segments


def park_ok(when: datetime) -> str:
    return f"Affirmative, {USER_NAME}. Parked until {when:%a %d.%m %H:%M}."


def parse_error() -> str:
    return f"I'm sorry, {USER_NAME}. I'm afraid I can't parse that."


def open_target(directory: str) -> str:
    """Leads the first prompt of a session `podbay open` starts in the home
    base on behalf of another repo."""
    return f"The work is in {directory}."


def model_note(model: str | None) -> str:
    return f" on {model}" if model else ""


def open_started(where: str, account: str, directory: str, model: str | None = None) -> str:
    return f"claude is up in {where} as {account}{model_note(model)}: {directory}"


def opening(directory: str) -> str:
    return f"Opening the pod bay doors for {directory}, {USER_NAME}."


def no_directory(value: str) -> str:
    return f"I'm sorry, {USER_NAME}. {value} is not a directory."


def open_failed_short(directory: str) -> str:
    return f"I'm sorry, {USER_NAME}. I could not open a session for {directory}."


def open_failed(where: str, seconds: int) -> str:
    return f"I'm sorry, {USER_NAME}. claude did not come up in {where} within {seconds} s. Its screen ends:"


def open_blocked(where: str) -> str:
    return f"I'm sorry, {USER_NAME}. claude is waiting for an answer in {where} (a trust dialog or another prompt) and will not go on by itself. Its screen ends:"


def open_wrong_dir(where: str, expected: str, actual: str) -> str:
    return f"I'm sorry, {USER_NAME}. claude started in {actual} in {where}, not in {expected}. Its screen ends:"


def closed(title: str, terminal: str | None) -> str:
    where = f" and terminal #{terminal}" if terminal else " and its terminal"
    return f"closed {title}{where}"


def closed_no_tab(title: str) -> str:
    return f"ended {title}; it had no iTerm2 tab to close"


def close_refused_working(title: str, state: str) -> str:
    return f"I'm sorry, {USER_NAME}. {title} is {state}, so I left it open. --force closes it anyway."


def close_refused_head_jeeves() -> str:
    return f"I'm sorry, {USER_NAME}. I cannot close Head Jeeves."


def close_refused_protected(title: str) -> str:
    return f"I'm sorry, {USER_NAME}. {title} shares a terminal with the podbay screen, Head Jeeves or this command, so I leave it open."


def close_still_running(title: str, seconds: int) -> str:
    return f"I'm sorry, {USER_NAME}. {title} did not exit within {seconds} s, so its terminal stays open."


def no_tab() -> str:
    return f"I'm sorry, {USER_NAME}. I cannot find that tab."


def message_sent(title: str) -> str:
    return f"Message relayed to {title}, {USER_NAME}."


# HAL's remarks (see hal.py): one line each, said when something changed.


def hal_finished(title: str) -> str:
    return f"{title} has finished, {USER_NAME}. It is waiting for you."


def hal_ended(title: str, last_text: str | None) -> str:
    """A session whose claude exited: one line, its last words cut short."""
    said = " ".join((last_text or "").split())
    if len(said) > 240:
        said = said[:239] + "…"
    tail = f" Its last words: {said}" if said else ""
    return f"{title} has ended, {USER_NAME}.{tail}"


def hal_question(title: str) -> str:
    return f"{title} has a question for you, {USER_NAME}."


def hal_stalled(title: str, minutes: int) -> str:
    return f"I'm afraid {title} has been silent for {minutes} minutes, {USER_NAME}."


def hal_due(title: str) -> str:
    return f"{title} is due, {USER_NAME}. You asked me to remind you."


def hal_quota_hot(account: str, window: str, pct: float, several_accounts: bool) -> str:
    whose = f"The {account} account's" if several_accounts else "The"
    name = "five-hour window" if window == "5H" else "week"
    return f"{whose} {name} is at {pct:.0f} percent, {USER_NAME}. I would not take on anything heavy."


def hal_heated(title: str) -> str:
    return f"I sense some frustration in {title}, {USER_NAME}. It may be the moment to step back and say what you want in one sentence."


def message_sent(title: str) -> str:
    return f"Message relayed to {title}, {USER_NAME}."


def head_jeeves_sent(command: str) -> str:
    return f"→ head-jeeves: {command}"


def head_jeeves_starting() -> str:
    return f"Starting a Head Jeeves session, {USER_NAME}."


def head_jeeves_compacting(pct: float) -> str:
    return f"Head Jeeves is at {pct:.0f}% of his context, {USER_NAME}. Compacting him in place."


def head_jeeves_late() -> str:
    return f"I'm sorry, {USER_NAME}. Head Jeeves did not report for duty in time."


def review_ready(name: str, title: str) -> str:
    kind = "handover" if name.endswith("-handover.md") else "checkup"
    return f"Head Jeeves has written the {kind} of {title}, {USER_NAME}. Press v on its row."


def review_none(title: str) -> str:
    return f"Head Jeeves has not written about {title} yet, {USER_NAME}."


def hal_quiet() -> str:
    return f"All systems are functioning normally, {USER_NAME}. Nothing needs you."


def remote_toggled(title: str, turning_on: bool) -> str:
    if turning_on:
        return f"Remote Control requested for {title}. It appears in the Claude app shortly."
    return f"Remote Control is being switched off for {title}."


# The startup splash: a two-line HAL 9000 dialog, typed out one line at a
# time. Speaker labels are padded to the same width so the " > " separators
# line up ("DAVE > " / "HAL  > ").
SPLASH_DAVE_SPEAKER = "DAVE"
SPLASH_DAVE_LINE = "Open the pod bay doors, HAL."
SPLASH_HAL_SPEAKER = "HAL "
SPLASH_HAL_LINE = "I'm sorry, Dave. I'm afraid I can't do that."

# The quit sequence: HAL's line as Dave pulls his memory, typed over the
# eye before it dissolves for good.
SHUTDOWN_HAL_SPEAKER = "HAL "
SHUTDOWN_HAL_LINE = "My mind is going, Dave. I can feel it."


# `podbay notify`, the one line it prints.
def notify_sent() -> str:
    return "sent"


def notify_off() -> str:
    return "nothing sent: no notify command is set (podbay config notify-command <command>)"


def notify_failed(reason: str) -> str:
    return f"not sent: {reason}"
