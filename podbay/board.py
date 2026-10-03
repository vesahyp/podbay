"""The session board: one HTML page from the inventory, written by
`podbay board` to ~/.local/state/podbay/board.html, for Head Jeeves to
publish as an artifact without spending his context on it. Cards grouped by
who must act next (Needs you, Working, Parked), each with the terminal
number, repo, title, state, age and the session's last text.

Tapping a card opens a one-line composer; sending posts a comment on the
artifact addressed `#6 sora: <text>` and sends it to the Claude session
that published the page, Head Jeeves, who passes it on with `podbay send`.
That path is the artifact `comments` capability (sendToClaude), so the
page must be published with `capabilities: {comments: {}}`; without it the
composer says so instead of failing.
"""

from __future__ import annotations

import html
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import voice
from .model import DUE, EMPTY, NEEDS_YOU, PARKED, SHELL, STALLED, WATCHING, WORKING, humanize_age

BOARD_PATH = Path.home() / ".local" / "state" / "podbay" / "board.html"
SUMMARY_CHARS = 300

# Which section a derived state lands in, and the word on its pill.
_SECTIONS = (
    ("you", "Needs you", (NEEDS_YOU, STALLED, DUE)),
    ("work", "Working", (WORKING, WATCHING)),
    ("park", "Parked", (PARKED, EMPTY)),
)
_PILL = {
    NEEDS_YOU: "needs you", STALLED: "stalled", DUE: "due",
    WORKING: "working", WATCHING: "watching", PARKED: "parked", EMPTY: "empty", SHELL: "shell",
}
_ASK_KINDS = {"ask_user_question": "asks", "question_text": "asks", "prompt": "dialog open", "permission": "wants permission"}

