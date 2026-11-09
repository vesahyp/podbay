"""The board's lines: what needs the user, grouped by section, from the
inventory and Head Jeeves' headlines. `podbay snapshot` puts them in
state.json, and both views read that one file: the phone app and the
board screen in the TUI (boardview.py, key b). So the Mac and the phone
always show the same lines (docs/adr/0002-one-phone-app.md).

The lines read like a status page for the person who owns the work,
grouped by project, never by session, and every line stands alone: no line
refers to an earlier message, and a link is the full URL.

- decision: only the real questions a session asks.
- shipped: one line per project that shipped today, its full URL, and the
  steps to check it.
- finished: sessions done today that Head Jeeves has not written a line for.
- progress: one line per project, with roughly when.

The lines need judgment, so Head Jeeves supplies them in
~/.local/state/podbay/headlines.json, keyed by session id (see
HEADLINES_PATH and the head-jeeves skill for the format). A session with no
headline falls back to the first sentence of its recap, cleaned of
markdown and never cut mid-word.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .model import DUE, HOME_BASE, NEEDS_YOU, PARKED, STALLED, WATCHING, WORKING, asks_user, humanize_age

STATE_DIR = Path.home() / ".local" / "state" / "podbay"
# {"<session id>": {"kind": "decision|shipped|progress", "text": "...",
#   "link": "https://...", "steps": ["...", "..."], "repo": "sora",
#   "at": "2026-10-03T19:40", "options": ["...", "..."]}}; only kind and
# text are required. Any other kind keeps that session off the board. A
# shipped entry whose session has ended stays on the board under `repo`
# until the user marks it tested (`tested` is set to a time, by
# mark_tested) or SHIPPED_DAYS after `at`.
HEADLINES_PATH = STATE_DIR / "headlines.json"
LINE_CHARS = 160
SHIPPED_DAYS = 7
TESTED_TEXT = "Tested"  # the phone message that means: mark the line tested
KINDS = ("decision", "shipped", "progress")
# The sections in order: Head Jeeves' kinds, plus the finished sessions he
# has not written a line for yet.
_BUCKETS = (*KINDS, "finished")

# A session waiting on one of these has asked the user something.
_ASK_KINDS = {
    "ask_user_question": "asks you a question",
    "question_text": "asks you a question",
    "prompt": "has a dialog open",
    "permission": "wants permission",
}
# The sessions that work on podbay itself (Head Jeeves names his fault
# sessions podbay-fix-<what>): plumbing, not the user's work.
_FIX_PREFIX = "podbay-"


# --- the words ---------------------------------------------------------------

_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPH_RE = re.compile(r"\*\*|__|\*|`")
_HEAD_RE = re.compile(r"(^|\s)#{1,6}\s+")
_BULLET_RE = re.compile(r"(?:^|\s)[-•]\s+(?=\S)")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'“(\[A-ZÅÄÖ0-9])")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def clean_line(text: str | None, limit: int = LINE_CHARS) -> str:
    """The first sentence of an agent's text as one plain line: markdown
    gone, a list cut where it starts, and when it is still too long, cut
    at a word boundary with an ellipsis, never inside a word."""
    text = _LINK_RE.sub(r"\1", text or "")
    text = _EMPH_RE.sub("", text)
    text = _HEAD_RE.sub(r"\1", text)
    text = " ".join(text.split())
    text = _BULLET_RE.split(text)
    text = next((part.strip() for part in text if part.strip()), "")  # a list is never part of the first line
    text = _SENTENCE_END_RE.split(text, maxsplit=1)[0].strip().rstrip(":;,").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > limit // 2 else text[:limit]
    return cut.rstrip(" ,;:.-") + "…"


def load_headlines(path: Path | None = None) -> tuple[dict[str, dict], str | None]:
    """Head Jeeves' lines, and the reason when the file cannot be used. A
    missing file is the normal case: no lines, no error."""
    path = path or HEADLINES_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return {}, f"{path}: {exc}"
    if not isinstance(data, dict):
        return {}, f"{path}: expected an object keyed by session id"
    return {str(k): v for k, v in data.items() if isinstance(v, dict) and isinstance(v.get("text"), str)}, None


def _headline(session: dict, headlines: dict[str, dict]) -> dict | None:
    for key in (session.get("session_id"), session.get("short_id")):
        if key and key in headlines:
            return headlines[key]
    return None


def _project(session: dict, headline: dict | None) -> str:
    """The project a session's line belongs to: its repo, or, for work done
    from the home base with no other repo, what the session is about."""
    if headline and headline.get("repo"):
        return str(headline["repo"])
    repo = session.get("work_repo") or session.get("repo") or ""
    if repo and repo != HOME_BASE:
        return repo
    return session.get("title") or repo or session.get("name") or "?"


def _target(session: dict) -> str:
    """How a reply addresses a session to Head Jeeves: the terminal number
    when known, else the session's name; then its repo. Never `#?`."""
    repo = session.get("work_repo") or session.get("repo") or ""
    if session.get("tab"):
        return f"#{session['tab']} {repo}".strip()
    name = session.get("name") or ""
    return name if not repo or repo == name else f"{name} {repo}".strip()


def _ended_target(repo: str) -> str:
    """How a reply addresses a shipped line whose session has ended: the
    repo, and that the session is gone, so Head Jeeves opens a new one."""
    return f"{repo} (session ended)"


def _ask(session: dict) -> str | None:
    waiting = session.get("waiting_on") or {}
    kind = waiting.get("kind")
    if kind not in _ASK_KINDS:
        return None
    return clean_line(waiting.get("detail")) or _ASK_KINDS[kind]


