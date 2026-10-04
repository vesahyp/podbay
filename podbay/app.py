"""Textual podbay app + CLI entry point.

`podbay` launches the TUI, `podbay list` prints the same rows as plain
text, `podbay focus <sessionName|pid>` jumps straight to a tab, `podbay
send <sessionName|pid> <text...>` writes a message into a tab without
switching to it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from rich.theme import Theme
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Input, Label, Static

from . import config
from . import glyphs
from . import board
from . import hal
from . import mood
from . import reviewer
from . import iterm as iterm_mod
from .accounts import Account, by_label, discover
from . import layout
from . import logs
from . import notifications
from . import notify as notify_mod
from . import opened as opened_mod
from . import screens
from . import sources
from . import usage
from . import voice
from .inventory import inventory_payload, render_status, render_table
from .model import (
    DUE,
    EMPTY,
    HEAD_JEEVES_NAME,
    HOME_BASE,
    find_session,
    is_head_jeeves,
    NEEDS_YOU,
    PARKED,
    SHELL,
    STALLED,
    STATUS_LABELS,
    WORKING,
    WATCHING,
    Session,
    humanize_age,
    repo_groups,
    shared_repos,
    sort_key,
)
from .splash import FADE_IN_SECONDS, FADE_OUT_SECONDS, ShutdownScreen, SplashScreen
from .state import STATE_PATH, StateStore, ParseError, parse_when

log = logging.getLogger(__name__)

try:
    # Owned by a parallel change; guarded so this module still imports (and
    # this app's own tests still pass) before history.py lands.
    from . import history as history_mod
except ImportError:
    history_mod = None

RECAP_WIDTH = 80
TRANSCRIPT_LIMIT = 40

# HAL 9000: black ground, red eye/accent, dim amber/white body text.
HAL_RED = "#e0201f"
HAL_AMBER = "#ffb000"
HAL_TEXT = "#d9c9a0"
HAL_BLACK = "#000000"
# Header wording that isn't a coloured percentage: dim on purpose, so the
# severity colours are the only thing that pops in the bar.
HAL_HEADER_GRAY = "#808080"

# Rich's default markdown styles are cyan-on-black; restyle to the HAL palette
# (amber accents, no coloured backgrounds) so transcript code is easy on the eyes.
HAL_MARKDOWN_THEME = Theme(
    {
        "markdown.code": f"{HAL_AMBER}",
        "markdown.code_block": f"{HAL_TEXT} on #1a1a1a",
        "markdown.list": HAL_TEXT,
        "markdown.item.bullet": f"bold {HAL_AMBER}",
        "markdown.item.number": HAL_AMBER,
        "markdown.h1": f"bold underline {HAL_AMBER}",
        "markdown.h2": f"bold {HAL_AMBER}",
        "markdown.h3": f"bold {HAL_AMBER}",
        "markdown.h4": f"italic {HAL_AMBER}",
        "markdown.block_quote": f"italic {HAL_TEXT}",
        "markdown.link": HAL_AMBER,
        "markdown.link_url": f"underline {HAL_AMBER}",
        "markdown.table.border": HAL_RED,
        "markdown.table.header": f"bold {HAL_AMBER}",
    }
)

# Row text style by derived status; DUE/NEEDS_YOU/STALLED keep the default
# body colour and only get their state cell coloured.
ROW_STYLES = {
    WORKING: "dim green",
    WATCHING: "dim #2e8b57",
    EMPTY: "dim #5f87af",
    PARKED: "dim #9a9a9a",
    SHELL: "dim #6a6a6a",
}
# Red = a finished turn waiting for a prompt; amber = mid-turn and silent,
# usually a permission prompt.
UNREAD_GLYPH = "●"
UNREAD_STYLE = f"bold {HAL_AMBER}"
STATE_STYLES = {
    DUE: f"bold {HAL_RED}",
    NEEDS_YOU: f"bold {HAL_RED}",
    STALLED: f"bold {HAL_AMBER}",
}

# A terminal read is a ~250ms blocking AppleScript call, so it waits for the
# cursor to settle and the result stays usable for a moment afterwards.
TABLE_REFRESH_SECONDS = 3
# `claude -p /usage` costs no tokens but takes ~5.5s of CPU and leaves a
# transcript behind, and the figure moves in whole percent over minutes.
USAGE_REFRESH_SECONDS = 600

SHELL_READ_DEBOUNCE = 0.25
SHELL_TEXT_TTL = 3.0

# Every column but Recap is fixed; Recap takes what is left so the window
# number stays on screen at the right edge (None = sized here, not fixed).
# Order: " ", State, Age, CTX, Model, Acct, RC, Mood, Dir, Repos, Wait, Title, Recap, Parked, #.
COLUMN_WIDTHS = [1, 11, 4, 4, 12, 8, 2, 2, 14, 18, 4, 38, None, 11, 3]
TITLE_COLUMN = 11
RECAP_COLUMN = 12  # index into COLUMN_WIDTHS of the one sized at runtime
# The last prompts of this session read heated (see mood.py).
HOT_GLYPH = "⚡"
HOT_STYLE = f"bold {HAL_AMBER}"
# Head Jeeves' row: a hue no status uses, whatever his own status is.
HEAD_JEEVES_STYLE = "bold #5fd7ff"
HEAD_JEEVES_GLYPH = "◉"
# How long a just-started Head Jeeves may take to show up idle before the
# queued command is dropped.
HEAD_JEEVES_LAUNCH_TIMEOUT = timedelta(seconds=90)
# A review file that lands within this of a scan is "new": toast it.
REVIEWS_POLL_SECONDS = 5
# Remote Control on: the session can be driven from the phone or the web.
REMOTE_GLYPH = "⇅"
REMOTE_STYLE = "bold #5fd7ff"
# The account label in the header, in front of each account's quota group.
ACCOUNT_TONE = "label"
ACCOUNT_STYLE = f"bold {HAL_AMBER}"
ACCOUNT_SEPARATOR = "  │  "
# Enter on the account pick list is "take this one"; the digits are the
# shortcut shown in front of each row.
ACCOUNT_PICK_KEYS = "123456789"
RECAP_MIN_WIDTH = 12

SEL_GLYPH = "✓"
SEL_STYLE = f"bold {HAL_AMBER}"

# Only a fallback for a headless run: the real frame comes from
# screens.arrange_display() at the moment you press A, so undocking or
# swapping monitors needs no change here.
EXTERNAL_SCREEN_FRAME = layout.Screen(0, 0, 1512, 982)

# One row per user-facing action: (key, textual action name, footer label).
# New actions (snooze rules, filters, ...) are added here only.
ACTIONS = [
    ("slash", "filter", "Filter"),
    ("p", "park", "Park"),
    ("u", "unpark", "Unpark"),
    ("n", "note", "Note"),
    ("t", "toggle_transcript", "Transcript"),
    ("m", "message", "Message"),
    ("space", "toggle_selection", "Select"),
    ("c", "clear_selection", "Clear sel"),
    ("A", "arrange", "Arrange"),
    ("o", "open_claude", "Open claude"),
    ("R", "resume", "Resume"),
    ("h", "history", "History"),
    ("x", "remote", "Remote"),
    ("E", "exit_interview", "Exit interview"),
    ("v", "view_review", "Review"),
    ("r", "refresh", "Refresh"),
    ("q", "quit", "Quit"),
]


def _short_recap(recap: str | None) -> str:
    if not recap:
        return ""
    single_line = " ".join(recap.split())
    if len(single_line) > RECAP_WIDTH:
        return single_line[: RECAP_WIDTH - 1] + "…"
    return single_line


def _parked_str(session: Session) -> str:
    if session.parked_until is None:
        return ""
    return session.parked_until.strftime("%m-%d %H:%M")


def _cell(value: str, style: str | None) -> Text | str:
    return Text(value, style=style) if style else value


# Context-usage colour thresholds, matching the status-line script: green
# while there's plenty of room left, escalating to red near the ceiling.
def _ctx_color(pct: float) -> str:
    if pct <= 40:
        return "green"
    if pct <= 70:
        return "yellow"
    if pct <= 90:
        return "bright_red"
    return "red"


def _ctx_cell(pct: float | None) -> Text | str:
    if pct is None:
        return ""
    return Text(f"{pct:.0f}%", style=_ctx_color(pct))


PROJECTION_TONE_STYLES = {"ok": "green", "warn": "yellow", "alert": "red"}


def _header_text(segments: list) -> Text:
    """Render voice.header_segments()/limits_segments() as one Text: a live
    percentage gets the same severity colour as the CTX column (_ctx_color),
    a projection its own tone (PROJECTION_TONE_STYLES), everything else
    stays dim so that colour is the only thing standing out in the bar."""
    text = Text()
    for content, tone in segments:
        if tone is None:
            style = HAL_HEADER_GRAY
        elif tone == ACCOUNT_TONE:
            style = ACCOUNT_STYLE
        elif isinstance(tone, str):
            style = PROJECTION_TONE_STYLES.get(tone, HAL_HEADER_GRAY)
        else:
            style = _ctx_color(tone)
        text.append(content, style=style)
    return text


def _model_label(model: str | None) -> str:
    """"Opus 5 (1M context)" doesn't fit the column; the context size is the
    only part worth keeping."""
    if not model:
        return ""
    return model.replace(" (1M context)", " 1M").replace(" (1M)", " 1M")


def _dir_label(cwd: str) -> str:
    """Home-relative, and only the last two segments of anything deeper --
    the repo name is what identifies a terminal, not its full path."""
    if not cwd:
        return ""
    home = os.path.expanduser("~")
    path = cwd
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        path = "~/" + path[len(home) + 1:]
    parts = path.split("/")
    if len(parts) > 2:
        return "/".join(parts[-2:])
    return path


def _state_cell(derived: str, selected: bool, row_style: str | None, force_style: str | None = None) -> Text | str:
    """The selection mark takes the status glyph's place: the word next to it
    still says what the state is, so the mark costs no width. `force_style`
    (Head Jeeves' colour) wins over the status colours, selection over that."""
    glyph, word = STATUS_LABELS[derived]
    if selected:
        return Text(f"{SEL_GLYPH} {word}", style=SEL_STYLE)
    style = force_style if force_style is not None else STATE_STYLES.get(derived, row_style)
    return _cell(f"{glyph} {word}", style)


# waiting_on["kind"] values (see model.Session docstring) shortened to fit
# the narrow Wait column.
WAIT_ABBREVIATIONS = {
    "ask_user_question": "ask",
    "permission": "perm",
    "question_text": "q?",
    "prompt": "dlg",
    "subagents_running": "sub",
    "interrupted": "int",
}


def _repos_label(session: Session, groups: list[dict]) -> str:
    """repos_touched minus the home base, each starred when edited, comma-joined,
    with a leading marker when this session shares a repo with another."""
    repos = sorted(r for r in session.repos_touched if r != HOME_BASE)
    text = ",".join(r + ("*" if r in session.repos_edited else "") for r in repos)
    if shared_repos(session, groups):
        return f"⇄ {text}" if text else "⇄"
    return text


def _wait_label(session: Session) -> str:
    if not session.waiting_on:
        return ""
    return WAIT_ABBREVIATIONS.get(session.waiting_on.get("kind") or "", "")


# Background polls that can hold up quit, with the longest they can take: the
# thread waits on its subprocess and Python waits on the thread at exit.
POLL_LIMITS = {
    "session scan": 15.0,
    "usage": 30.0,
}


def progress_bar(elapsed: float, limit: float, width: int = 20) -> str:
    filled = min(width, int(width * elapsed / limit)) if limit > 0 else 0
    return "▓" * filled + "░" * (width - filled)


class QuitWaitScreen(ModalScreen[None]):
    """Quit while a poll still runs: one line per poll with how long it has
    run against its limit. The app exits when the last one ends; any key
    force-quits (podbay's own child processes are stopped first)."""

    DEFAULT_CSS = """
    QuitWaitScreen {
        align: center middle;
        background: #000000;
    }
    #quit-wait {
        width: 70;
        height: auto;
        border: round #e0201f;
        padding: 1 2;
        background: #000000;
        color: #d9c9a0;
    }
    """

    def __init__(self, polls, on_done, on_force):
        super().__init__()
        self._polls = polls  # () -> {label: started (monotonic)}
        self._on_done = on_done
        self._on_force = on_force

    def compose(self) -> ComposeResult:
        yield Static(id="quit-wait")

    def on_mount(self) -> None:
        self._tick()
        self.set_interval(0.25, self._tick)

    def _tick(self) -> None:
        polls = self._polls()
        if not polls:
            self._on_done()
            return
        now = time.monotonic()
        text = Text("Waiting for background work to finish\n\n", style=f"bold {HAL_AMBER}")
        for label, started in sorted(polls.items()):
            elapsed = now - started
            limit = POLL_LIMITS.get(label, 0.0)
            line = f"{label:<13} {elapsed:4.0f} s"
            if limit:
                line += f" of max {limit:.0f} s  {progress_bar(elapsed, limit)}"
            text.append(line + "\n", style=HAL_TEXT)
        text.append("\nany key: quit now", style="dim")
        self.query_one("#quit-wait", Static).update(text)

    def on_key(self, event) -> None:
        event.stop()
        self._on_force()


def build_rows(sessions: list[Session], now: datetime, selected: set[str] | None = None) -> list[dict]:
    ordered = sorted(sessions, key=lambda s: sort_key(s, now))
    selected = selected or set()
    groups = repo_groups(sessions)
    rows = []
    for s in ordered:
        derived = s.derive_status(now)
        head = is_head_jeeves(s)
        row_style = HEAD_JEEVES_STYLE if head else ROW_STYLES.get(derived)
        rows.append(
            {
                "session_id": s.session_id,
                "new": _cell(UNREAD_GLYPH, UNREAD_STYLE) if s.unread and not head else "",
                "state": _state_cell(derived, s.session_id in selected, row_style, force_style=HEAD_JEEVES_STYLE if head else None),
                "age": _cell(humanize_age(s.age_seconds(now)), row_style),
                "ctx": _ctx_cell(s.context_pct),
                "model": _cell(_model_label(s.model), row_style),
                "account": _cell("" if s.is_shell else s.account, row_style),
                "remote": _cell(REMOTE_GLYPH, REMOTE_STYLE) if s.remote_session_id else "",
                "mood": _cell(HOT_GLYPH, HOT_STYLE) if not head and mood.is_hot(s.recent_prompts) else "",
                "dir": _cell(_dir_label(s.cwd), row_style),
                "repos": _cell(_repos_label(s, groups), row_style),
                "wait": _cell(_wait_label(s), row_style),
                "title": _cell(f"{HEAD_JEEVES_GLYPH} {s.title}" if head else s.title, row_style),
                "recap": _cell(_short_recap(s.recap), row_style),
                "parked": _cell(_parked_str(s), row_style),
                "win": _cell(f"#{s.terminal}" if s.terminal else "", row_style),
                "session": s,
            }
        )
    return rows


def _detail_line(pairs: list[tuple[str, str | None]], row_style: str | None = None) -> Text:
    """One line of 'a · b · c', skipping the parts that have no value."""
    values = [value for _label, value in pairs if value]
    return Text(" · ".join(values), style=row_style or "")


def _detail_text(session: Session, now: datetime) -> Text:
    """The left panel: a few short grouped lines rather than one label:value
    block per field. Empty fields are left out instead of printed as '-'."""
    derived = session.derive_status(now)
    glyph, word = STATUS_LABELS[derived]
    text = Text()
    text.append(session.title + "\n", style=f"bold {HAL_TEXT}")

    status_bits = [f"{glyph} {word}", humanize_age(session.age_seconds(now))]
    if session.terminal:
        status_bits.append(f"#{session.terminal}")
    text.append(" · ".join(status_bits) + "\n", style=STATE_STYLES.get(derived, HAL_AMBER))

    text.append("\n")
    place = [_dir_label(session.cwd) or session.cwd]
    if session.git_branch:
        place.append(session.git_branch)
    text.append(" · ".join(place) + "\n", style=HAL_TEXT)

    if getattr(session, "is_shell", False):
        if session.tty:
            text.append(session.tty + "\n", style="dim")
        return text

    spec = [session.account, _model_label(session.model)]
    if session.effort:
        spec.append(session.effort)
    if session.context_pct is not None:
        spec.append(f"ctx {session.context_pct:.0f}%")
    if spec:
        text.append(" · ".join(x for x in spec if x) + "\n", style="dim")
    if session.remote_url:
        text.append(f"{REMOTE_GLYPH} remote control on · ", style=REMOTE_STYLE)
        text.append(session.remote_url + "\n", style="dim")
    if mood.is_hot(session.recent_prompts):
        text.append(f"{HOT_GLYPH} the last prompts read heated\n", style=HOT_STYLE)

    if session.parked_until is not None:
        text.append(f"\nparked until {_parked_str(session)}\n", style=HAL_AMBER)
    if session.note:
        text.append("\n" + session.note + "\n", style=HAL_AMBER)
    if session.recap:
        text.append("\n")
        text.append(session.recap.strip() + "\n", style=HAL_TEXT)
    return text


def _render_transcript(entries: list[dict]) -> RenderableType:
    """Assistant turns are markdown (bold, inline code, lists, fences), so
    they render through rich Markdown; user and tool lines stay literal."""
    if not entries:
        return Text("(no conversation yet)")
    blocks: list[RenderableType] = []
    for entry in entries:
        text = entry["text"]
        role = entry["role"]
        if role == "user":
            line = Text(f"▶ {voice.USER_NAME.upper()}  ", style="bold")
            line.append(text)
            blocks.append(line)
        elif role == "assistant":
            blocks.append(Text("● HAL", style="bold"))
            blocks.append(Markdown(text, code_theme="ansi_dark", inline_code_theme="ansi_dark"))
        else:  # tool
            blocks.append(Text(text, style="dim"))
        blocks.append(Text(""))
    return Group(*blocks[:-1])


class AccountScreen(ModalScreen["Account | None"]):
    """Which account to start claude as: one row per config dir Claude has
    been run from, the highlighted session's account pre-selected. Enter or
    the row's digit picks, Escape cancels. Only shown when there is more
    than one account."""

    DEFAULT_CSS = """
    AccountScreen {
        align: center middle;
        background: transparent 60%;
    }
    #account-box {
        width: 60;
        height: auto;
        border: round #e0201f;
        padding: 1 2;
        background: #000000;
        color: #d9c9a0;
    }
    #account-box Label {
        color: #d9c9a0;
        margin-bottom: 1;
    }
    #account-table {
        height: auto;
        background: #000000;
        color: #d9c9a0;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, accounts: list[Account], current: Account | None, live_counts: dict[str, int] | None = None):
        super().__init__()
        self._accounts = accounts
        self._current = current
        self._live_counts = live_counts or {}
        self._done = False

    def _finish(self, value) -> None:
        if self._done:
            return
        self._done = True
        self.dismiss(value)

    def compose(self) -> ComposeResult:
        with Vertical(id="account-box"):
            yield Label("Start claude as which account?  enter or digit picks, esc cancels")
            yield DataTable(id="account-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#account-table", DataTable)
        table.add_columns(" ", "Account", "Config dir", "Live")
        home = os.path.expanduser("~")
        for i, account in enumerate(self._accounts):
            shown = str(account.config_dir)
            if shown.startswith(home + "/"):
                shown = "~" + shown[len(home):]
            live = self._live_counts.get(account.label, 0)
            table.add_row(
                ACCOUNT_PICK_KEYS[i] if i < len(ACCOUNT_PICK_KEYS) else "",
                Text(account.label, style=ACCOUNT_STYLE),
                shown,
                str(live) if live else "",
                key=account.label,
            )
        if self._current is not None and self._current in self._accounts:
            table.move_cursor(row=self._accounts.index(self._current))
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if 0 <= event.cursor_row < len(self._accounts):
            self._finish(self._accounts[event.cursor_row])

    def on_key(self, event) -> None:
        if event.character and event.character in ACCOUNT_PICK_KEYS:
            index = ACCOUNT_PICK_KEYS.index(event.character)
            if index < len(self._accounts):
                event.stop()
                self._finish(self._accounts[index])

    def action_cancel(self) -> None:
        self._finish(None)


class PromptScreen(ModalScreen[str | None]):
    """A single-line input modal. Enter submits, Escape cancels."""

    DEFAULT_CSS = """
    PromptScreen {
        align: center middle;
        background: transparent 60%;
    }
    #prompt-box {
        width: 60;
        height: auto;
        border: round #e0201f;
        padding: 1 2;
        background: #000000;
        color: #d9c9a0;
    }
    #prompt-box Label {
        color: #d9c9a0;
    }
    #prompt-box Input {
        background: #000000;
        color: #d9c9a0;
        border: solid #e0201f;
    }
    #prompt-glyph {
        width: 100%;
        content-align: center middle;
    }
    #prompt-caption {
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, initial: str = "", glyph: glyphs.Glyph | None = None):
        super().__init__()
        self._prompt = prompt
        self._initial = initial
        self._glyph = glyph
        self._done = False

    def _finish(self, value: str | None) -> None:
        # Enter and Escape can race; dismissing twice raises in Textual.
        if self._done:
            return
        self._done = True
        self.dismiss(value)

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            if self._glyph is not None:
                yield Static(self._glyph.art, id="prompt-glyph")
                yield Static(f"[bold {self._glyph.accent}]{self._glyph.caption}[/]", id="prompt-caption")
            yield Label(self._prompt)
            yield Input(value=self._initial, id="prompt-input")

    def on_mount(self) -> None:
        if self._glyph is not None:
            # The border and the input frame take the glyph's accent so the
            # whole modal, not just the art, says which prompt this is.
            self.query_one("#prompt-box").styles.border = ("round", self._glyph.accent)
            self.query_one("#prompt-input", Input).styles.border = ("solid", self._glyph.accent)
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._finish(event.value)

    def action_cancel(self) -> None:
        self._finish(None)


