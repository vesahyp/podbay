"""The board: one HTML page from the inventory, written by `podbay board` to
~/.local/state/podbay/board.html, for Head Jeeves to publish as an artifact
without spending his context on it.

It reads like a status page for the person who owns the work, grouped by
project, never by session, and every line stands alone: no line refers to
an earlier message, and a link is the full URL.

- Decisions for you: only the real questions a session asks. Tapping one
  opens the composer to answer it.
- Ready for you to test: one line per project that shipped today, its full
  URL, and the steps to check it.
- In progress: one line per project, with roughly when.
- Machine room (collapsed): every session with its terminal number, state,
  age and context use, and the accounts' usage windows. podbay's own fix
  sessions live only here.

The lines need judgment, so Head Jeeves may supply them in
~/.local/state/podbay/headlines.json, keyed by session id (see
HEADLINES_PATH and the head-jeeves skill for the format). A session with no
headline falls back to the first sentence of its recap, cleaned of
markdown and never cut mid-word.

Sending from the composer posts a comment on the artifact addressed
`#6 sora: <text>` (the session name stands in when the terminal number is
unknown) and sends it to the Claude session that published the page, Head
Jeeves, who passes it on with `podbay send`. That path is the artifact
`comments` capability (sendToClaude), so the page must be published with
`capabilities: {comments: {}}`; without it the composer says so instead of
failing.
"""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from . import voice
from .model import EMPTY, HOME_BASE, NEEDS_YOU, SHELL, STALLED, WATCHING, WORKING, humanize_age

STATE_DIR = Path.home() / ".local" / "state" / "podbay"
BOARD_PATH = STATE_DIR / "board.html"
# {"<session id>": {"kind": "decision|shipped|progress", "text": "...",
#   "link": "https://...", "steps": ["...", "..."], "repo": "sora",
#   "at": "2026-10-03T19:40"}}; only kind and text are required. Any other
# kind keeps that session in the machine room only. A shipped entry whose
# session has ended stays on the board for the day in `at`, under `repo`.
HEADLINES_PATH = STATE_DIR / "headlines.json"
LINE_CHARS = 160
KINDS = ("decision", "shipped", "progress")
# The sections in page order: Head Jeeves' kinds, plus the finished sessions
# he has not written a line for yet.
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
_PILL = {
    NEEDS_YOU: "finished", STALLED: "stalled",
    WORKING: "working", WATCHING: "watching", EMPTY: "empty", SHELL: "shell",
}

_CSS = """
:root {
  --bg: #f4f4f1; --card: #ffffff; --fg: #16181c; --muted: #5f6570; --line: #e0e0da;
  --ask: #a8510b; --ship: #22704a; --prog: #1f63a3;
  --ok: #22704a; --bad: #b3261e;
  --sans: "IBM Plex Sans", system-ui, sans-serif; --mono: "IBM Plex Mono", ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #111316; --card: #1a1d22; --fg: #eceef1; --muted: #9aa1ac; --line: #2a2e35;
  --ask: #f2a65e; --ship: #6fcf97; --prog: #79b4ec;
  --ok: #6fcf97; --bad: #f28b82; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #111316; --card: #1a1d22; --fg: #eceef1; --muted: #9aa1ac; --line: #2a2e35;
  --ask: #f2a65e; --ship: #6fcf97; --prog: #79b4ec;
  --ok: #6fcf97; --bad: #f28b82; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font: 16px/1.45 var(--sans); margin: 0; }
main { max-width: 560px; margin: 0 auto; padding: 24px 16px 48px; display: grid; gap: 26px; }
header h1 { font-size: 24px; margin: 0; font-weight: 600; letter-spacing: -.01em; }
header .when { margin: 2px 0 0; color: var(--muted); font-size: 14px; }
header .sum { margin: 10px 0 0; font-size: 17px; }
section { display: grid; gap: 8px; }
h2 { margin: 0; font-size: 13px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase; color: var(--c); display: flex; gap: 8px; align-items: baseline; }
h2 .n { font: 500 12px var(--mono); color: var(--muted); letter-spacing: 0; }
.ask { --c: var(--ask); }
.ship { --c: var(--ship); }
.prog { --c: var(--prog); }
.done { --c: var(--muted); }
ul.lines > li > div + div { margin-top: 8px; }
ul.lines { list-style: none; margin: 0; padding: 0; background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--c, var(--line)); border-radius: 12px; overflow: hidden; }
ul.lines > li { padding: 12px 14px; border-top: 1px solid var(--line); }
ul.lines > li:first-child { border-top: 0; }
.proj { font-weight: 600; margin-right: 6px; }
.text { overflow-wrap: anywhere; }
.when-tag { color: var(--muted); font-size: 14px; white-space: nowrap; }
a.go { display: block; margin-top: 4px; color: var(--c); font-weight: 500; overflow-wrap: anywhere; }
ol.steps { margin: 6px 0 0; padding-left: 1.3em; color: var(--muted); font-size: 15px; }
ol.steps li { margin: 2px 0; }
.tap { cursor: pointer; }
.tap:focus-visible { outline: 2px solid var(--c, var(--muted)); outline-offset: -2px; }
.hint { color: var(--muted); font-size: 13px; margin: 0; }
.none { color: var(--muted); font-size: 15px; margin: 0; }
form.say { display: none; gap: 8px; margin-top: 10px; }
.open > form.say { display: flex; }
form.say input { flex: 1 1 auto; min-width: 0; font: inherit; font-size: 16px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--fg); }
form.say button { font: inherit; font-weight: 600; padding: 8px 14px; border: 0; border-radius: 8px; background: var(--c, var(--muted)); color: #fff; }
.status { font-size: 13px; color: var(--muted); margin: 4px 0 0; }
.status:empty { display: none; }
.status.ok { color: var(--ok); } .status.bad { color: var(--bad); }
details.room { border-top: 1px solid var(--line); padding-top: 14px; }
details.room > summary { cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase; }
details.room[open] > summary { margin-bottom: 12px; }
.room ul.lines { font-size: 14px; }
.room ul.lines > li { padding: 9px 12px; }
.row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.tab { font: 500 13px var(--mono); color: var(--muted); min-width: 2.2em; }
.row .title { flex: 1 1 10em; min-width: 0; }
.meta { color: var(--muted); font-size: 13px; }
.ctx { font: 500 12px var(--mono); color: var(--muted); white-space: nowrap; }
.ctx.hot { color: var(--ask); } .ctx.full { color: var(--bad); }
.quotas { margin: 0 0 8px; display: grid; gap: 2px; font: 500 13px var(--mono); color: var(--muted); }
.quotas b { font-weight: 500; color: var(--fg); }
"""

