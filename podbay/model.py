"""Merged session model and status derivation.

A Session is the join of the live registry entry, the transcript recap,
podbay's own seen state, and (best effort) an iTerm2 tab. Nothing
here reads files directly -- that lives in sources.py, iterm.py, state.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config


WORKING = "working"
# Turn ended, but a background Bash command or Monitor it started still runs.
WATCHING = "watching"
NEEDS_YOU = "needs_you"
STALLED = "stalled"
EMPTY = "empty"
# Distinct from the raw registry status string "shell" (Session.status),
# which means something else: a Claude Code session whose tool is a shell.
SHELL = "shell_pane"

REMOTE_URL_BASE = "https://claude.ai/code/"

# Head Jeeves' own session, matched by registry name (see app.py).
HEAD_JEEVES_NAME = "head-jeeves"

STALLED_THRESHOLD = timedelta(minutes=10)
# A background Bash command has no timeout of its own; past this age a start
# with no end record is taken as lost (the notification fell outside the tail).
BACKGROUND_TASK_MAX_AGE = timedelta(hours=6)

# glyph, word shown in the state column
STATUS_LABELS = {
    WORKING: ("*", "working"),
    WATCHING: ("o", "watching"),
    NEEDS_YOU: ("?", "needs you"),
    STALLED: ("~", "stalled"),
    EMPTY: ("-", "empty"),
    SHELL: ("$", "shell"),
}


@dataclass
class Session:
    session_id: str
    pid: int
    cwd: str
    name: str
    name_source: str
    status: str  # raw registry status: busy | idle | waiting | shell
    status_updated_at: datetime
    updated_at: datetime
    started_at: datetime

    # Which Claude Code account (config dir) the session runs under; the
    # label from accounts.py, "claude" for the default ~/.claude.
    account: str = "claude"

    # Remote Control: the claude.ai session id the registry reports while the
    # session is bridged to the web and phone apps (`bridgeSessionId`), None
    # when it is not. See remote_url.
    remote_session_id: str | None = None

    git_branch: str | None = None
    recap: str | None = None
    recap_ts: datetime | None = None
    last_prompt: str | None = None
    # The newest prompts you typed, oldest first, for the mood gauge (mood.py).
    recent_prompts: list[str] = field(default_factory=list)

    # Primary signal: newest user/assistant record in the transcript.
    # "in_progress" = a prompt or tool result awaiting the model, or an
    # assistant turn that stopped on tool_use; "end_turn" = the assistant
    # finished its turn; None = no transcript signal (fall back).
    last_turn: str | None = None
    last_turn_ts: datetime | None = None

    # Background Bash commands and Monitors the transcript started:
    # {"id", "ts" (local datetime), "timeout_ms" (Monitor only), "ended"}.
    background_tasks: list[dict] = field(default_factory=list)
    # Subagent transcripts modified since the main turn ended: a background
    # Agent call ends the main turn while the work goes on in those files.
    subagents_running: int = 0
    # The newest write to any of its subagent transcripts.
    subagent_written_at: datetime | None = None

    # iTerm2 tab-title glyph fallback (None when no tab or unrecognized
    # glyph); registry status is the last-resort fallback.
    tab_busy: bool | None = None

    # Context-window usage percent from the most recent status-line
    # snapshot for this session; None when no snapshot has been seen yet.
    context_pct: float | None = None

    # Model display name ("Fable 5.1") from the status-line snapshot, else
    # derived from the transcript's model id; effort level from the snapshot.
    model: str | None = None
    effort: str | None = None

    seen_at: datetime | None = None

    # False when no transcript file exists yet: a freshly opened session
    # nobody has typed into.
    has_transcript: bool = True

    # Repo names (under REPOS_DIR) seen in the transcript's cwd/tool-call
    # inputs, and the subset actually edited (Edit/Write/...).
    repos_touched: list[str] = field(default_factory=list)
    repos_edited: list[str] = field(default_factory=list)

    # What an idle session looks stuck on -- see sources.compute_waiting_on.
    # {"kind": "ask_user_question" | "permission" | "question_text" |
    # "prompt" | "subagents_running", "detail": str}, or None when nothing
    # is pending.
    waiting_on: dict | None = None

    # Whether the newest assistant record in the transcript ended its turn
    # (stop_reason == "end_turn"); None when there is no transcript signal.
    turn_ended: bool | None = None

    iterm_tab_id: str | None = None
    iterm_title: str | None = None

    # The name of the session that started this one with `podbay open`
    # (see opened.py); None when nothing recorded it.
    opened_by: str | None = None

    # True for a synthetic row built from an iTerm2 pane with no live
    # Claude registry entry -- a plain shell. tty/window_id identify the
    # pane the same way as for a Claude session.
    is_shell: bool = False
    tty: str | None = None
    window_id: str | None = None
    tab_index: int | None = None
    window_number: int | None = None

    @property
    def terminal(self) -> str | None:
        """The number to say for "terminal #N": iTerm's window number, plus
        ".<tab>" for a session outside its window's first tab."""
        if not self.window_number:
            return None
        if self.tab_index and self.tab_index > 1:
            return f"{self.window_number}.{self.tab_index}"
        return str(self.window_number)

    @property
    def title(self) -> str:
        return self.iterm_title or self.name

    @property
    def remote_url(self) -> str | None:
        """Where this session is on claude.ai while Remote Control is on."""
        if not self.remote_session_id:
            return None
        return f"{REMOTE_URL_BASE}{self.remote_session_id}"

    @property
    def awaiting_prompt(self) -> bool:
        """The registry says a question or permission dialog is open, and
        that status is not older than the transcript's newest turn."""
        if self.status != "waiting":
            return False
        return self.last_turn_ts is None or self.status_updated_at >= self.last_turn_ts

    @property
    def unread(self) -> bool:
        """A finished answer you have not looked at yet."""
        if self.last_turn != "end_turn" or self.last_turn_ts is None:
            return False
        return self.seen_at is None or self.last_turn_ts > self.seen_at

    def running_background_tasks(self, now: datetime) -> list[dict]:
        """Started, not ended, started by this process (a restart kills
        them), and not past a Monitor's timeout or BACKGROUND_TASK_MAX_AGE."""
        running = []
        for task in self.background_tasks:
            ts = task.get("ts")
            if task.get("ended") or ts is None or ts < self.started_at:
                continue
            timeout_ms = task.get("timeout_ms")
            limit = timedelta(milliseconds=timeout_ms) if timeout_ms else BACKGROUND_TASK_MAX_AGE
            if now - ts <= min(limit, BACKGROUND_TASK_MAX_AGE):
                running.append(task)
        return running

    def derive_status(self, now: datetime) -> str:
        """Classify into exactly one bucket, first match wins.

        The transcript's last_turn is primary: "in_progress" means working
        (or STALLED if it's been in progress for more than
        STALLED_THRESHOLD -- likely a hung session or one waiting on a
        permission prompt); "end_turn" falls through to the needs_you
        logic. With no transcript signal at all (last_turn is
        None), fall back to the iTerm2 tab-title glyph, and failing that to
        the (stale-prone) registry status. A finished turn whose subagents
        still run is WORKING; one with a background Bash command or Monitor
        still running is WATCHING, not needs_you. A
        session with no transcript file at all is EMPTY: opened, never used.
        is_shell wins over everything else: a plain terminal has no Claude
        turn to classify. A registry status of "waiting" newer
        than the last transcript turn wins over the transcript: an open
        AskUserQuestion or permission dialog is not in the transcript until
        answered, so the transcript alone would call that session working or
        stalled.
        """
        if self.is_shell:
            return SHELL
        if self.awaiting_prompt:
            return NEEDS_YOU
        if self.last_turn == "in_progress":
            if self.last_turn_ts is not None and (now - self.last_turn_ts) > STALLED_THRESHOLD:
                return STALLED
            return WORKING
        if self.subagents_running:
            return WORKING  # the turn ended, its agents did not: nothing is finished yet
        if self.last_turn is None:
            if self.tab_busy is not None:
                if self.tab_busy:
                    return WORKING
            elif self.status in ("busy", "shell"):
                return WORKING

        if self.has_transcript and self.running_background_tasks(now):
            return WATCHING
        if not self.has_transcript:
            return EMPTY
        return NEEDS_YOU

    @property
    def activity_at(self) -> datetime:
        """The newest sign of life, which every age is measured from: the
        registry's status change, the newest turn (a subagent's in-progress
        turn included) or a subagent transcript write. The registry alone
        stops at the main turn's end while its agents work on."""
        return max(t for t in (self.status_updated_at, self.last_turn_ts, self.subagent_written_at) if t is not None)

    def age_seconds(self, now: datetime) -> float:
        return (now - self.activity_at).total_seconds()