RESUME_AGE_WIDTH = 5
RESUME_DIR_WIDTH = 16
RESUME_TITLE_WIDTH = 62
RESUME_SEARCH_DEBOUNCE = 0.2
RESUME_SEARCH_MIN_LEN = 3
RESUME_CONVERSATION_LABEL = "Found in conversation"


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _dir_basename(cwd: str) -> str:
    return os.path.basename(cwd.rstrip("/")) or cwd


def _fuzzy_score(haystack: str, query: str) -> float | None:
    """fzf-style subsequence match, case-insensitive: every query character
    must occur in haystack in order (not necessarily contiguous). None means
    no match; otherwise lower is better -- a tight, early match beats a
    scattered or late one."""
    if not query:
        return 0.0
    h = haystack.lower()
    q = query.lower()
    pos = -1
    first = None
    prev = None
    gap_penalty = 0
    for ch in q:
        idx = h.find(ch, pos + 1)
        if idx == -1:
            return None
        if first is None:
            first = idx
        if prev is not None:
            gap_penalty += idx - prev - 1
        prev = idx
        pos = idx
    span = pos - first + 1
    return span + gap_penalty + first * 0.01


def _filter_rows(rows: list[dict], query: str) -> list[dict]:
    """Rows matching `query` as a fuzzy subsequence of "title + directory",
    ranked by match quality then recency. Empty query keeps the incoming
    (already recency-sorted) order untouched."""
    query = query.strip()
    if not query:
        return rows
    scored = []
    for row in rows:
        haystack = f"{row['title']} {_dir_basename(row['cwd'])}"
        score = _fuzzy_score(haystack, query)
        if score is not None:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], -item[1]["sort_ts"].timestamp()))
    return [row for _, row in scored]


def _snapshot_resume_rows(entries: list[dict], now: datetime) -> list[dict]:
    """Snapshot entries (see StateStore.get_snapshot) to resume rows, newest first."""
    rows = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["seen_at"]) if e.get("seen_at") else now
        except ValueError:
            ts = now
        rows.append({
            "session_id": e.get("session_id"),
            "cwd": e.get("cwd") or "",
            "title": e.get("title") or e.get("session_id") or "",
            "account": e.get("account"),
            "sort_ts": ts,
        })
    rows.sort(key=lambda r: r["sort_ts"], reverse=True)
    return rows


def _history_resume_rows(entries: list) -> list[dict]:
    """history.PastSession objects to resume rows, newest first."""
    rows = [
        {"session_id": e.session_id, "cwd": e.cwd, "title": e.title, "account": getattr(e, "account", None), "sort_ts": e.ended_at}
        for e in entries
    ]
    rows.sort(key=lambda r: r["sort_ts"], reverse=True)
    return rows


class ReviewScreen(ModalScreen[None]):
    """A saved Head Jeeves review (exit interview or checkup), as Markdown,
    scrollable. Escape or v closes it."""

    DEFAULT_CSS = """
    ReviewScreen {
        align: center middle;
        background: transparent 60%;
    }
    #review-box {
        width: 110;
        height: auto;
        max-height: 40;
        border: round #e0201f;
        padding: 1 2;
        background: #000000;
        color: #d9c9a0;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close"), Binding("v", "close", "Close", show=False)]

    def __init__(self, markdown: str, path: Path):
        super().__init__()
        self._markdown = markdown
        self._path = path

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="review-box"):
            yield Static(Markdown(self._markdown, code_theme="ansi_dark", inline_code_theme="ansi_dark"))
            yield Static(Text(f"\n{self._path}", style="dim"))

    def on_mount(self) -> None:
        self.query_one("#review-box", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


class HistoryScreen(ModalScreen[None]):
    """Today's notifications and sent warnings, newest first. Escape or h
    closes it."""

    DEFAULT_CSS = """
    HistoryScreen {
        align: center middle;
        background: transparent 60%;
    }
    #history-box {
        width: 110;
        height: auto;
        max-height: 36;
        border: round #e0201f;
        padding: 1 2;
        background: #000000;
        color: #d9c9a0;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close"), Binding("h", "close", "Close", show=False)]

    def __init__(self, entries: list[tuple[datetime, str, str]]):
        super().__init__()
        self._entries = entries

    def compose(self) -> ComposeResult:
        text = Text()
        if not self._entries:
            text.append("No notifications today.")
        for i, (when, kind, message) in enumerate(self._entries):
            if i:
                text.append("\n")
            style = {"error": "bold #e0201f", "warning": "#e0a01f", "sent": "#7fb0d0"}.get(kind, "")
            text.append(f"{when:%H:%M:%S} {kind:<11} ", style=style)
            text.append(message)
        with VerticalScroll(id="history-box"):
            yield Static(text)

    def action_close(self) -> None:
        self.dismiss(None)