_JS = """
(() => {
  let comments;  // undefined until use() answers; null when this view cannot send
  const ready = (window.claude && window.claude.use) ? window.claude.use("comments") : Promise.resolve(null);
  ready.then(c => { comments = c; }, () => { comments = null; });

  const explain = (code) => ({
    consent_required: "Allow comments from this page in the comment panel, then send again.",
    claude_unavailable: "Head Jeeves is not listening right now.",
    forbidden: "Messaging from this page is off for you here.",
    rate_limited: "Too fast. Wait a moment.",
    invalid: "Shorter, plain text only.",
  })[code] || "Could not send.";

  document.querySelectorAll(".tap[data-target]").forEach(item => {
    const form = item.querySelector("form.say");
    const input = form.querySelector("input");
    const status = item.querySelector(".status");
    const target = item.dataset.target;
    const toggle = () => {
      const open = item.classList.toggle("open");
      if (open) {
        input.focus();
        if (comments === null) status.textContent = "Messaging is not available in this view.";
      }
    };
    item.addEventListener("click", (e) => { if (!e.target.closest("form, a")) toggle(); });
    item.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === item) toggle(); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      if (!comments) { status.className = "status bad"; status.textContent = "Messaging is not available in this view."; return; }
      status.className = "status"; status.textContent = "Sending…";
      try {
        const anchor = await comments.anchorFor(item);
        await comments.sendToClaude({ anchor, text: target + ": " + text });
        status.className = "status ok"; status.textContent = "Sent to Head Jeeves.";
        input.value = "";
      } catch (err) {
        status.className = "status bad"; status.textContent = explain(err && err.code);
      }
    });
  });
})();
"""


def _esc(text: str | None) -> str:
    return html.escape(text or "", quote=True)


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
    """How the composer addresses a session to Head Jeeves: the terminal
    number when known, else the session's name; then its repo. Never `#?`."""
    repo = session.get("work_repo") or session.get("repo") or ""
    if session.get("tab"):
        return f"#{session['tab']} {repo}".strip()
    name = session.get("name") or ""
    return name if not repo or repo == name else f"{name} {repo}".strip()


def _ask(session: dict) -> str | None:
    waiting = session.get("waiting_on") or {}
    kind = waiting.get("kind")
    if kind not in _ASK_KINDS:
        return None
    return clean_line(waiting.get("detail")) or _ASK_KINDS[kind]


def _active_today(session: dict, now: datetime) -> bool:
    seconds = session.get("age_seconds")
    return seconds is not None and (now - timedelta(seconds=seconds)).date() == now.date()


def _same_day(stamp: object, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(stamp)[:19]).date() == now.date()
    except ValueError:
        return False


def _when(session: dict, now: datetime) -> str:
    """Roughly when, for an In progress line without a headline."""
    state = session.get("state")
    if state == STALLED:
        return f"no output for {humanize_age(session.get('age_seconds') or 0)}"
    if state == WATCHING:
        return "waiting on a running job"
    return "working now"