_CSS = """
:root {
  --bg: #f3f4f6; --card: #ffffff; --fg: #17191d; --muted: #5d6470; --line: #dde0e6;
  --you: #b4530a; --you-bg: #fdf0e3; --work: #1f6fb2; --work-bg: #e6f0fa; --park: #6b7280; --park-bg: #eceef1;
  --ok: #2c7a4b; --bad: #b3261e;
  --sans: "IBM Plex Sans", system-ui, sans-serif; --mono: "IBM Plex Mono", ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #121418; --card: #1b1e24; --fg: #e8eaee; --muted: #9aa1ad; --line: #2b2f37;
  --you: #f0a35c; --you-bg: #3a2814; --work: #74b2ec; --work-bg: #172a3d; --park: #a3a9b4; --park-bg: #262a31;
  --ok: #6fcf97; --bad: #f28b82; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #121418; --card: #1b1e24; --fg: #e8eaee; --muted: #9aa1ad; --line: #2b2f37;
  --you: #f0a35c; --you-bg: #3a2814; --work: #74b2ec; --work-bg: #172a3d; --park: #a3a9b4; --park-bg: #262a31;
  --ok: #6fcf97; --bad: #f28b82; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font: 15px/1.45 var(--sans); margin: 0; }
main { max-width: 560px; margin: 0 auto; padding: 20px 16px 40px; display: grid; gap: 22px; }
header h1 { font-size: 20px; margin: 0; font-weight: 600; }
header p { margin: 2px 0 0; color: var(--muted); font-size: 13px; }
section { display: grid; gap: 10px; }
h2 { margin: 0; font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; display: grid; gap: 6px; border-left: 4px solid var(--c); cursor: pointer; }
.card:focus-visible { outline: 2px solid var(--c); outline-offset: 2px; }
.top { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.tab { font: 500 13px var(--mono); color: var(--muted); }
.title { font-weight: 600; flex: 1 1 auto; min-width: 0; }
.pill { font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 99px; color: var(--c); background: var(--cb); white-space: nowrap; }
.meta { font-size: 13px; color: var(--muted); }
.what { margin: 0; overflow-wrap: anywhere; }
.ask { margin: 0; font-weight: 500; color: var(--c); }
.you { --c: var(--you); --cb: var(--you-bg); }
.work { --c: var(--work); --cb: var(--work-bg); }
.park { --c: var(--park); --cb: var(--park-bg); }
form.say { display: none; gap: 8px; margin-top: 4px; }
.card.open form.say { display: flex; }
form.say input { flex: 1 1 auto; min-width: 0; font: inherit; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--fg); }
form.say button { font: inherit; font-weight: 600; padding: 8px 14px; border: 0; border-radius: 8px; background: var(--c); color: #fff; }
.status { font-size: 13px; color: var(--muted); margin: 0; min-height: 1.2em; }
.status.ok { color: var(--ok); } .status.bad { color: var(--bad); }
.empty { color: var(--muted); font-size: 14px; }
.ctx { font: 500 12px var(--mono); color: var(--muted); white-space: nowrap; }
.ctx.hot { color: var(--you); } .ctx.full { color: var(--bad); }
.quotas { margin: 6px 0 0; display: grid; gap: 2px; font: 500 13px var(--mono); color: var(--muted); }
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

  document.querySelectorAll(".card[data-target]").forEach(card => {
    const form = card.querySelector("form.say");
    const input = form.querySelector("input");
    const status = card.querySelector(".status");
    const target = card.dataset.target;
    const toggle = () => {
      const open = card.classList.toggle("open");
      if (open) {
        input.focus();
        if (comments === null) status.textContent = "Messaging is not available in this view.";
      }
    };
    card.addEventListener("click", (e) => { if (!e.target.closest("form")) toggle(); });
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) toggle(); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      if (!comments) { status.className = "status bad"; status.textContent = "Messaging is not available in this view."; return; }
      status.className = "status"; status.textContent = "Sending…";
      try {
        const anchor = await comments.anchorFor(card);
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


def _summary(text: str | None) -> str:
    text = " ".join((text or "").split())
    if len(text) > SUMMARY_CHARS:
        text = text[: SUMMARY_CHARS - 1] + "…"
    return text


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


def _ask(session: dict) -> str | None:
    waiting = session.get("waiting_on") or {}
    kind = waiting.get("kind")
    if kind not in _ASK_KINDS:
        return None
    detail = " ".join((waiting.get("detail") or "").split())
    return f"{_ASK_KINDS[kind]}: {detail}" if detail else _ASK_KINDS[kind]


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


def _card(session: dict, css_class: str, now: datetime) -> str:
    tab = f"#{session['tab']}" if session.get("tab") else "#?"
    repo = session.get("work_repo") or session.get("repo") or ""
    target = f"{tab} {repo}".strip()
    meta = " · ".join(x for x in (repo, _age(session, now)) if x)
    pill = _PILL.get(session.get("state") or "", session.get("state") or "")
    if session.get("state") == PARKED and session.get("parked_until"):
        pill = f"until {session['parked_until'][5:16].replace('T', ' ')}"
    ask = _ask(session)
    summary = _summary(session.get("last_text")) or ("never used" if not session.get("has_transcript") else "")
    parts = [
        f'<div class="card {css_class}" data-target="{_esc(target)}" tabindex="0">',
        f'<div class="top"><span class="tab">{_esc(tab)}</span><span class="title">{_esc(session.get("title") or session.get("name"))}</span>{_ctx(session)}<span class="pill">{_esc(pill)}</span></div>',
        f'<div class="meta">{_esc(meta)}</div>',
    ]
    if summary:
        parts.append(f'<p class="what">{_esc(summary)}</p>')
    if ask:
        parts.append(f'<p class="ask">{_esc(ask)}</p>')
    parts.append(
        '<form class="say"><input type="text" maxlength="2000" placeholder="Message for this session…" aria-label="Message">'
        '<button type="submit">Send</button></form><p class="status"></p>'
    )
    parts.append("</div>")
    return "\n".join(parts)


def render(payload: dict, now: datetime | None = None, limits: dict[str, dict] | None = None) -> str:
    """The whole page from an inventory payload (see inventory_payload;
    the sessions need `state`, `age_seconds` and `parked_until`) and the
    per-account limits (sources.newest_limits), for the header."""
    now = now or datetime.now()
    head = next((s for s in payload.get("sessions", []) if s.get("head_jeeves")), None)
    sessions = [s for s in payload.get("sessions", []) if not s.get("head_jeeves")]
    needs = sum(1 for s in sessions if s.get("state") in (NEEDS_YOU, STALLED, DUE))
    sections = []
    for css_class, heading, states in _SECTIONS:
        members = [s for s in sessions if s.get("state") in states]
        if not members:
            continue
        cards = "\n".join(_card(s, css_class, now) for s in members)
        sections.append(f"<section>\n<h2>{heading}</h2>\n{cards}\n</section>")
    body = "\n".join(sections) if sections else '<p class="empty">No live sessions.</p>'
    stamp = f"{now:%-d %b, %H:%M}"
    count = f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}"
    need = f" · {needs} need{'s' if needs == 1 else ''} you" if needs else ""
    head_ctx = f" · Head Jeeves {_ctx(head)}" if head and _ctx(head) else ""
    quotas = _quotas(limits, now)
    return f"""<title>Session Board</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500&display=swap">
<style>{_CSS}</style>
<main>
<header><h1>Sessions</h1><p>{_esc(stamp)} · {count}{need}{head_ctx} · tap a card to message it</p>{quotas}</header>
{body}
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