class ResumeScreen(ModalScreen[list[dict] | None]):
    """Pick list over two fixed groups (snapshot-before-restart, then older
    history) plus a third, search-only group of conversation hits. Typing in
    the search box fuzzy-filters the first two groups at once (synchronous,
    subsequence match over title+directory) and, once the query is 3+ chars
    and settles for a moment, kicks off a background content search whose
    results merge in as "Found in conversation". space toggles the
    highlighted row, enter opens every ticked row (or just the highlighted
    one if none is ticked), escape clears a live query before it cancels the
    modal. Divider rows (group labels) aren't selectable."""

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
        background: transparent 60%;
    }
    #resume-box {
        width: 96;
        height: auto;
        max-height: 34;
        border: round #e0201f;
        padding: 1 2;
        background: #000000;
        color: #d9c9a0;
    }
    #resume-box Label {
        color: #d9c9a0;
    }
    #resume-search {
        background: #000000;
        color: #d9c9a0;
        border: solid #e0201f;
        margin-bottom: 1;
    }
    #resume-table {
        height: auto;
        max-height: 28;
        background: #000000;
        color: #d9c9a0;
    }
    """

    # DataTable owns "enter" itself (select_cursor -> RowSelected); confirm
    # is wired through on_data_table_row_selected below, same pattern the
    # main table uses for its own Enter-to-focus action. up/down/tab are
    # bound here too because the search Input has focus by default and does
    # not itself act on them, so without these they would just be typed --
    # they only reach these actions when the focused widget didn't already
    # handle them itself (i.e. never once the table -- which owns cursor
    # movement -- has focus).
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("space", "toggle", "Select"),
        Binding("tab", "focus_table", "Table"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, groups: list[tuple[str, list[dict]]], now: datetime | None = None):
        super().__init__()
        self._now = now or datetime.now()
        labels = list(groups) + [("", []), ("", [])]
        self._before_label, self._before_rows = labels[0]
        self._older_label, self._older_rows = labels[1]
        self._conv_matches: list = []  # history.SessionMatch, from the search worker
        self._entries: list[dict] = []  # divider rows and pickable rows, in display order
        self._selected: set[str] = set()  # session_ids -- resilient to re-sorting/filtering
        self._query = ""
        self._search_token = 0
        self._debounce_timer = None
        self._done = False
        self._col_keys: list = []

    def _finish(self, value: list[dict] | None) -> None:
        if self._done:
            return
        self._done = True
        self.dismiss(value)

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-box"):
            yield Label("Resume: type to search  ↑↓ move  enter open  tab+space multi  esc back")
            yield Input(placeholder="filter by title/directory, or search conversations...", id="resume-search")
            yield DataTable(id="resume-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#resume-table", DataTable)
        # fixed widths: an untruncated title pushes the other columns off the
        # modal and leaves the list scrolling sideways
        self._col_keys = [
            table.add_column("Age", width=RESUME_AGE_WIDTH),
            table.add_column("Dir", width=RESUME_DIR_WIDTH),
            table.add_column("Title", width=RESUME_TITLE_WIDTH),
        ]
        self._rebuild_entries()
        self.query_one("#resume-search", Input).focus()

    def _rebuild_entries(self) -> None:
        """Recompute the displayed rows from the current query and whatever
        conversation matches have arrived so far, then redraw the table."""
        query = self._query.strip()
        before_rows = _filter_rows(self._before_rows, query)
        older_rows = _filter_rows(self._older_rows, query)
        visible_ids = {r["session_id"] for r in before_rows} | {r["session_id"] for r in older_rows}

        conv_rows = []
        seen_ids: set[str] = set()
        for match in self._conv_matches:
            sid = match.session.session_id
            if sid in visible_ids or sid in seen_ids:
                continue  # already offered in one of the first two groups
            seen_ids.add(sid)
            conv_rows.append({
                "session_id": sid,
                "cwd": match.session.cwd,
                "title": match.session.title,
                "account": getattr(match.session, "account", None),
                "sort_ts": match.session.ended_at,
                "snippet": match.snippet,
            })

        groups = [(self._before_label, before_rows), (self._older_label, older_rows)]
        if conv_rows:
            groups.append((RESUME_CONVERSATION_LABEL, conv_rows))

        self._entries = []
        for label, rows in groups:
            if not rows:
                continue
            self._entries.append({"divider": True, "label": label})
            self._entries.extend(rows)

        live_ids = {e["session_id"] for e in self._entries if not e.get("divider")}
        self._selected &= live_ids
        self._redraw_table()

    def _redraw_table(self) -> None:
        table = self.query_one("#resume-table", DataTable)
        previous_id = self._current_session_id()
        table.clear()
        for idx, entry in enumerate(self._entries):
            if entry.get("divider"):
                table.add_row("", "", _cell(entry["label"], "bold"), key=str(idx))
                continue
            age = humanize_age((self._now - entry["sort_ts"]).total_seconds())
            dirname = _clip(_dir_basename(entry["cwd"]), RESUME_DIR_WIDTH)
            table.add_row(age, dirname, self._title_cell(entry), key=str(idx))

        target = None
        if previous_id is not None:
            target = next((i for i, e in enumerate(self._entries) if e.get("session_id") == previous_id), None)
        if target is None:
            target = next((i for i, e in enumerate(self._entries) if not e.get("divider")), None)
        if target is not None:
            table.move_cursor(row=target)

    def _current_session_id(self) -> str | None:
        table = self.query_one("#resume-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._entries):
            return None
        return self._entries[table.cursor_row].get("session_id")

    def _title_cell(self, entry: dict) -> Text | str:
        selected = entry.get("session_id") in self._selected
        mark = f"{SEL_GLYPH} " if selected else "  "
        inner_width = RESUME_TITLE_WIDTH - 2
        snippet = entry.get("snippet")
        if snippet and snippet != entry["title"]:
            title = _clip(entry["title"], inner_width // 2)
            rest_width = max(inner_width - len(title) - 1, 8)
            text = Text(mark + title, style=SEL_STYLE if selected else None)
            text.append(" " + _clip(snippet, rest_width), style="dim")
            return text
        title = _clip(entry["title"], inner_width)
        return Text(mark + title, style=SEL_STYLE) if selected else mark + title

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()  # otherwise bubbles to PodbayApp's own row-selected handler
        self.action_confirm()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "resume-search":
            return
        event.stop()
        self.action_confirm()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "resume-search":
            return
        self._query = event.value
        self._search_token += 1
        if len(self._query.strip()) < RESUME_SEARCH_MIN_LEN:
            self._conv_matches = []
        self._rebuild_entries()

        if self._debounce_timer is not None:
            self._debounce_timer.stop()
            self._debounce_timer = None
        query = self._query.strip()
        if len(query) >= RESUME_SEARCH_MIN_LEN:
            token = self._search_token
            self._debounce_timer = self.set_timer(RESUME_SEARCH_DEBOUNCE, lambda: self._start_search(query, token))

    def _start_search(self, query: str, token: int) -> None:
        if token != self._search_token:
            return  # the query moved on while we were waiting out the debounce
        self._search_worker(query, token)

    @work(thread=True, exclusive=True, group="resume-search")
    def _search_worker(self, query: str, token: int) -> None:
        search_sessions = getattr(history_mod, "search_sessions", None) if history_mod else None
        matches: list = []
        if search_sessions is not None:
            try:
                matches = search_sessions(query, limit=40)
            except Exception:
                log.warning("resume search failed for %r", query, exc_info=True)
                matches = []
        self.app.call_from_thread(self._apply_search_results, token, matches)

    def _apply_search_results(self, token: int, matches: list) -> None:
        if token != self._search_token:
            return  # a newer query has since started typing
        self._conv_matches = matches
        self._rebuild_entries()

    def action_cancel(self) -> None:
        if self._query:
            if self._debounce_timer is not None:
                self._debounce_timer.stop()
                self._debounce_timer = None
            self._query = ""
            self._search_token += 1
            self._conv_matches = []
            self.query_one("#resume-search", Input).value = ""
            self._rebuild_entries()
            return
        self._finish(None)

    def action_focus_table(self) -> None:
        self.query_one("#resume-table", DataTable).focus()

    def action_cursor_up(self) -> None:
        self.query_one("#resume-table", DataTable).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#resume-table", DataTable).action_cursor_down()

    def action_toggle(self) -> None:
        table = self.query_one("#resume-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._entries):
            return
        idx = table.cursor_row
        entry = self._entries[idx]
        if entry.get("divider"):
            return
        sid = entry["session_id"]
        if sid in self._selected:
            self._selected.discard(sid)
        else:
            self._selected.add(sid)
        table.update_cell(str(idx), self._col_keys[2], self._title_cell(entry))

    def action_confirm(self) -> None:
        if self._selected:
            selected = [e for e in self._entries if e.get("session_id") in self._selected]
        else:
            table = self.query_one("#resume-table", DataTable)
            idx = table.cursor_row
            if idx is None or idx >= len(self._entries) or self._entries[idx].get("divider"):
                self._finish([])
                return
            selected = [self._entries[idx]]
        self._finish(selected)


class PodbayHeader(Widget):
    """Single-line ship console: ship identity left, quota figures centred,
    scan clock right. Textual's own Header only takes one style for the
    whole title; severity colouring needs per-segment styles, hence this
    stand-in."""

    DEFAULT_CSS = f"""
    PodbayHeader {{
        dock: top;
        width: 100%;
        height: 1;
        background: {HAL_BLACK};
        layout: horizontal;
    }}
    PodbayHeader > Static {{
        height: 1;
        background: {HAL_BLACK};
    }}
    PodbayHeader > #header-ship {{
        width: auto;
        content-align: left middle;
        padding: 0 1;
    }}
    PodbayHeader > #header-quotas {{
        width: 1fr;
        content-align: center middle;
        text-align: center;
    }}
    PodbayHeader > #header-clock {{
        width: auto;
        content-align: right middle;
        padding: 0 1;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(id="header-ship")
        yield Static(id="header-quotas")
        yield Static(id="header-clock")

    def update_ship(self, text: Text) -> None:
        self.query_one("#header-ship", Static).update(text)

    def update_quotas(self, text: Text) -> None:
        self.query_one("#header-quotas", Static).update(text)

    def update_clock(self, text: str) -> None:
        self.query_one("#header-clock", Static).update(Text(text, style=HAL_HEADER_GRAY))