def _classify(session: dict, headline: dict | None, now: datetime) -> str | None:
    """Which section a live session's line goes in, or None for the machine
    room only. A finished session is never a decision: only a question is."""
    state = session.get("state")
    if headline:
        kind = headline.get("kind")
        if kind == "decision" and state not in (NEEDS_YOU, STALLED):
            kind = "progress"  # answered: the session is at work again, so the question is stale
            headline.pop("text", None)
        return kind if kind in KINDS else None
    if (session.get("name") or "").startswith(_FIX_PREFIX):
        return None
    if state == NEEDS_YOU and _ask(session):
        return "decision"
    if state in (WORKING, WATCHING, STALLED):
        return "progress"
    # Done, but only Head Jeeves can say it is ready to test and how.
    if state == NEEDS_YOU and session.get("has_transcript") and _active_today(session, now):
        return "finished"
    return None


# --- the page ----------------------------------------------------------------


def _composer(placeholder: str = "Your answer…") -> str:
    return (
        f'<form class="say"><input type="text" maxlength="2000" placeholder="{placeholder}" aria-label="Message">'
        '<button type="submit">Send</button></form><p class="status"></p>'
    )


def _link(url: str | None) -> str:
    """The full URL, shown and tappable, so the line works on its own."""
    if not url:
        return ""
    href = url if re.match(r"https?://", url) else f"https://{url}"
    return f'<a class="go" href="{_esc(href)}" target="_blank" rel="noopener">{_esc(href)}</a>'


def _steps(steps: object) -> str:
    if not isinstance(steps, list):
        return ""
    items = "".join(f"<li>{_esc(clean_line(str(s), 2 * LINE_CHARS))}</li>" for s in steps if str(s).strip())
    return f'<ol class="steps">{items}</ol>' if items else ""


def _decision_item(item: dict) -> str:
    return (
        f'<li class="tap" data-target="{_esc(_target(item["session"]))}" tabindex="0">'
        f'<span class="proj">{_esc(item["project"])}</span><span class="text">{_esc(item["text"])}</span>'
        f'{_link(item.get("link"))}{_composer()}</li>'
    )


def _project_item(project: str, items: list[dict]) -> str:
    parts = []
    for item in items:
        tag = f' <span class="when-tag">{_esc(item["when"])}</span>' if item.get("when") else ""
        line = f'<span class="text">{_esc(item["text"])}</span>{tag}{_link(item.get("link"))}{_steps(item.get("steps"))}'
        parts.append(line if len(items) == 1 else f"<div>{line}</div>")
    return f'<li><span class="proj">{_esc(project)}</span>{"".join(parts)}</li>'


def _section(css: str, heading: str, body: str, count: int) -> str:
    return f'<section class="{css}">\n<h2>{heading}<span class="n">{count}</span></h2>\n{body}\n</section>'


def _grouped(items: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["project"], []).append(item)
    return list(groups.items())


def _ctx(session: dict) -> str:
    pct = session.get("context_pct")
    if not isinstance(pct, (int, float)):
        return ""
    cls = "ctx full" if pct > 90 else "ctx hot" if pct > 70 else "ctx"
    return f'<span class="{cls}">ctx {pct:.0f}%</span>'


def _quotas(limits: dict[str, dict] | None, now: datetime) -> str:
    """One line per account: `claude 5H 36% ↻2h15m  7D 17% ↻1d7h →~17%`,
    the header's own wording (voice.limits_segments)."""
    lines = []
    for account, figures in sorted((limits or {}).items()):
        segments = voice.limits_segments(
            figures.get("five_pct"), figures.get("five_resets_at"), figures.get("week_pct"), figures.get("week_resets_at"), now
        )
        if segments:
            lines.append(f"<div><b>{_esc(account)}</b> {_esc(''.join(text for text, _ in segments))}</div>")
    return f'<div class="quotas">{"".join(lines)}</div>' if lines else ""


def _age(session: dict, now: datetime) -> str:
    """The age in the state's own words: a working session is active, never
    idle, and says so when the work is its subagents'."""
    seconds = session.get("age_seconds")
    state = session.get("state")
    if seconds is None or state == SHELL:
        return ""
    label = humanize_age(seconds)
    if state in (WORKING, WATCHING):
        age = "active now" if label == "now" else f"active {label} ago"
        if (session.get("waiting_on") or {}).get("kind") == "subagents_running":
            age += " · subagent running"
        return age
    if state == STALLED:
        return f"silent {label}" if label != "now" else "silent"
    return "just now" if label == "now" else f"idle {label}"