# needs_you and STALLED share a group -- sorted together by age, not
# needs_you-then-stalled -- since both mean "likely wants your attention".
# Plain shells sit below all Claude work.
_GROUP_ORDER = {NEEDS_YOU: 1, STALLED: 1, WORKING: 2, WATCHING: 2, EMPTY: 3, SHELL: 4}


def sort_key(session: Session, now: datetime):
    """Sort order: needs_you/stalled (oldest idle first), then working,
    then empty (unused sessions), then shell (plain terminals)."""
    derived = session.derive_status(now)
    group = _GROUP_ORDER[derived]
    secondary = session.activity_at  # ascending -> oldest first
    if is_head_jeeves(session):
        return (-1, secondary)  # ahead of every group, whatever his status
    return (group, secondary)


def is_head_jeeves(session: Session) -> bool:
    return session.name == HEAD_JEEVES_NAME


def find_session(sessions: list[Session], target: str) -> Session | None:
    """The one session `target` means, the way a person says it: its name,
    pid or session id (or a prefix of the id), its terminal number (`#6` or
    `6`), its repo, or a fragment of its title, case-insensitive. An exact
    name wins; a fragment or repo has to fit exactly one session. Shell rows
    are not candidates."""
    target = target.strip()
    if not target:
        return None
    candidates = [s for s in sessions if not s.is_shell]
    for s in candidates:
        if s.name == target or str(s.pid) == target or s.session_id == target:
            return s
    number = target[1:] if target.startswith("#") else target
    for s in candidates:
        if s.terminal == number:
            return s
    by_prefix = [s for s in candidates if len(target) >= 4 and s.session_id.startswith(target)]
    if len(by_prefix) == 1:
        return by_prefix[0]
    low = target.lower()
    by_title = [s for s in candidates if low in s.title.lower() or low == s.name.lower()]
    if len(by_title) == 1:
        return by_title[0]
    by_repo = [s for s in candidates if low in {r.lower() for r in s.repos_touched} or s.cwd.lower().rstrip("/").endswith("/" + low)]
    if len(by_repo) == 1:
        return by_repo[0]
    return None