class PodbayApp(App):
    TITLE = "podbay"
    BINDINGS = [
        Binding("enter", "focus_selected", "Focus"),
        Binding("escape", "escape_pressed", "", show=False),
        *[Binding(key, action, label) for key, action, label in ACTIONS],
    ]
    CSS = f"""
    Screen {{
        background: {HAL_BLACK};
        color: {HAL_TEXT};
    }}
    Footer {{
        background: {HAL_BLACK};
        color: {HAL_RED};
    }}
    FooterKey {{
        background: {HAL_BLACK};
        color: {HAL_RED};
        text-style: bold;
    }}
    FooterLabel {{
        background: {HAL_BLACK};
        color: {HAL_TEXT};
    }}
    #table {{
        height: 45%;
        background: {HAL_BLACK};
        color: {HAL_TEXT};
        border-bottom: solid {HAL_RED};
    }}
    #table-filter {{
        display: none;
        background: {HAL_BLACK};
        color: {HAL_TEXT};
        border: solid {HAL_RED};
    }}
    DataTable > .datatable--header {{
        background: {HAL_BLACK};
        color: {HAL_RED};
        text-style: bold;
    }}
    DataTable > .datatable--odd-row {{
        background: {HAL_BLACK};
    }}
    DataTable > .datatable--even-row {{
        background: {HAL_BLACK};
    }}
    DataTable > .datatable--cursor {{
        background: #3a0000;
        color: #ffffff;
    }}
    #lower {{
        height: 1fr;
    }}
    #detail {{
        width: 48;
        height: 1fr;
        border: solid {HAL_RED};
        padding: 1 2;
        background: {HAL_BLACK};
        color: {HAL_TEXT};
    }}
    #transcript {{
        width: 1fr;
        height: 1fr;
        border: solid {HAL_RED};
        padding: 0 1;
        background: {HAL_BLACK};
        color: {HAL_TEXT};
    }}
    #transcript:focus {{
        border: solid #ffffff;
    }}
    """

    def __init__(
        self,
        state_store: StateStore | None = None,
        iterm_lister: iterm_mod.ItermLister | None = None,
        no_splash: bool = False,
        backdrop: list[str] | None = None,
        notifications_path: Path | None = None,
        accounts: list[Account] | None = None,
        voice_mode: str = hal.VOICE_OFF,
        head_jeeves: bool = False,
        review_model: str | None = None,
        reviews_dir: Path | None = None,
        head_jeeves_account: str | None = None,
    ):
        super().__init__()
        # HAL's remarks (hal.py). Off unless asked: a test or a script that
        # mounts the app must not toast about the real sessions.
        self._voice_mode = voice_mode
        self._hal = hal.Memory()
        # Head Jeeves (skills/head-jeeves, reviewer.py): a standing session
        # podbay starts and feeds. Off unless asked, for the same reason: it
        # starts a Claude session and sends it work that spends tokens.
        self._head_jeeves = head_jeeves
        self._review_model = review_model
        self._reviews_dir = reviews_dir
        self._head_jeeves_account_label = head_jeeves_account
        # {"follow_up": <command to send once he is up>, "launched_at": datetime}
        self._head_jeeves_pending: dict | None = None
        # The session podbay primed as Head Jeeves: his /rename takes a few
        # scans to show in the registry, and he is him in the meantime.
        self._head_jeeves_id: str | None = None
        self._reviews_seen: dict[str, float] | None = None  # file -> mtime, None until the first look
        self.state_store = state_store or StateStore()
        self.iterm_lister = iterm_lister or iterm_mod.ItermLister()
        # Every Claude Code config dir on the machine (see accounts.py); the
        # table, the header quotas, open and resume all work per account.
        self._accounts: list[Account] = accounts or discover()
        self.no_splash = no_splash
        self.backdrop = backdrop
        self._quitting = False
        self._force_quit = False
        # label -> monotonic start, for each poll whose thread is running
        self._polls: dict[str, float] = {}
        self.rows: list[dict] = []
        self._row_keys: list[str] = []
        self._transcript_cache: dict[str, tuple[float, int, list[dict]]] = {}
        self._last_transcript_session: str | None = None
        self._scanning = False
        self._last_scan_at: datetime | None = None
        self._usage_scanning = False
        # account label -> per-model weekly entries from `claude -p /usage`
        self._usage_entries: dict[str, list[dict]] = {}
        self._live_sessions: dict[str, Session] = {}
        self._col_keys: list = []
        # account label -> sources.newest_limits() of that account's snapshots
        self._limits: dict[str, dict] = {}
        self._limits_now: datetime = datetime.now()
        self._selected: set[str] = set()
        self._shell_text_cache: dict[str, tuple[float, str]] = {}
        self._shell_read_timer = None
        # / filters the table (see _apply_filter); self.rows/_row_keys always
        # stay the complete set (snapshot, arrange, window numbers read those).
        self._filtering = False
        self._filter_query = ""
        self._conv_hit_ids: set[str] = set()
        self._filter_token = 0
        self._filter_timer = None
        self._visible_rows: list[dict] = []
        # Terminal that `o` just started Claude in: its shell row is about to
        # be replaced by a Claude row with a new session id, and the cursor
        # should follow the terminal across that change (see _redraw_table).
        self._follow_tty: str | None = None
        self._follow_until: float = 0.0
        self._visible_row_keys: list[str] = []
        # Notification history for `h`. Only main() passes a file path, so a
        # test never appends to the real history; without one it stays in memory.
        self._notifications_path = notifications_path
        self._notification_log: list[tuple[datetime, str, str]] = []
        try:
            self._own_tty: str | None = os.ttyname(0)
        except OSError:
            self._own_tty = None

    def _handle_exception(self, error: Exception) -> None:
        # Textual prints the traceback only after it has restored the
        # terminal, and the next podbay run in the same tab scrolls it away.
        log.critical("unhandled exception, the TUI is exiting", exc_info=error)
        super()._handle_exception(error)

    def compose(self) -> ComposeResult:
        yield PodbayHeader(id="header")
        yield DataTable(id="table", cursor_type="row")
        yield Input(placeholder="filter by title/directory, or search conversations...", id="table-filter")
        with Horizontal(id="lower"):
            yield Static(id="detail")
            with VerticalScroll(id="transcript"):
                yield Static(id="transcript-body")
        yield Footer()

    def on_mount(self) -> None:
        self.console.push_theme(HAL_MARKDOWN_THEME)
        table = self.query_one("#table", DataTable)
        self._col_keys = table.add_columns(
            " ", "State", "Age", "CTX", "Model", "Acct", "RC", "⚡", "Dir", "Repos", "Wait", "Title", "Recap", "Parked", "#"
        )
        for key, width in zip(self._col_keys, COLUMN_WIDTHS):
            if width:
                self._set_column_width(key, width)
        self.call_after_refresh(self._fit_recap_column)
        self.sub_title = ""
        self._update_header()
        # Kick off the first background scan before the splash even renders,
        # so the table is already populated by the time it's dismissed.
        self.trigger_refresh()
        self.set_interval(TABLE_REFRESH_SECONDS, self.trigger_refresh)
        self._refresh_usage()
        self.set_interval(USAGE_REFRESH_SECONDS, self._refresh_usage)
        if not self.no_splash:
            # The main screen starts fully dark and fades in once the splash
            # has dissolved to black, so splash -> app is one continuous fade.
            main = self.screen
            main.styles.opacity = 0.0
            self.push_screen(
                SplashScreen(backdrop=self.backdrop),
                lambda _result: main.styles.animate("opacity", 1.0, duration=FADE_IN_SECONDS),
            )

    def action_quit(self) -> None:
        """Quit through the eye: the main screen fades to black, HAL's
        shutdown line plays over the eye, then the app exits. A second key
        during the sequence exits at once; --no-splash exits at once."""
        if self.no_splash or self._quitting:
            self._quitting = True
            self._exit_when_idle()
            return
        self._quitting = True
        main = self.screen
        main.styles.animate(
            "opacity",
            0.0,
            duration=FADE_OUT_SECONDS,
            on_complete=lambda: self.push_screen(ShutdownScreen(on_complete=self._exit_when_idle)),
        )

    @contextmanager
    def _polling(self, label: str):
        """Mark a poll as running for the quit wait screen."""
        self._polls[label] = time.monotonic()
        try:
            yield
        finally:
            self._polls.pop(label, None)

    def _exit_when_idle(self) -> None:
        """Exit now when no poll runs, else show the wait screen (once)."""
        if not self._polls:
            self.exit()
        elif not isinstance(self.screen, QuitWaitScreen):
            self.push_screen(QuitWaitScreen(lambda: dict(self._polls), self.exit, self._force_exit))

    def _force_exit(self) -> None:
        """Stop podbay's own child processes and leave without waiting on
        the poll threads; main() then skips the interpreter's thread join."""
        self._force_quit = True
        log.info("force quit while polls ran: %s", ", ".join(sorted(self._polls)))
        try:
            subprocess.run(["pkill", "-TERM", "-P", str(os.getpid())], check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            log.warning("could not stop child processes", exc_info=True)
        self.exit()

    def trigger_refresh(self) -> None:
        """Kick off a background session-gather; called by the periodic
        timer and by 'r'. A no-op while one is already in flight -- the
        gather (registry + transcripts + iTerm2) runs in a worker thread so
        it never blocks the UI loop."""
        if self._scanning or self._quitting:
            return
        self._scanning = True
        self._update_header()
        self._gather_worker()

    @work(thread=True, exclusive=True, group="refresh")
    def _gather_worker(self) -> None:
        now = datetime.now()
        with self._polling("session scan"):
            snapshots = sources.read_status_snapshots()
            sessions = sources.gather_sessions(
                self.state_store, self.iterm_lister, status_snapshots=snapshots, accounts=self._accounts
            )
        limits = {a.label: sources.newest_limits(snapshots, a.label) for a in self._accounts}
        self.call_from_thread(self._apply_refresh, sessions, now, limits)

    def _refresh_usage(self) -> None:
        """Kick off a background /usage refresh; a no-op while one is
        already running -- fetch() takes ~5.5s and must never stall a
        keypress."""
        if self._usage_scanning or self._quitting:
            return
        self._usage_scanning = True
        self._usage_worker()

    @work(thread=True, exclusive=True, group="usage")
    def _usage_worker(self) -> None:
        """One `claude -p /usage` per account, each behind its own cache."""
        results: dict[str, dict | None] = {}
        for account in self._accounts:
            path = usage.cache_path_for(account)
            data = usage.read_cache(path, max_age=USAGE_REFRESH_SECONDS)
            if data is None:
                with self._polling("usage"):
                    data = usage.fetch(account=account)
                if data is not None:
                    usage.write_cache(data, path)
            results[account.label] = data
        self.call_from_thread(self._apply_usage, results)

    def _apply_usage(self, results: dict[str, dict | None]) -> None:
        self._usage_scanning = False
        for label, data in results.items():
            if data is None:
                continue  # keep whatever the header already shows for that account
            self._usage_entries[label] = data.get("entries", [])
        self._update_header()

    def _apply_refresh(
        self,
        sessions: list[Session],
        now: datetime,
        limits: dict[str, dict] | None = None,
    ) -> None:
        """Runs on the UI thread (via call_from_thread): apply the result of
        a background gather to the table and header. Must stay fast."""
        if self._own_tty is not None:
            sessions = [s for s in sessions if getattr(s, "tty", None) != self._own_tty]
        # Tabs beyond a window's first are that session's helper terminals
        # (a login, a log tail), not sessions of their own.
        sessions = [s for s in sessions if getattr(s, "tab_index", None) in (None, 1)]

        self.state_store.prune({s.session_id for s in sessions}, now)
        self._live_sessions = {s.session_id: s for s in sessions if not getattr(s, "is_shell", False)}
        self.rows = build_rows(sessions, now, self._selected)
        self._check_head_jeeves_pending(sessions, now)
        self._row_keys = [r["session_id"] for r in self.rows]
        self._selected &= set(self._row_keys)  # drop selections for rows that vanished
        self._write_snapshot(now)

        self._limits = limits or {}
        self._limits_now = now
        self._hal_remarks(sessions, now)
        self._ensure_head_jeeves()
        self._watch_reviews()

        try:
            self._fit_recap_column()
            # a live filter query stays applied across a refresh -- this
            # recomputes self._visible_rows from the new self.rows and redraws
            # the table, restoring the cursor by session id (see _redraw_table).
            self._apply_filter()
        except NoMatches:
            return  # the screen is already gone: a scan landing during shutdown

        self._scanning = False
        self._last_scan_at = now
        self._update_header()

    def _hal_remarks(self, sessions: list[Session], now: datetime) -> None:
        if self._voice_mode == hal.VOICE_OFF:
            return
        try:
            lines = hal.remarks(self._hal, sessions, self._limits, now)
        except Exception:  # noqa: BLE001 -- a remark must never take the TUI down
            log.warning("HAL remarks failed", exc_info=True)
            return
        for line in lines:
            self.notify(line, title=voice.SHIP_NAME, timeout=12)
        if self._head_jeeves:
            for session_id in self._hal.newly_heated:
                session = self._live_sessions.get(session_id)
                if session is not None:
                    self._send_to_head_jeeves(f"/head-jeeves checkup {session.name} {session.session_id}")
            # what HAL just told the screen, Head Jeeves tells the phone
            for name, line in self._hal.events:
                self._send_to_head_jeeves(f"/head-jeeves event {name}: {line}")

    # ---- Head Jeeves ---------------------------------------------------------

    def _head_jeeves_session(self) -> Session | None:
        """The live Head Jeeves session, searched across the full row set so
        an active filter never hides him."""
        for r in self.rows:
            if is_head_jeeves(r["session"]) or r["session"].session_id == self._head_jeeves_id:
                return r["session"]
        return None

    def _send_to_head_jeeves(self, command: str) -> None:
        head = self._head_jeeves_session()
        if head is None:
            self._start_head_jeeves(command)
            return
        if not self._send_to_session(head, command):
            self.notify(voice.no_tab(), severity="warning")
            return
        self.notify(voice.head_jeeves_sent(command))

    def _start_head_jeeves(self, follow_up: str) -> None:
        """No Head Jeeves session exists: launch one from the home repo (the
        same free-pane-or-new-window path as resume) and queue `follow_up`
        for _check_head_jeeves_pending to deliver once he is up idle. A
        launch already in flight just gets its queued command replaced."""
        if self._head_jeeves_pending is not None:
            self._head_jeeves_pending["follow_up"] = follow_up
            return
        account = self._head_jeeves_account()
        args = f"-n {HEAD_JEEVES_NAME}" + (f" --model {shlex.quote(self._review_model)}" if self._review_model else "")
        command = self._claude_command(account, self._open_default_dir(None), args)
        launched_at = datetime.now()
        ok, _used_pane = self._dispatch_command(self._free_shell_sessions(), command)
        if not ok:
            self.notify(voice.no_tab(), severity="warning")
            return
        self._head_jeeves_pending = {"follow_up": follow_up, "launched_at": launched_at}
        self.notify(voice.head_jeeves_starting(), title=voice.SHIP_NAME)

    def _head_jeeves_account(self) -> Account:
        return by_label(self._accounts, self._head_jeeves_account_label) or self._accounts[0]

    def _check_head_jeeves_pending(self, sessions: list[Session], now: datetime) -> None:
        """While a launch is pending: look for a session that appeared after
        it, idle, in the home repo; name it if `-n` did not stick; prime it
        with a bare /head-jeeves, then deliver the queued command."""
        pending = self._head_jeeves_pending
        if pending is None:
            return
        if now - pending["launched_at"] > HEAD_JEEVES_LAUNCH_TIMEOUT:
            self._head_jeeves_pending = None
            self.notify(voice.head_jeeves_late(), severity="warning")
            return
        home = self._open_default_dir(None)
        label = self._head_jeeves_account().label
        candidate = next(
            (
                s for s in sessions
                if not s.is_shell and s.account == label and s.started_at > pending["launched_at"]
                and s.cwd == home and s.status == "idle"
            ),
            None,
        )
        if candidate is None:
            return
        self._head_jeeves_pending = None
        self._head_jeeves_id = candidate.session_id
        if not is_head_jeeves(candidate):
            self._send_to_session(candidate, f"/rename {HEAD_JEEVES_NAME}")
        self._send_to_session(candidate, "/head-jeeves")
        if pending["follow_up"] == "/head-jeeves":
            return  # nothing queued beyond reporting for duty
        if not self._send_to_session(candidate, pending["follow_up"]):
            self.notify(voice.no_tab(), severity="warning")
            return
        self.notify(voice.head_jeeves_sent(pending["follow_up"]))

    def _ensure_head_jeeves(self) -> None:
        """Start Head Jeeves when he is wanted and not up, with nothing
        queued but his own report for duty; events find him there. A launch
        already pending keeps whatever it has queued."""
        if not self._head_jeeves or not self._live_sessions or self._head_jeeves_pending is not None:
            return
        if self._head_jeeves_session() is None:
            self._start_head_jeeves("/head-jeeves")

    def _watch_reviews(self) -> None:
        """Toast each review file Head Jeeves wrote since the last look. The
        first look only sets the baseline."""
        current = reviewer.newest_files(self._reviews_dir)
        previous = self._reviews_seen
        self._reviews_seen = current
        if previous is None:
            return
        by_id = {s.session_id: s for s in self._live_sessions.values()}
        for name, mtime in sorted(current.items(), key=lambda item: item[1]):
            if previous.get(name) == mtime:
                continue
            session_id = name.rsplit("-", 1)[0] if name != "watch.md" else ""
            session = by_id.get(session_id)
            title = session.title if session is not None else session_id[:8]
            self.notify(voice.review_ready(name, title), title=voice.SHIP_NAME, timeout=15)

    def action_exit_interview(self) -> None:
        session = self._selected_session()
        if session is None or not self._require_claude_session(session, "Exit interview"):
            return
        if is_head_jeeves(session):
            return
        self._send_to_head_jeeves(f"/head-jeeves exit {session.name} {session.session_id}")

    def action_view_review(self) -> None:
        session = self._selected_session()
        if session is None or not self._require_claude_session(session, "Review"):
            return
        path = reviewer.latest(session.session_id, self._reviews_dir)
        if path is None:
            self.notify(voice.review_none(session.title), severity="warning")
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            self.notify(voice.review_failed(session.title), severity="error")
            return
        self.push_screen(ReviewScreen(text, path))

    def _write_snapshot(self, now: datetime) -> None:
        """Persist the live Claude sessions (not shells -- nothing to resume)
        as the working set to offer back via Resume after a reboot. Reads
        the iTerm2 window cache directly rather than iterm_lister.windows()
        so this never forces a fresh AppleScript call from the UI thread."""
        windows_cache = getattr(self.iterm_lister, "_windows", None) or {}
        entries = []
        for r in self.rows:
            session = r["session"]
            if getattr(session, "is_shell", False):
                continue
            window_id = getattr(session, "window_id", None)
            win = windows_cache.get(window_id) if window_id else None
            entries.append(
                {
                    "session_id": session.session_id,
                    "cwd": session.cwd,
                    "title": session.title,
                    "account": session.account,
                    "window_id": window_id,
                    "bounds": list(win.bounds) if win is not None else None,
                    "seen_at": now.isoformat(),
                }
            )
        if not entries:
            return  # right after a reboot nothing is live yet: keep the pre-reboot set to restore from
        self.state_store.set_snapshot(entries, now)

    def _apply_filter(self) -> None:
        """Recompute self._visible_rows from self.rows plus the live query
        and whatever conversation hits have arrived so far, then redraw.
        self.rows/_row_keys are never touched here -- they stay the complete
        set for the snapshot, arrange and window numbers."""
        query = self._filter_query.strip()
        if not query:
            self._visible_rows = list(self.rows)
            self._redraw_table()
            return

        # Same fuzzy subsequence scoring the Resume modal uses over "title +
        # directory" -- reuse _filter_rows itself rather than a second scorer.
        candidates = [
            {
                "session_id": r["session_id"],
                "title": r["session"].title,
                "cwd": r["session"].cwd,
                "sort_ts": r["session"].status_updated_at,
            }
            for r in self.rows
        ]
        fuzzy = _filter_rows(candidates, query)
        by_id = {r["session_id"]: r for r in self.rows}
        visible = [by_id[c["session_id"]] for c in fuzzy]
        visible_ids = {c["session_id"] for c in fuzzy}

        # Conversation-only hits (matched by history.search_sessions in the
        # background, not by title/directory) are appended, newest first.
        extra_ids = [sid for sid in self._conv_hit_ids if sid in by_id and sid not in visible_ids]
        extra_ids.sort(key=lambda sid: by_id[sid]["session"].status_updated_at, reverse=True)
        visible.extend(by_id[sid] for sid in extra_ids)

        self._visible_rows = visible
        self._redraw_table()

    def _mark_conv_hit(self, row: dict) -> Text:
        """The only visible sign a row is here because the query matched
        inside its transcript, not its title/directory: a dim prefix on the
        Recap cell (which already has room, unlike the fixed-width Title)."""
        marker = Text("[chat] ", style="dim italic")
        original = row["recap"]
        return marker + (original if isinstance(original, Text) else Text(str(original) if original else ""))

    # Claude registers itself within a few seconds of starting; past this the
    # start most likely failed and the cursor is released.
    FOLLOW_TTY_SECONDS = 60

    def _follow(self, tty: str | None) -> None:
        self._follow_tty = tty
        self._follow_until = time.time() + self.FOLLOW_TTY_SECONDS

    def _cursor_row_after_redraw(self, previous_key: str | None, previous_index: int | None) -> int | None:
        """Where the cursor goes once the rows have been rebuilt: the row the
        followed terminal now shows (a shell row turning into a Claude row
        keeps the highlight), else the same session id, else the same index
        (clamped) so a vanished row never throws the cursor back to the top."""
        if self._follow_tty and time.time() > self._follow_until:
            self._follow_tty = None  # Claude never showed up there: stop pinning the cursor to that shell
        if self._follow_tty:
            for i, row in enumerate(self._visible_rows):
                session = row["session"]
                if getattr(session, "tty", None) == self._follow_tty:
                    if not getattr(session, "is_shell", False):
                        self._follow_tty = None  # Claude is up in that terminal: the follow is done
                    return i
        if previous_key and previous_key in self._visible_row_keys:
            return self._visible_row_keys.index(previous_key)
        if previous_index is not None and self._visible_row_keys:
            return min(previous_index, len(self._visible_row_keys) - 1)
        return None

    def _redraw_table(self) -> None:
        """Redraw #table from self._visible_rows, restoring the cursor by
        session id when the previously highlighted row is still listed (see
        _cursor_row_after_redraw for the fallbacks)."""
        table = self.query_one("#table", DataTable)
        previous_key = None
        previous_index = table.cursor_row
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self._visible_row_keys):
            previous_key = self._visible_row_keys[table.cursor_row]

        table.clear()
        if self._filter_query.strip() and not self._visible_rows:
            self._visible_row_keys = []
            table.add_row(
                *[""] * TITLE_COLUMN, _cell("no match", "dim italic"), *[""] * (len(COLUMN_WIDTHS) - TITLE_COLUMN - 1),
                key="__no_match__",
            )
            self._update_detail()
            self._refresh_transcript()
            return

        self._visible_row_keys = [r["session_id"] for r in self._visible_rows]
        for r in self._visible_rows:
            recap = self._mark_conv_hit(r) if r["session_id"] in self._conv_hit_ids else r["recap"]
            table.add_row(
                r["new"], r["state"], r["age"], r["ctx"], r["model"], r["account"], r["remote"], r["mood"], r["dir"], r["repos"], r["wait"],
                r["title"], recap, r["parked"], r["win"],
                key=r["session_id"],
            )

        target = self._cursor_row_after_redraw(previous_key, previous_index)
        if target is not None:
            table.move_cursor(row=target)
        self._update_detail()
        self._refresh_transcript()

    def _set_column_width(self, key, width: int) -> None:
        """auto_width has to go off too: with it on, a long cell widens the
        column past the fixed width and pushes the last columns off screen."""
        column = self.query_one("#table", DataTable).columns[key]
        column.auto_width = False
        column.width = width
        column.content_width = width

    def _fit_recap_column(self) -> None:
        table = self.query_one("#table", DataTable)
        if not self._col_keys:
            return
        fixed = sum(w for w in COLUMN_WIDTHS if w)
        padding = 2 * len(COLUMN_WIDTHS)
        available = max(RECAP_MIN_WIDTH, table.size.width - fixed - padding)
        self._set_column_width(self._col_keys[RECAP_COLUMN], available)
        table.refresh()

    def on_resize(self, event) -> None:
        self._fit_recap_column()



    def _quota_segments(self) -> list:
        """One quota group per account, the account's label in front of it
        when there is more than one account; an account nothing is known
        about yet shows no group."""
        segments: list = []
        for account in self._accounts:
            limits = self._limits.get(account.label) or {}
            group = voice.limits_segments(
                limits.get("five_pct"),
                limits.get("five_resets_at"),
                limits.get("week_pct"),
                limits.get("week_resets_at"),
                self._limits_now,
                self._usage_entries.get(account.label),
            )
            if not group:
                continue
            if segments:
                segments.append((ACCOUNT_SEPARATOR, None))
            if len(self._accounts) > 1:
                segments.append((f"{account.label} ", ACCOUNT_TONE))
            segments.extend(group)
        return segments

    def _update_header(self) -> None:
        quota_segments = self._quota_segments()
        ship_segments = voice.ship_segments()
        scan_text = voice.scan_status(self._scanning, self._last_scan_at)
        # app.title stays the one-string version of the whole bar, which the
        # split widget no longer renders as one run
        title_parts = ["".join(content for content, _ in ship_segments)]
        if quota_segments:
            title_parts.append("".join(content for content, _ in quota_segments))
        self.title = "  ·  ".join(title_parts)
        self.sub_title = scan_text
        header = self.query_one(PodbayHeader)
        header.update_ship(_header_text(ship_segments))
        header.update_quotas(_header_text(quota_segments))
        header.update_clock(scan_text)

    def _selected_session(self) -> Session | None:
        """The highlighted row's session, resolved through the currently
        *visible* (possibly filtered) rows -- table.cursor_row indexes what
        is actually listed, not the full self.rows."""
        table = self.query_one("#table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        if table.cursor_row >= len(self._visible_rows):
            return None
        return self._visible_rows[table.cursor_row]["session"]

    def _update_detail(self) -> None:
        try:
            detail = self.query_one("#detail", Static)
        except NoMatches:
            return  # the screen is already gone
        session = self._selected_session()
        if session is None:
            detail.update("(no sessions)")
            return
        detail.update(_detail_text(session, datetime.now()))

    def _refresh_transcript(self, force: bool = False) -> None:
        """Redraw the transcript pane for the highlighted session, re-parsing
        its transcript only when the file's mtime/size changed since the
        last read (or the selection moved to a different session)."""
        try:
            body = self.query_one("#transcript-body", Static)
        except NoMatches:
            return  # the screen is already gone
        session = self._selected_session()
        if session is None:
            self._last_transcript_session = None
            body.update("(no sessions)")
            return

        if getattr(session, "is_shell", False):
            self._refresh_shell_pane(session, body)
            return

        account = by_label(self._accounts, session.account) or self._accounts[0]
        path = sources.transcript_path_for(session.cwd, session.session_id, account.projects_dir)
        if path is None:
            self._last_transcript_session = session.session_id
            body.update("(no transcript)")
            return

        try:
            stat = path.stat()
        except OSError:
            self._last_transcript_session = session.session_id
            body.update("(no transcript)")
            return

        changed_session = session.session_id != self._last_transcript_session
        cached = self._transcript_cache.get(session.session_id)
        unchanged_file = cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size

        if not force and not changed_session and unchanged_file:
            return  # nothing new; leave the pane (and any manual scroll) alone

        if unchanged_file:
            entries = cached[2]
        else:
            entries = sources.read_conversation(path, TRANSCRIPT_LIMIT)
            self._transcript_cache[session.session_id] = (stat.st_mtime, stat.st_size, entries)

        self._last_transcript_session = session.session_id

        scroller = self.query_one("#transcript", VerticalScroll)
        if self.focused is scroller and session.unread:
            self._mark_seen(session)  # reading the pane as the answer lands
        at_bottom = scroller.scroll_y >= scroller.max_scroll_y - 1
        body.update(_render_transcript(entries))
        if changed_session or at_bottom:
            scroller.scroll_end(animate=False)

    def _refresh_shell_pane(self, session: Session, body: Static) -> None:
        """No transcript file for a shell row -- show its on-screen text as
        plain text instead. Reading a terminal costs ~250ms of blocking
        AppleScript, so a cached copy is drawn at once and the read itself
        happens on a thread, only once the cursor stops on this row."""
        changed_session = session.session_id != self._last_transcript_session
        self._last_transcript_session = session.session_id
        tty = session.tty

        cached = self._shell_text_cache.get(tty) if tty else None
        fresh = cached is not None and (time.monotonic() - cached[0]) < SHELL_TEXT_TTL
        self._draw_shell_text(body, cached[1] if cached else None, changed_session)

        if self._shell_read_timer is not None:
            self._shell_read_timer.stop()
            self._shell_read_timer = None
        if tty and not fresh:
            self._shell_read_timer = self.set_timer(
                SHELL_READ_DEBOUNCE, lambda: self._read_shell_text(tty, session.session_id)
            )

    def _draw_shell_text(self, body: Static, text: str | None, changed_session: bool) -> None:
        scroller = self.query_one("#transcript", VerticalScroll)
        at_bottom = scroller.scroll_y >= scroller.max_scroll_y - 1
        body.update(Text(text) if text else Text("(reading...)" if text is None else "(no output)"))
        if changed_session or at_bottom:
            scroller.scroll_end(animate=False)

    @work(thread=True, exclusive=True, group="shell-read")
    def _read_shell_text(self, tty: str, session_id: str) -> None:
        text = iterm_mod.read_session_text(tty) or ""
        self.call_from_thread(self._apply_shell_text, tty, session_id, text)

    def _apply_shell_text(self, tty: str, session_id: str, text: str) -> None:
        self._shell_text_cache[tty] = (time.monotonic(), text)
        if session_id != self._last_transcript_session:
            return  # the cursor moved on while we were reading
        self._draw_shell_text(self.query_one("#transcript-body", Static), text, False)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail()
        self._refresh_transcript()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_focus_selected()

    def _mark_seen(self, session: Session) -> None:
        """You are looking at this session now: clear its unread marker in
        the table at once, and persist so the next scan agrees."""
        now = datetime.now()
        session.seen_at = now
        self.state_store.set_seen(session.session_id, now)
        table = self.query_one("#table", DataTable)
        try:
            table.update_cell(session.session_id, self._col_keys[0], "")
        except Exception:
            pass  # row vanished between selection and click; the next scan redraws

    def action_focus_selected(self) -> None:
        # The only place podbay switches to iTerm2, and only on an explicit
        # Enter. Nothing else (open, resume, refresh) may call focus_tty.
        session = self._selected_session()
        if session is None:
            return
        self._mark_seen(session)
        tty = iterm_mod.get_tty_for_pid(session.pid)
        if not tty or not iterm_mod.focus_tty(tty):
            self.notify(voice.no_tab(), severity="warning")

    def _require_claude_session(self, session: Session | None, action_label: str) -> bool:
        """park/unpark/note store per-Claude-session state; a plain-shell
        row has none. False means the caller already notified."""
        if session is not None and getattr(session, "is_shell", False):
            self.notify(f"{action_label}: no Claude session on this row", severity="warning")
            return False
        return True

    def action_toggle_transcript(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        if self.focused is transcript:
            self.query_one("#table", DataTable).focus()
        else:
            transcript.focus()

    def on_descendant_focus(self, event) -> None:
        """Any way into the chat-log pane (t key, click, tab) counts as
        looking at the highlighted session."""
        transcript = self.query_one("#transcript", VerticalScroll)
        if event.widget is transcript or transcript in event.widget.ancestors:
            session = self._selected_session()
            if session is not None and session.unread:
                self._mark_seen(session)

    def action_focus_table(self) -> None:
        self.query_one("#table", DataTable).focus()

    def action_escape_pressed(self) -> None:
        if self._filtering:
            self._clear_filter()
            return
        self.action_focus_table()

    def action_filter(self) -> None:
        self._filtering = True
        input_widget = self.query_one("#table-filter", Input)
        input_widget.display = True
        input_widget.focus()

    def _clear_filter(self) -> None:
        self._filtering = False
        self._filter_query = ""
        self._conv_hit_ids = set()
        self._filter_token += 1
        if self._filter_timer is not None:
            self._filter_timer.stop()
            self._filter_timer = None
        input_widget = self.query_one("#table-filter", Input)
        input_widget.value = ""
        input_widget.display = False
        self._apply_filter()
        self.action_focus_table()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "table-filter":
            return
        event.stop()
        self._filter_query = event.value
        self._filter_token += 1
        if len(self._filter_query.strip()) < RESUME_SEARCH_MIN_LEN:
            self._conv_hit_ids = set()
        self._apply_filter()

        if self._filter_timer is not None:
            self._filter_timer.stop()
            self._filter_timer = None
        query = self._filter_query.strip()
        if len(query) >= RESUME_SEARCH_MIN_LEN:
            token = self._filter_token
            # shells have no transcript to search; only real Claude sessions
            # are worth asking history.search_sessions to confirm.
            live_ids = {r["session_id"] for r in self.rows if not getattr(r["session"], "is_shell", False)}
            self._filter_timer = self.set_timer(
                RESUME_SEARCH_DEBOUNCE, lambda: self._start_filter_search(query, token, live_ids)
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "table-filter":
            return
        event.stop()
        self.action_focus_selected()

    def _start_filter_search(self, query: str, token: int, live_ids: set[str]) -> None:
        if token != self._filter_token:
            return  # the query moved on while we were waiting out the debounce
        self._filter_search_worker(query, token, live_ids)

    @work(thread=True, exclusive=True, group="table-filter-search")
    def _filter_search_worker(self, query: str, token: int, live_ids: set[str]) -> None:
        search_sessions = getattr(history_mod, "search_sessions", None) if history_mod else None
        matches: list = []
        if search_sessions is not None:
            try:
                matches = search_sessions(query, limit=100, include_ids=live_ids)
            except Exception:
                log.warning("filter search failed for %r", query, exc_info=True)
                matches = []
        self.call_from_thread(self._apply_filter_search_results, token, matches)

    def _apply_filter_search_results(self, token: int, matches: list) -> None:
        if token != self._filter_token:
            return  # a newer query has since started typing
        self._conv_hit_ids = {m.session.session_id for m in matches}
        self._apply_filter()

    def action_park(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        if not self._require_claude_session(session, "Park"):
            return

        def handle_result(value: str | None) -> None:
            if not value:
                return
            try:
                when = parse_when(value)
            except ParseError:
                self.notify(voice.parse_error(), severity="error")
                return
            self.state_store.set_parked(session.session_id, when)
            self.notify(voice.park_ok(when))
            self.trigger_refresh()

        self.push_screen(
            PromptScreen("Park until (+2h, today 14, tomorrow 9, fri 14, 2026-09-12 09:00, ...):", glyph=glyphs.PARK), handle_result
        )

    def action_unpark(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        if not self._require_claude_session(session, "Unpark"):
            return
        self.state_store.set_parked(session.session_id, None)
        self.trigger_refresh()

    def action_note(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        if not self._require_claude_session(session, "Note"):
            return

        def handle_result(value: str | None) -> None:
            if value is None:
                return
            self.state_store.set_note(session.session_id, value or None)
            self.trigger_refresh()

        self.push_screen(PromptScreen("Note:", initial=session.note or ""), handle_result)

    def _send_to_session(self, session: Session, text: str) -> bool:
        """Resolve the target tty exactly as action_message does (a shell
        row already carries its own tty from the iTerm2 join; a Claude row's
        tty comes from its pid) and write text into it via iTerm2."""
        is_shell = getattr(session, "is_shell", False)
        tty = session.tty if is_shell else iterm_mod.get_tty_for_pid(session.pid)
        return bool(tty) and iterm_mod.send_text(tty, text)

    def action_message(self) -> None:
        """A Claude row goes through get_tty_for_pid (session.pid); a plain
        shell already carries its own tty from the iTerm2 join."""
        session = self._selected_session()
        if session is None:
            return
        is_shell = getattr(session, "is_shell", False)

        def handle_result(value: str | None) -> None:
            if not value:
                return
            if not self._send_to_session(session, value):
                self.notify(voice.no_tab(), severity="warning")
                return
            self.notify(voice.message_sent(session.title))

        target = f"shell {session.title} ({session.cwd or session.tty})" if is_shell else session.title
        self.push_screen(PromptScreen(f"Message to {target}:", glyph=glyphs.MESSAGE), handle_result)

    def notify(self, message, *args, **kwargs) -> None:
        self._record_notification(kwargs.get("severity") or "information", str(message))
        super().notify(message, *args, **kwargs)

    def _record_notification(self, kind: str, text: str) -> None:
        now = datetime.now()
        self._notification_log.append((now, kind, text))
        if self._notifications_path is not None:
            notifications.record(self._notifications_path, now, kind, text)

    def _notification_history(self, now: datetime) -> list[tuple[datetime, str, str]]:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._notifications_path is not None:
            return notifications.read_since(self._notifications_path, midnight)
        return sorted((e for e in self._notification_log if e[0] >= midnight), key=lambda e: e[0], reverse=True)

    def action_history(self) -> None:
        self.push_screen(HistoryScreen(self._notification_history(datetime.now())))

    def _focus_session(self, session: Session) -> None:
        """Switch to the session's iTerm2 tab from a list screen (g, j)."""
        self._mark_seen(session)
        tty = iterm_mod.get_tty_for_pid(session.pid)
        if not tty or not iterm_mod.focus_tty(tty):
            self.notify(voice.no_tab(), severity="warning")

    def action_remote(self) -> None:
        """Toggle Remote Control for the highlighted session by typing
        /remote-control into its tab (the command itself toggles). On, the
        session shows up in the Claude app and on claude.ai/code; the
        registry reports the bridge within a few seconds and the RC column
        follows."""
        session = self._selected_session()
        if session is None:
            return
        if not self._require_claude_session(session, "Remote"):
            return
        if not self._send_to_session(session, REMOTE_CONTROL_COMMAND):
            self.notify(voice.no_tab(), severity="warning")
            return
        self.notify(voice.remote_toggled(session.title, turning_on=not session.remote_session_id))
        self.trigger_refresh()

    def action_refresh(self) -> None:
        self.trigger_refresh()

    def action_toggle_selection(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        key = session.session_id
        if key in self._selected:
            self._selected.discard(key)
        else:
            self._selected.add(key)
        self._redraw_state_cell(key)

    def action_clear_selection(self) -> None:
        if not self._selected:
            return
        cleared = list(self._selected)
        self._selected.clear()
        for key in cleared:
            self._redraw_state_cell(key)

    def _redraw_state_cell(self, key: str) -> None:
        row = next((r for r in self.rows if r["session_id"] == key), None)
        if row is None:
            return
        session = row["session"]
        derived = session.derive_status(datetime.now())
        cell = _state_cell(derived, key in self._selected, ROW_STYLES.get(derived))
        row["state"] = cell
        try:
            self.query_one("#table", DataTable).update_cell(key, self._col_keys[1], cell)
        except Exception:
            pass  # row vanished between highlight and toggle; next scan redraws

    def action_arrange(self) -> None:
        """Build a plan from the selected rows in table order (podbay's own
        row and any row without a window id are dropped) and apply it."""
        sessions_by_id = {r["session_id"]: r["session"] for r in self.rows}
        pane_ids = []
        for key in self._row_keys:
            if key not in self._selected:
                continue
            session = sessions_by_id.get(key)
            if session is None:
                continue
            if self._own_tty is not None and getattr(session, "tty", None) == self._own_tty:
                continue
            window_id = getattr(session, "window_id", None)
            if window_id is None or window_id in pane_ids:
                continue  # two selected tabs of one window are one window to move
            pane_ids.append(window_id)

        if not pane_ids:
            self.notify("nothing to arrange (select terminals first)", severity="warning")
            return

        display = screens.arrange_display()
        frame = layout.Screen(*display.bounds) if display is not None else EXTERNAL_SCREEN_FRAME
        where = "external display" if display is not None and not display.is_main else "this screen"
        plan = layout.plan_layout(pane_ids, frame)
        self._apply_plan(plan, where)

    def _apply_plan(self, plan: list[layout.Placement], where: str = "this screen") -> None:
        """The single place that touches the OS for an arrange. macOS has no
        API for moving a window to another desktop, so this places windows on
        whatever desktop they are already on."""
        applied = 0
        for placement in plan:
            try:
                ok = iterm_mod.set_window_bounds(placement.pane_id, placement.bounds)
            except Exception:
                log.warning("set_window_bounds failed for window %s", placement.pane_id, exc_info=True)
                ok = False
            if ok:
                applied += 1
        self.notify(f"arranged {applied}/{len(plan)} window(s) on the {where}")

    def _free_shell_sessions(self) -> list[Session]:
        """Listed terminals sitting at an empty shell prompt, in table order,
        the highlighted one first. A new window is a last resort: the point is
        to fill the terminals that are already open."""
        busy = sources.busy_ttys()
        free = [
            row["session"]
            for row in self.rows
            if getattr(row["session"], "is_shell", False)
            and getattr(row["session"], "tty", None)
            and row["session"].tty not in busy
            and iterm_mod.at_empty_prompt(row["session"].tty)
        ]
        highlighted = self._selected_session()
        if highlighted is not None and highlighted in free:
            free.remove(highlighted)
            free.insert(0, highlighted)
        return free

    def _free_shell_session(self) -> Session | None:
        free = self._free_shell_sessions()
        return free[0] if free else None

    def _dispatch_command(self, pool: list[Session], command: str) -> tuple[bool, bool]:
        """Run `command` in the next free shell pane in `pool` (popped, so
        repeated calls advance through it), or open a new iTerm2 window when
        the pool is empty. Returns (success, used_pane); a pane target is
        never retried as a new window on failure."""
        if pool:
            target = pool.pop(0)
            return iterm_mod.send_text(target.tty, command), True
        return bool(iterm_mod.open_window(command=command)), False

    def _live_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for session in self._live_sessions.values():
            counts[session.account] = counts.get(session.account, 0) + 1
        return counts

    def _claude_command(self, account: Account, directory: str, args: str = "") -> str:
        prefix = account.command_prefix()
        tail = f" {args}" if args else ""
        return f"cd {shlex.quote(os.path.expanduser(directory))} && {prefix}claude{tail}"

    def action_open_claude(self) -> None:
        """Starts Claude in the highlighted terminal when that one is a plain
        shell, otherwise in the next free terminal, and only opens a new
        window when every terminal is busy. With more than one account the
        pick list comes first, pre-selected to the highlighted session's
        account (the default one on a shell row); then the directory prompt."""
        session = self._selected_session()
        is_shell = session is not None and getattr(session, "is_shell", False)
        default_dir = self._open_default_dir(session)
        account = self._accounts[0] if session is None or is_shell else (by_label(self._accounts, session.account) or self._accounts[0])
        free = session if is_shell else self._free_shell_session()

        def handle_account(chosen: Account | None) -> None:
            if chosen is not None:
                self._prompt_open_directory(chosen, default_dir, free)

        if len(self._accounts) > 1:
            self.push_screen(AccountScreen(self._accounts, account, self._live_counts()), handle_account)
        else:
            self._prompt_open_directory(account, default_dir, free)

    @staticmethod
    def _open_default_dir(session: Session | None) -> str:
        """Where a new claude starts: the home-base repo (`podbay config
        home-repo`) when there is one and it exists on disk, since every
        session is launched from there; else the highlighted session's
        directory; else home."""
        if HOME_BASE:
            home_base = sources.REPOS_DIR / HOME_BASE
            if home_base.is_dir():
                return str(home_base)
        if session is not None and session.cwd:
            return session.cwd
        return os.path.expanduser("~")

    def _prompt_open_directory(self, chosen: Account, default_dir: str, free: Session | None) -> None:
        def handle_result(value: str | None) -> None:
            directory = (value or "").strip()
            if not directory:
                return
            command = self._claude_command(chosen, directory)
            if free is not None and free.tty:
                if not iterm_mod.send_text(free.tty, command):
                    self.notify(voice.no_tab(), severity="warning")
                    return
                self.notify(f"claude started in {free.title} ({directory})")
                self._follow(free.tty)
                self.trigger_refresh()
                return

            open_window = getattr(iterm_mod, "open_window", None)
            write_text = getattr(iterm_mod, "write_text_to_window", None)
            if open_window is None or write_text is None:
                self.notify("open claude: not available yet", severity="warning")
                return
            window_id = open_window()
            if not window_id:
                self.notify("could not open a new window", severity="warning")
                return
            if not write_text(window_id, command):
                self.notify("opened a window but could not start claude", severity="warning")
                return
            self.notify(f"claude started in a new window ({directory})")
            self.trigger_refresh()

        target = f"in {free.title}" if free is not None else "in a new window"
        who = f" as {chosen.label}" if len(self._accounts) > 1 else ""
        self.push_screen(PromptScreen(f"Start claude {target}{who}, directory:", initial=default_dir), handle_result)

    def action_resume(self) -> None:
        """Offer sessions to resume: ones from the last snapshot that are no
        longer live, then older history. Opening one starts a new window
        running `claude --resume <id>` in its original directory."""
        now = datetime.now()
        live_ids = {r["session_id"] for r in self.rows}
        snapshot = self.state_store.get_snapshot()
        before_restart = [e for e in snapshot if e.get("session_id") not in live_ids]
        before_rows = _snapshot_resume_rows(before_restart, now)

        list_past_sessions = getattr(history_mod, "list_past_sessions", None) if history_mod else None
        older_rows: list[dict] = []
        if list_past_sessions is not None:
            exclude_ids = live_ids | {e.get("session_id") for e in snapshot}
            try:
                past = list_past_sessions(exclude_ids=exclude_ids, limit=100, since_days=30, accounts=self._accounts)
            except Exception:
                log.warning("list_past_sessions failed", exc_info=True)
                past = []
            older_rows = _history_resume_rows(past)

        if not before_rows and not older_rows:
            self.notify("nothing to resume", severity="warning")
            return

        def handle_result(selected: list[dict] | None) -> None:
            if not selected:
                return
            self._resume_entries(selected)

        self.push_screen(
            ResumeScreen([("Before the restart", before_rows), ("Older sessions", older_rows)], now),
            handle_result,
        )

    def _resume_entries(self, selected: list[dict]) -> None:
        """Each entry takes the next terminal already sitting at a prompt; a
        new window is opened only once those run out."""
        pool = self._free_shell_sessions()
        opened = 0
        reused = 0
        for entry in selected:
            account = by_label(self._accounts, entry.get("account")) or self._accounts[0]
            command = self._claude_command(account, entry["cwd"], f"--resume {shlex.quote(entry['session_id'])}")
            ok, used_pane = self._dispatch_command(pool, command)
            if ok:
                opened += 1
                if used_pane:
                    reused += 1
        where = f"{reused} in open terminals" if reused else "in new windows"
        self.notify(f"resumed {opened}/{len(selected)} session(s), {where}")
        self.trigger_refresh()


# Typed into a session's tab to toggle its bridge; the registry then adds or
# drops bridgeSessionId within a few seconds.
REMOTE_CONTROL_COMMAND = "/remote-control"


def _plain(value: Text | str) -> str:
    """build_rows may hand back Rich Text cells (HAL row styling); the CLI
    just wants their plain text."""
    return value.plain if isinstance(value, Text) else value


def cmd_list() -> None:
    state_store = StateStore()
    iterm_lister = iterm_mod.ItermLister()
    now = datetime.now()
    sessions = sources.gather_sessions(state_store, iterm_lister)
    state_store.prune({s.session_id for s in sessions}, now)
    rows = build_rows(sessions, now)

    header = (
        f"  {'STATE':<12} {'AGE':>5} {'CTX':>4}  {'MODEL':<10} {'ACCT':<8} RC {'DIR':<22} {'TITLE':<40} "
        f"{'PARKED':<12} {'#':>4} RECAP"
    )
    print(header)
    for r in rows:
        new = _plain(r["new"]) or " "
        state = _plain(r["state"])
        age = _plain(r["age"])
        ctx = _plain(r["ctx"])
        model = _plain(r["model"])[:10]
        account = _plain(r["account"])[:8]
        remote = _plain(r["remote"]) or " "
        directory = _plain(r["dir"])[:22]
        title = _plain(r["title"])[:40]
        parked = _plain(r["parked"])
        recap = _plain(r["recap"])
        win = _plain(r["win"])
        print(
            f"{new} {state:<12} {age:>5} {ctx:>4}  {model:<10} {account:<8} {remote}  {directory:<22} {title:<40} "
            f"{parked:<12} {win:>4} {recap}"
        )


def _find_session(target: str) -> Session | None:
    """See model.find_session: name, pid, id, terminal number, repo or a
    fragment of the title."""
    state_store = StateStore()
    iterm_lister = iterm_mod.ItermLister()
    return find_session(sources.gather_sessions(state_store, iterm_lister), target)


PROMPTS_DIR = STATE_PATH.parent / "prompts"
PROMPT_KEEP = timedelta(days=7)
OPEN_WAIT_SECONDS = 180
OPEN_POLL_SECONDS = 5


def _write_prompt_file(prompt: str, directory: Path = PROMPTS_DIR) -> Path:
    """The first prompt goes to claude through a file: typed terminal input
    is cut at 1024 bytes (MAX_CANON), which once left a long prompt's
    closing quote off and the shell waiting at a continuation line. Files
    older than a week are dropped on the way."""
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - PROMPT_KEEP.total_seconds()
    for old in directory.glob("*.txt"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    path = directory / f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.txt"
    path.write_text(prompt)
    return path


def open_command(account: Account, launch_dir: str, name: str | None, prompt_file: Path | None) -> str:
    """The short line typed into the shell: the prompt is read from its
    file by the shell, never typed."""
    args = [f"-n {shlex.quote(name)}"] if name else []
    if prompt_file is not None:
        args.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
    tail = f" {' '.join(args)}" if args else ""
    return f"cd {shlex.quote(launch_dir)} && {account.command_prefix()}claude{tail}"


def open_launch(directory: str, prompt: str) -> tuple[str, str]:
    """Every session starts in the home base (`podbay config home-repo`)
    when there is one; the repo it is for goes into its first prompt.
    Returns (launch directory, prompt)."""
    target = os.path.abspath(os.path.expanduser(directory))
    home = sources.REPOS_DIR / HOME_BASE if HOME_BASE else None
    if home is None or not home.is_dir() or Path(target) == home:
        return target, prompt
    lead = voice.open_target(target)
    return str(home), f"{lead}\n\n{prompt}" if prompt else lead


def _ancestor_pids(pid: int, limit: int = 30) -> list[int]:
    """`pid`'s parent, its parent, and so on up to launchd."""
    chain: list[int] = []
    while pid > 1 and len(chain) < limit:
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2).stdout
            pid = int(out.strip())
        except (subprocess.SubprocessError, OSError, ValueError):
            break
        chain.append(pid)
    return chain


def _calling_session(sessions: list[Session], ancestors: list[int]) -> Session | None:
    """The Claude session this podbay process runs under: the first
    ancestor that is a live session's pid. None from a plain shell."""
    by_pid = {s.pid: s for s in sessions if not s.is_shell}
    return next((by_pid[pid] for pid in ancestors if pid in by_pid), None)


def _new_session_on(tty: str, before: set[str]) -> Session | None:
    sessions = sources.gather_sessions(StateStore(), iterm_mod.ItermLister())
    return next((s for s in sessions if not s.is_shell and s.tty == tty and s.session_id not in before), None)


def _wait_for_claude(tty: str, before: set[str], marker: str | None, wait: int) -> bool:
    """True once a new Claude session sits on `tty` in the registry, or the
    screen shows the Claude banner below the typed command (`marker` is
    text unique to that command). A slow machine takes over a minute to
    register a new session."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(OPEN_POLL_SECONDS)
        if marker:
            screen = iterm_mod.read_session_text(tty, max_lines=60) or ""
            if marker in screen and "Claude Code" in screen.rsplit(marker, 1)[1]:
                return True
        if _new_session_on(tty, before) is not None:
            return True
    return False


# After the banner shows, the registry can take a while longer to list the
# session; this long podbay keeps looking for its id to record.
REGISTER_WAIT_SECONDS = 30


def _record_session_id(tty: str, before: set[str], opened_at: datetime) -> None:
    deadline = time.monotonic() + REGISTER_WAIT_SECONDS
    while True:
        started = _new_session_on(tty, before)
        if started is not None:
            opened_mod.set_session(tty, opened_at, started.session_id)
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(OPEN_POLL_SECONDS)


def cmd_open(directory: str, account_label: str | None, name: str | None, prompt: str, wait: int = OPEN_WAIT_SECONDS) -> None:
    """Start claude in the first terminal sitting at an empty shell prompt,
    or in a new window, and wait until it is up; prints where, or the
    screen's tail and exits 1 when it never came up."""
    accounts = discover()
    account = by_label(accounts, account_label)
    if account is None:
        print(f"no account {account_label!r}; known: {', '.join(a.label for a in accounts)}", file=sys.stderr)
        sys.exit(1)
    launch_dir, prompt = open_launch(directory, prompt)
    prompt_file = _write_prompt_file(prompt) if prompt else None
    command = open_command(account, launch_dir, name, prompt_file)
    sessions = sources.gather_sessions(StateStore(), iterm_mod.ItermLister())
    before = {s.session_id for s in sessions if not s.is_shell}
    busy = sources.busy_ttys()
    own = None
    try:
        own = os.ttyname(0)
    except OSError:
        pass
    candidates = [s for s in sessions if s.is_shell and s.tty and s.tty not in busy and s.tty != own]
    target = next((s for s in candidates if iterm_mod.at_empty_prompt(s.tty)), None)
    if target is not None:
        if not iterm_mod.send_text(target.tty, command):
            print("could not type into the free terminal", file=sys.stderr)
            sys.exit(1)
        tty, where = target.tty, f"terminal #{target.terminal or '?'} ({target.tty})"
    else:
        opened = iterm_mod.open_window_with_tty(command=command)
        if not opened or not opened[1]:
            print("could not open a new iTerm2 window", file=sys.stderr)
            sys.exit(1)
        tty, where = opened[1], f"a new window ({opened[1]})"
    caller = _calling_session(sessions, _ancestor_pids(os.getpid()))
    opened_at = datetime.now()
    opened_mod.record(tty, name, caller.name if caller else None, opened_at, prompt=prompt)
    if wait <= 0:
        print(f"typed into {where} as {account.label}: {launch_dir}")
        return
    print(f"waiting up to {wait} s for claude in {where}", flush=True)
    marker = prompt_file.name if prompt_file else None
    if _wait_for_claude(tty, before, marker, wait):
        print(voice.open_started(where, account.label, launch_dir), flush=True)
        _record_session_id(tty, before, opened_at)
        return
    print(voice.open_failed(where, wait), file=sys.stderr)
    screen = iterm_mod.read_session_text(tty, max_lines=60) or ""
    tail = [line for line in screen.splitlines() if line.strip()][-15:]
    print("\n".join(tail) or "(nothing readable)", file=sys.stderr)
    sys.exit(1)


def cmd_board(out: Path | None) -> None:
    """Write the board from the live inventory and print its path."""
    snapshots = sources.read_status_snapshots()
    sessions = sources.gather_sessions(StateStore(), iterm_mod.ItermLister(), status_snapshots=snapshots)
    payload = inventory_payload(sessions, set())
    limits = {a.label: sources.newest_limits(snapshots, a.label) for a in discover()}
    headlines, problem = board.load_headlines()
    if problem:
        print(f"headlines ignored: {problem}", file=sys.stderr)
    path = board.write(board.render(payload, limits=limits, headlines=headlines), out)
    print(path)


def cmd_excerpt(target: str, turns: int) -> None:
    match = _find_session(target)
    if match is None or match.is_shell:
        print(f"no live session matches {target!r}", file=sys.stderr)
        sys.exit(1)
    account = by_label(discover(), match.account)
    path = sources.transcript_path_for(match.cwd, match.session_id, account.projects_dir if account else sources.PROJECTS_DIR)
    if path is None:
        print(f"{target!r} has no transcript yet", file=sys.stderr)
        sys.exit(1)
    print(reviewer.excerpt(path, turns))


def cmd_focus(target: str) -> None:
    match = _find_session(target)
    if match is None:
        print(f"no live session matches {target!r}", file=sys.stderr)
        sys.exit(1)

    tty = iterm_mod.get_tty_for_pid(match.pid)
    if not tty or not iterm_mod.focus_tty(tty):
        print(f"could not find an iTerm2 tab for {target!r} (pid {match.pid})", file=sys.stderr)
        sys.exit(1)


def cmd_send(target: str, text: str) -> None:
    """Type `text` into a session's tab. Run from inside a Claude session
    (Head Jeeves passing an order on), the text is recorded as the agent's,
    so the mood gauge does not read it as the user's."""
    sessions = sources.gather_sessions(StateStore(), iterm_mod.ItermLister())
    match = find_session(sessions, target)
    if match is None:
        print(f"no live session matches {target!r}", file=sys.stderr)
        sys.exit(1)

    tty = iterm_mod.get_tty_for_pid(match.pid)
    if not tty or not iterm_mod.send_text(tty, text):
        print(f"could not find an iTerm2 tab for {target!r} (pid {match.pid})", file=sys.stderr)
        sys.exit(1)
    caller = _calling_session(sessions, _ancestor_pids(os.getpid()))
    if caller is not None:
        opened_mod.record_sent(match.session_id, text, caller.name or caller.session_id, datetime.now())


# How long a session gets to exit after SIGTERM before close gives up.
CLOSE_WAIT_SECONDS = 15
# A session doing any of these has not finished.
UNFINISHED = (WORKING, WATCHING, STALLED)


def _pid_gone(pid: int, wait: float) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def cmd_close(target: str, force: bool) -> None:
    """End a finished Claude session (SIGTERM, as closing its terminal
    would) and close its iTerm2 tab, or its window when that tab was the
    window's only one. A session still working is left alone unless
    `force`; Head Jeeves' own session is never closed."""
    sessions = sources.gather_sessions(StateStore(), iterm_mod.ItermLister())
    match = find_session(sessions, target)
    if match is None:
        print(f"no live session matches {target!r}", file=sys.stderr)
        sys.exit(1)
    if is_head_jeeves(match):
        print(voice.close_refused_head_jeeves(), file=sys.stderr)
        sys.exit(1)
    state = match.derive_status(datetime.now())
    if state in UNFINISHED and not force:
        print(voice.close_refused_working(match.title, STATUS_LABELS[state][1]), file=sys.stderr)
        sys.exit(1)
    tty = match.tty or iterm_mod.get_tty_for_pid(match.pid)
    try:
        os.kill(match.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if not _pid_gone(match.pid, CLOSE_WAIT_SECONDS):
        print(voice.close_still_running(match.title, CLOSE_WAIT_SECONDS), file=sys.stderr)
        sys.exit(1)
    opened_mod.forget(match.session_id)
    if not tty or not iterm_mod.close_tty(tty):
        print(voice.closed_no_tab(match.title))
        return
    print(voice.closed(match.title, match.terminal))


def cmd_config(key: str | None, value: str | None) -> None:
    """`podbay config` lists every setting, `podbay config home-repo` prints
    one, `podbay config home-repo jeeves` sets it (takes effect at the next
    podbay start)."""
    if key is None:
        data = config.read()
        for name, field in sorted(config.KEYS.items()):
            print(f"{name} = {data.get(field, '')}")
        return
    if value is None:
        print(config.read().get(config.KEYS[key], ""))
        return
    data = config.set_value(key, value)
    print(f"{key} = {data.get(config.KEYS[key], '')}  ({config.CONFIG_PATH}; restart podbay to apply)")


def cmd_notify(text: str) -> int:
    """`podbay notify <text>`: push one line to the user's phone through the
    configured notify command. One line out; exit 1 when nothing was sent,
    never a traceback."""
    sent, reason = notify_mod.send(text)
    if sent:
        print(voice.notify_sent())
        return 0
    print(voice.notify_off() if reason == "off" else voice.notify_failed(reason), file=sys.stderr)
    return 1


def cmd_inventory(as_table: bool, as_status: bool, exclude: list[str]) -> None:
    """Print the deterministic session inventory. Never touches the TUI.
    Default (no --table/--status) is JSON, one gather_sessions() call shared
    by every rendering, same as cmd_list."""
    state_store = StateStore()
    iterm_lister = iterm_mod.ItermLister()
    sessions = sources.gather_sessions(state_store, iterm_lister)
    payload = inventory_payload(sessions, set(exclude))

    if as_table:
        print(render_table(payload))
    elif as_status:
        print(render_status(payload))
    else:
        json.dump(payload, sys.stdout, default=str)
        sys.stdout.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="podbay")
    parser.add_argument(
        "--no-splash", action="store_true", help="skip the startup splash animation"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="print sessions as plain text")

    target_help = "session name, pid, id, terminal number (#6), repo, or a fragment of its title"
    focus_parser = sub.add_parser("focus", help="focus a session's iTerm2 tab")
    focus_parser.add_argument("target", help=target_help)

    send_parser = sub.add_parser("send", help="send a message to a session's iTerm2 tab")
    send_parser.add_argument("target", help=target_help)
    send_parser.add_argument("text", nargs="+", help="message text")

    notify_parser = sub.add_parser("notify", help="push one line to the user's phone through `podbay config notify-command` (see podbay/notify.py)")
    notify_parser.add_argument("text", nargs="+", help="the message, one standalone sentence")

    config_parser = sub.add_parser("config", help="show or set a podbay setting (see podbay/config.py)")
    config_parser.add_argument("key", nargs="?", choices=sorted(config.KEYS), help="the setting; none lists them all")
    config_parser.add_argument("value", nargs="?", help="the new value; none prints the current one; '' clears it")

    board_parser = sub.add_parser("board", help="write the status board page (decisions, ready to test, in progress, by project) for Head Jeeves to publish")
    board_parser.add_argument("--out", default=None, metavar="PATH", help=f"where to write it (default {board.BOARD_PATH})")

    excerpt_parser = sub.add_parser("excerpt", help="print a session's last turns as plain text (what Head Jeeves reads)")
    excerpt_parser.add_argument("target", help=target_help)
    excerpt_parser.add_argument("--turns", type=int, default=reviewer.TURNS, help=f"how many entries (default {reviewer.TURNS})")

    open_parser = sub.add_parser("open", help="start a claude session in a free terminal (or a new window) and wait until it is up")
    open_parser.add_argument("directory", help="the repo it is for; it starts in the home base and is told this in its first prompt")
    open_parser.add_argument("--account", default=None, metavar="LABEL", help="the account to run as (default: the default account)")
    open_parser.add_argument("--name", default=None, metavar="NAME", help="the session name (claude -n)")
    open_parser.add_argument("--wait", type=int, default=OPEN_WAIT_SECONDS, metavar="SECONDS", help="how long to wait for claude to come up (0: do not wait)")
    open_parser.add_argument("prompt", nargs="*", help="the first prompt, handed to claude through a file")

    close_parser = sub.add_parser("close", help="end a finished claude session and close its iTerm2 tab, or its window when that was the only tab")
    close_parser.add_argument("target", help=target_help)
    close_parser.add_argument("--force", action="store_true", help="close it even while it is working")

    inventory_parser = sub.add_parser("inventory", help="print a deterministic session inventory")
    inventory_parser.add_argument("--json", action="store_true", help="print JSON (default)")
    inventory_parser.add_argument("--table", action="store_true", help="print as a plain-text table")
    inventory_parser.add_argument("--status", action="store_true", help="print as a status digest")
    inventory_parser.add_argument(
        "--exclude", action="append", default=[], metavar="NAME",
        help="session name or short id to exclude (repeatable)",
    )

    args = parser.parse_args()
    logs.configure()
    logs.install_excepthook()
    log.info("start pid=%d argv=%s", os.getpid(), sys.argv[1:])

    if args.command == "config":
        cmd_config(args.key, args.value)
    elif args.command == "list":
        cmd_list()
    elif args.command == "focus":
        cmd_focus(args.target)
    elif args.command == "send":
        cmd_send(args.target, " ".join(args.text))
    elif args.command == "notify":
        sys.exit(cmd_notify(" ".join(args.text)))
    elif args.command == "board":
        cmd_board(Path(args.out) if args.out else None)
    elif args.command == "excerpt":
        cmd_excerpt(args.target, args.turns)
    elif args.command == "open":
        cmd_open(args.directory, args.account, args.name, " ".join(args.prompt), args.wait)
    elif args.command == "close":
        cmd_close(args.target, args.force)
    elif args.command == "inventory":
        cmd_inventory(args.table, args.status, args.exclude)
    else:
        no_splash = args.no_splash or os.environ.get("PODBAY_NO_SPLASH") == "1"
        # Captured before Textual takes the alternate screen, so the splash
        # can draw the eye over what the terminal was showing.
        backdrop = None if no_splash else iterm_mod.capture_own_screen()
        app = PodbayApp(
            no_splash=no_splash,
            backdrop=backdrop,
            notifications_path=notifications.LOG_PATH,
            voice_mode=config.voice_mode(),
            head_jeeves=config.head_jeeves_on(),
            review_model=config.review_model(),
            head_jeeves_account=config.head_jeeves_account(),
        )
        app.run()
        log.info("exit return_code=%s%s", app.return_code, " (forced)" if app._force_quit else "")
        if app._force_quit:
            logging.shutdown()
            os._exit(app.return_code or 0)


if __name__ == "__main__":
    main()