def _room_row(session: dict, now: datetime) -> str:
    tab = f"#{session['tab']}" if session.get("tab") else "–"
    repo = session.get("work_repo") or session.get("repo") or ""
    state = session.get("state") or ""
    pill = _PILL.get(state, state)
    if state == EMPTY or not session.get("has_transcript"):
        pill = "never used"
    meta = " · ".join(x for x in (repo, pill, _age(session, now)) if x)
    return (
        f'<li class="tap" data-target="{_esc(_target(session))}" tabindex="0"><div class="row">'
        f'<span class="tab">{_esc(tab)}</span><span class="title">{_esc(session.get("title") or session.get("name"))}</span>'
        f'{_ctx(session)}</div><div class="meta">{_esc(meta)}</div>{_composer("Message for this session…")}</li>'
    )


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def render(
    payload: dict,
    now: datetime | None = None,
    limits: dict[str, dict] | None = None,
    headlines: dict[str, dict] | None = None,
) -> str:
    """The whole page from an inventory payload (see inventory_payload;
    the sessions need `state` and `age_seconds`), the
    per-account limits (sources.newest_limits) and Head Jeeves' lines
    (load_headlines)."""
    now = now or datetime.now()
    headlines = {k: dict(v) for k, v in (headlines or {}).items()}
    head = next((s for s in payload.get("sessions", []) if s.get("head_jeeves")), None)
    sessions = [s for s in payload.get("sessions", []) if not s.get("head_jeeves")]

    found: dict[str, list[dict]] = {kind: [] for kind in _BUCKETS}
    for s in sessions:
        headline = _headline(s, headlines)
        kind = _classify(s, headline, now)
        if kind is None:
            continue
        item = {"session": s, "project": _project(s, headline), "link": None, "steps": None, "when": ""}
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

    # A shipped line outlives its session for the rest of the day.
    live = {s.get("session_id") for s in sessions} | {s.get("short_id") for s in sessions}
    for key, headline in headlines.items():
        if key in live or headline.get("kind") != "shipped" or not headline.get("repo"):
            continue
        if _same_day(headline.get("at"), now):
            found["shipped"].append({"session": {}, "project": str(headline["repo"]), "when": "",
                                     "text": clean_line(headline["text"], 2 * LINE_CHARS),
                                     "link": headline.get("link"), "steps": headline.get("steps")})

    decisions = found["decision"]
    shipped, finished, progress = (_grouped(found[k]) for k in ("shipped", "finished", "progress"))
    if decisions:
        body = f'<ul class="lines">\n{chr(10).join(_decision_item(i) for i in decisions)}\n</ul>\n<p class="hint">Tap a question to answer it.</p>'
    else:
        body = '<p class="none">Nothing waits on you.</p>'
    sections = [_section("ask", "Decisions for you", body, len(decisions))]
    for css, heading, groups, empty in (
        ("ship", "Ready for you to test", shipped, "Nothing new to test today."),
        ("done", "Finished today", finished, ""),
        ("prog", "In progress", progress, "Nothing running."),
    ):
        if not groups and not empty:
            continue
        body = (f'<ul class="lines">\n{chr(10).join(_project_item(p, i) for p, i in groups)}\n</ul>'
                if groups else f'<p class="none">{empty}</p>')
        sections.append(_section(css, heading, body, len(groups)))

    rows = "\n".join(_room_row(s, now) for s in sessions)
    head_ctx = f'<p class="hint">Head Jeeves {_ctx(head)}</p>' if head and _ctx(head) else ""
    room = (
        f'<details class="room"><summary>Machine room · {_plural(len(sessions), "session", "sessions")}</summary>\n'
        f"{_quotas(limits, now)}{head_ctx}\n"
        + (f'<ul class="lines">\n{rows}\n</ul>\n<p class="hint">Tap a session to message it.</p>' if rows else '<p class="none">No live sessions.</p>')
        + "</details>"
    )

    summary = " · ".join(x for x in (
        _plural(len(decisions), "decision for you", "decisions for you") if decisions else "Nothing waits on you",
        _plural(len(shipped), "project to test", "projects to test") if shipped else "",
        f"{len(finished)} finished today" if finished else "",
        f"{len(progress)} in progress" if progress else "",
    ) if x)
    stamp = f"{now:%A %-d %b, %H:%M}"
    return f"""<title>Status Board</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500&display=swap">
<style>{_CSS}</style>
<main>
<header><h1>Status</h1><p class="when">{_esc(stamp)}</p><p class="sum">{_esc(summary)}</p></header>
{chr(10).join(sections)}
{room}
</main>
<script>{_JS}</script>
"""


def write(html_text: str, path: Path | None = None) -> Path:
    """Atomic, like every other file podbay writes."""
    path = path or BOARD_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".board-", suffix=".html.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(html_text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def dump_json(payload: dict) -> str:
    return json.dumps(payload, default=str)