def humanize_age(seconds: float) -> str:
    """4m, 2h, 3d style humanised duration. Negative/zero collapses to 0m."""
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


# The repo every session is launched from, when there is one (`podbay config
# home-repo <name>`, a directory name under ~/Repositories): a tool call that
# only reads there says nothing about where the work is, so it counts only
# when edited. See repo_groups. Empty when no repo plays that role.
HOME_BASE = config.home_repo()


def repo_groups(sessions: list[Session]) -> list[dict]:
    """Group on the repos a session actually works in, not on its cwd: with
    a home base every session is launched from it, so cwd alone puts them
    all in one group. A project repo counts when any tool call touched it;
    the home base itself counts only when the session edited a file there."""
    by_repo: dict[str, list[str]] = {}
    for s in sessions:
        repos = {r for r in s.repos_touched if r != HOME_BASE} | ({HOME_BASE} & set(s.repos_edited))
        for repo in repos:
            by_repo.setdefault(repo, []).append(s.name)
    return [{"repo": repo, "sessions": names} for repo, names in sorted(by_repo.items()) if len(names) >= 2]


def shared_repos(session: Session, groups: list[dict]) -> list[str]:
    """Repos this session shares with at least one other session, per the
    groups repo_groups() already computed."""
    return [g["repo"] for g in groups if session.name in g["sessions"]]