def _active_today(session: dict, now: datetime) -> bool:
    seconds = session.get("age_seconds")
    return seconds is not None and (now - timedelta(seconds=seconds)).date() == now.date()


def _recent(stamp: object, now: datetime) -> bool:
    """`stamp` is no later than today and less than SHIPPED_DAYS old."""
    try:
        day = datetime.fromisoformat(str(stamp)[:19]).date()
    except ValueError:
        return False
    return 0 <= (now.date() - day).days < SHIPPED_DAYS


def mark_tested(line_id: str, path: Path | None = None, now: datetime | None = None) -> bool:
    """The user tested a shipped line: write `tested` (a time) on its entry
    in headlines.json, which takes it off the board and tells Head Jeeves.
    `line_id` is the board line's id (a session id, or shipped:<key>).
    False when no shipped entry matches or the file cannot be used."""
    path = path or HEADLINES_PATH
    key = line_id.removeprefix("shipped:")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not key:
        return False
    hit = [k for k, v in data.items() if isinstance(v, dict) and v.get("kind") == "shipped"
           and (k == key or (len(key) >= 6 and (k.startswith(key) or key.startswith(k))))]
    if not hit:
        return False
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    for k in hit:
        data[k]["tested"] = stamp
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True


def _when(session: dict, now: datetime) -> str:
    """Roughly when, for an In progress line without a headline."""
    state = session.get("state")
    if state == PARKED and session.get("parked_until"):
        try:
            until = datetime.fromisoformat(session["parked_until"])
        except ValueError:
            return "parked"
        return f"parked until {until:%H:%M}" if until.date() == now.date() else f"parked until {until:%a %-d %b}"
    if state == DUE:
        return "back on the list now"
    if state == STALLED:
        return f"no output for {humanize_age(session.get('age_seconds') or 0)}"
    if state == WATCHING:
        return "waiting on a running job"
    return "working now"


def _classify(session: dict, headline: dict | None, now: datetime) -> str | None:
    """Which section a live session's line goes in, or None for none. A
    finished session is never a decision: only a question is."""
    state = session.get("state")
    if headline:
        kind = headline.get("kind")
        if kind == "shipped" and headline.get("tested"):
            return None  # the user has tested it
        if kind == "decision" and state not in (NEEDS_YOU, STALLED, DUE):
            kind = "progress"  # answered: the session is at work again, so the question is stale
            headline.pop("text", None)
        return kind if kind in KINDS else None
    if (session.get("name") or "").startswith(_FIX_PREFIX):
        return None
    if state == NEEDS_YOU and _ask(session):
        return "decision"
    if state in (WORKING, WATCHING, STALLED, PARKED, DUE):
        return "progress"
    # Done, but only Head Jeeves can say it is ready to test and how.
    if state == NEEDS_YOU and session.get("has_transcript") and _active_today(session, now):
        return "finished"
    return None


def _options(headline: dict | None) -> list[str]:
    """A decision's answer buttons: Head Jeeves' optional `options` list,
    each one short line."""
    raw = (headline or {}).get("options")
    if not isinstance(raw, list):
        return []
    return [clean_line(str(o), 60) for o in raw if str(o).strip()][:4]


def collect(payload: dict, now: datetime | None = None, headlines: dict[str, dict] | None = None) -> dict[str, list[dict]]:
    """The lines of the board by section (decision, shipped, finished,
    progress), each a dict with a stable `id`, `target`, `project`,
    `text`, `link`, `steps`, `when`, `options` and `asks` (its session
    asks the user something: model.asks_user). `podbay snapshot`
    reads this."""
    now = now or datetime.now()
    headlines = {k: dict(v) for k, v in (headlines or {}).items()}
    sessions = [s for s in payload.get("sessions", []) if not s.get("head_jeeves")]

    found: dict[str, list[dict]] = {kind: [] for kind in _BUCKETS}
    for s in sessions:
        headline = _headline(s, headlines)
        kind = _classify(s, headline, now)
        if kind is None:
            continue
        item = {"id": s.get("session_id") or s.get("short_id") or s.get("name") or "", "session": s,
                "target": _target(s), "project": _project(s, headline), "link": None, "steps": None, "when": "",
                "options": _options(headline) if kind == "decision" else [],
                "asks": asks_user(s.get("waiting_on"))}
        if headline and headline.get("text"):
            item.update(text=clean_line(headline["text"], 2 * LINE_CHARS), link=headline.get("link"), steps=headline.get("steps"))
        else:
            item["text"] = (_ask(s) if kind == "decision" else None) or clean_line(s.get("last_text")) or s.get("title") or ""
            if kind == "finished":
                urls = _URL_RE.findall(s.get("last_text") or "")
                item["link"] = urls[0].rstrip(".,;:") if urls else None
            if kind == "progress":
                item["when"] = _when(s, now)
        found[kind].append(item)

    # A shipped line outlives its session until tested, for SHIPPED_DAYS at most.
    live = {s.get("session_id") for s in sessions} | {s.get("short_id") for s in sessions}
    for key, headline in headlines.items():
        if key in live or headline.get("kind") != "shipped" or not headline.get("repo") or headline.get("tested"):
            continue
        if _recent(headline.get("at"), now):
            found["shipped"].append({"id": f"shipped:{key}", "session": {}, "target": _ended_target(str(headline["repo"])),
                                     "project": str(headline["repo"]), "when": "", "options": [], "asks": False,
                                     "text": clean_line(headline["text"], 2 * LINE_CHARS),
                                     "link": headline.get("link"), "steps": headline.get("steps")})
    return found
