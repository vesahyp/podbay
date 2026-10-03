"""Read-only joins across the live session registry, transcripts, podbay
state, and iTerm2 tabs. Nothing here mutates anything outside podbay's
own state store."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import iterm as iterm_mod
from .accounts import DEFAULT_LABEL, Account, discover
from .model import Session
from .state import SessionState, StateStore

SESSIONS_DIR = Path.home() / ".claude" / "sessions"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATUS_DIR = Path.home() / ".local" / "state" / "podbay" / "status"
REPOS_DIR = Path.home() / "Repositories"
TAIL_BYTES = 512 * 1024
SNAPSHOT_PRUNE_AFTER_DAYS = 14
PERMISSION_IDLE_MINUTES = 2.0

_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)
# Matched on the raw JSON line, where a newline is the two characters `\n`.
_TASK_ID_RE = re.compile(r"<task-notification>(?:\\n|\s)*<task-id>([^<\s\\]+)</task-id>")
_WS_RE = re.compile(r"\s+")
_REPO_PATH_RE = re.compile(re.escape(str(REPOS_DIR)) + r"/([A-Za-z0-9][A-Za-z0-9._-]*)")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PATH_TOOLS = EDIT_TOOLS | {"Bash", "Read", "Glob", "Grep", "LSP"}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    return True


def _ms_to_dt(ms: int | float | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000)


def _iso_to_local_dt(ts: str | None) -> datetime | None:
    """Parse a transcript record's ISO-8601 timestamp (UTC, 'Z' suffix) to
    a naive local datetime, matching the other (registry-derived) naive
    local datetimes on Session."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def read_registry(sessions_dir: Path = SESSIONS_DIR) -> list[dict]:
    """Live registry entries only (dead pids' stale files are dropped)."""
    entries = []
    if not sessions_dir.is_dir():
        return entries
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        pid = data.get("pid")
        if pid is None or not _pid_alive(int(pid)):
            continue
        entries.append(data)
    return entries


def read_status_snapshots(status_dir: Path = STATUS_DIR) -> dict[str, dict]:
    """Read the podbay status-line snapshots (context/rate-limit usage),
    keyed by session id. Snapshot files older than SNAPSHOT_PRUNE_AFTER_DAYS
    are pruned (not just skipped) -- dead sessions stop emitting new ones,
    and the live-registry join already drops anything for a dead pid."""
    result: dict[str, dict] = {}
    if not status_dir.is_dir():
        return result
    cutoff = time.time() - SNAPSHOT_PRUNE_AFTER_DAYS * 86400
    for path in status_dir.glob("*.json"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        session_id = data.get("session_id") or path.stem
        context_window = data.get("context_window") or {}
        rate_limits = data.get("rate_limits") or {}
        five = rate_limits.get("five_hour") or {}
        week = rate_limits.get("seven_day") or {}
        result[session_id] = {
            "account": data.get("account") or DEFAULT_LABEL,
            "model": (data.get("model") or {}).get("display_name"),
            "effort": (data.get("effort") or {}).get("level"),
            "context_pct": context_window.get("used_percentage"),
            "five_pct": five.get("used_percentage"),
            "five_resets_at": five.get("resets_at"),
            "week_pct": week.get("used_percentage"),
            "week_resets_at": week.get("resets_at"),
            "mtime": mtime,
        }
    return result


def newest_limits(snapshots: dict[str, dict], account: str | None = None) -> dict:
    """5h and 7d usage, each from the newest snapshot that carries it. Both
    figures are account-wide: every session of one account reports the same
    pair whatever model it runs, and a single snapshot often has only one of
    the two. With `account`, only that account's snapshots count (a snapshot
    that names no account belongs to the default one)."""
    limits: dict = {"five_pct": None, "five_resets_at": None, "week_pct": None, "week_resets_at": None}
    if account is not None:
        snapshots = {k: s for k, s in snapshots.items() if (s.get("account") or DEFAULT_LABEL) == account}
    for snap in sorted(snapshots.values(), key=lambda s: s.get("mtime", 0)):
        if snap.get("five_pct") is not None:
            limits["five_pct"] = snap["five_pct"]
            limits["five_resets_at"] = snap.get("five_resets_at")
        if snap.get("week_pct") is not None:
            limits["week_pct"] = snap["week_pct"]
            limits["week_resets_at"] = snap.get("week_resets_at")
    return limits


def model_name_from_id(model_id: str | None) -> str | None:
    """'claude-fable-5-1' -> 'Fable 5.1', 'claude-haiku-4-5-20251001' ->
    'Haiku 4.5'. Fallback when no status-line snapshot names the model."""
    if not model_id:
        return None
    parts = [p for p in model_id.split("-") if p != "claude"]
    words = [p.capitalize() for p in parts if not p.isdigit()]
    digits = [p for p in parts if p.isdigit() and len(p) < 8]  # drop date stamps
    version = ".".join(digits)
    return " ".join(x for x in (" ".join(words), version) if x) or model_id


def _slug_for_cwd(cwd: str) -> str:
    return cwd.replace("/", "-")


def transcript_path_for(cwd: str, session_id: str, projects_dir: Path = PROJECTS_DIR) -> Path | None:
    direct = projects_dir / _slug_for_cwd(cwd) / f"{session_id}.jsonl"
    if direct.exists():
        return direct
    matches = list(projects_dir.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def _tail_lines(path: Path, tail_bytes: int) -> list[str]:
    """Read only the tail of a (possibly huge) file and split into lines,
    dropping a leading partial line when the read didn't start at byte 0."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - tail_bytes))
            data = fh.read()
    except OSError:
        return []

    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if size > tail_bytes:
        lines = lines[1:]  # drop the partial first line
    return lines


# Upper bound on how far back read_conversation walks for its entries: a
# single tool result (a Slack file read, a big grep) can run to hundreds of
# KB, so a fixed tail window can end inside one record and hide every turn
# before it. Walking backwards until `limit` entries are found fixes that;
# the cap only bounds the cost on a pathological file.
CONVERSATION_MAX_BYTES = 64 * 1024 * 1024
_REVERSE_CHUNK = 256 * 1024


def _lines_reversed(path: Path, max_bytes: int = CONVERSATION_MAX_BYTES, chunk_size: int = _REVERSE_CHUNK):
    """Yield the complete lines of `path` from the last to the first, reading
    the file backwards in chunks and never holding more than one line plus a
    chunk in memory. Stops after `max_bytes`, dropping the partial line the
    cut lands in. Yields nothing on any OSError."""
    try:
        size = path.stat().st_size
        fh = path.open("rb")
    except OSError:
        return
    with fh:
        pos = size
        floor = max(0, size - max_bytes)
        carry = b""
        while pos > floor:
            start = max(floor, pos - chunk_size)
            fh.seek(start)
            data = fh.read(pos - start) + carry
            pos = start
            parts = data.split(b"\n")
            carry = parts[0]
            for raw in reversed(parts[1:]):
                yield raw.decode("utf-8", errors="replace")
        if pos == 0 and carry:
            yield carry.decode("utf-8", errors="replace")


def _repos_in_text(text: str) -> set[str]:
    """Repo names under REPOS_DIR mentioned in a path or command.
    Underscore-prefixed dirs are old copies and are skipped."""
    return {m for m in _REPO_PATH_RE.findall(text) if not m.startswith("_")}


def _note_repos(block: dict, result: dict) -> None:
    """A tool call that acts on a repo path counts as touching that repo.
    Only path-acting tools count: an Agent or SendMessage prompt that merely
    mentions a path is not work in that repo. Edits also count as editing,
    the stronger signal the grouping uses for the always-shared home base."""
    if block.get("name") not in PATH_TOOLS:
        return
    inp = block.get("input") or {}
    result["repos_touched"].update(_repos_in_text(json.dumps(inp, default=str)))
    if block.get("name") in EDIT_TOOLS:
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        result["repos_edited"].update(_repos_in_text(path))


def tail_read_transcript(path: Path, tail_bytes: int = TAIL_BYTES, include_sidechain: bool = False) -> dict:
    """Read only the tail of a (possibly huge) transcript and extract the
    newest recap, last prompt, last assistant text and git branch, plus (for
    the inventory) repos touched/edited, resolved tool_use ids, and the
    newest assistant record's stop_reason/tool_use blocks.
    `include_sidechain` is for subagent transcripts, where every record is a
    sidechain record."""
    result = {
        "recap": None,
        "recap_ts": None,
        "last_prompt": None,
        "last_assistant_text": None,
        "git_branch": None,
        "last_turn": None,
        "last_turn_ts": None,
        "model_id": None,
        "repos_touched": set(),
        "repos_edited": set(),
        "resolved_tool_use_ids": set(),
        "interrupted": False,
        "newest_assistant": None,
        "background_tasks": {},  # id -> {"id", "ts", "timeout_ms", "ended"}
    }
    newest_assistant_ts = None

    for line in _tail_lines(path, tail_bytes):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        rtype = record.get("type")
        if rtype == "user" and not record.get("isSidechain"):
            _note_background_task(record, result["background_tasks"])
        if "<task-notification>" in line:
            _end_notified_tasks(line, result["background_tasks"])
        branch = record.get("gitBranch")
        if branch:
            result["git_branch"] = branch

        relevant = include_sidechain or not record.get("isSidechain")
        if relevant and record.get("cwd"):
            result["repos_touched"].update(_repos_in_text(record["cwd"]))

        if rtype == "system" and record.get("subtype") == "away_summary":
            result["recap"] = record.get("content")
            result["recap_ts"] = record.get("timestamp")
        elif rtype == "last-prompt":
            result["last_prompt"] = record.get("lastPrompt")
        elif rtype == "assistant":
            model_id = (record.get("message") or {}).get("model")
            if model_id:
                result["model_id"] = model_id
            content = (record.get("message") or {}).get("content") or []
            texts = [c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if texts:
                result["last_assistant_text"] = texts[-1]

            if relevant:
                tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                for block in tool_uses:
                    _note_repos(block, result)
                ts = _iso_to_local_dt(record.get("timestamp"))
                if ts is not None and (newest_assistant_ts is None or ts >= newest_assistant_ts):
                    newest_assistant_ts = ts
                    result["newest_assistant"] = {
                        "stop_reason": (record.get("message") or {}).get("stop_reason"),
                        "text": "\n".join(texts),
                        "tool_uses": tool_uses,
                    }
        elif rtype == "user" and relevant:
            content = (record.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                        result["resolved_tool_use_ids"].add(block["tool_use_id"])

        # Newest record (in tail order) whose type is "user" or "assistant",
        # skipping sidechain records, drives last_turn/last_turn_ts -- keeps
        # overwriting as we walk forward so the final value is the newest.
        if rtype in ("user", "assistant") and relevant:
            if rtype == "assistant":
                stop_reason = (record.get("message") or {}).get("stop_reason")
                turn = "end_turn" if stop_reason == "end_turn" else "in_progress"
                result["interrupted"] = False
            elif _is_interrupt_record(record):
                # Esc writes a user record instead of a stop_reason, so the
                # turn is over even though the newest record is the user's.
                turn = "end_turn"
                result["interrupted"] = True
            else:
                turn = "in_progress"
                result["interrupted"] = False
            result["last_turn"] = turn
            result["last_turn_ts"] = _iso_to_local_dt(record.get("timestamp"))

    if not result["recap"] and result["last_assistant_text"]:
        result["recap"] = result["last_assistant_text"]

    return result


def _note_background_task(record: dict, tasks: dict) -> None:
    """A background Bash launch (`backgroundTaskId`) or a Monitor
    (`taskId` + `timeoutMs`) starts a task; a TaskStop result (`task_id`)
    ends it, and so does a <task-notification> (see _end_notified_tasks).
    Background agents are left to subagent_activity, which reads their own
    transcripts."""
    result = record.get("toolUseResult")
    ts = _iso_to_local_dt(record.get("timestamp"))
    if isinstance(result, dict):
        task_id = result.get("backgroundTaskId") or (result.get("taskId") if "timeoutMs" in result else None)
        if task_id:
            tasks.setdefault(task_id, {"id": task_id, "ts": ts, "timeout_ms": result.get("timeoutMs"), "ended": False})
        stopped = result.get("task_id")
        if stopped and stopped in tasks:
            tasks[stopped]["ended"] = True


def _end_notified_tasks(line: str, tasks: dict) -> None:
    """A completion notice ends its task wherever it lands: a user record
    when the session was idle, or queue-operation / queued_command records
    when it arrived mid-turn."""
    for task_id in _TASK_ID_RE.findall(line):
        if task_id in tasks:
            tasks[task_id]["ended"] = True


def _strip_wrapped_spans(text: str) -> str:
    text = _SYSTEM_REMINDER_RE.sub("", text)
    text = _TASK_NOTIFICATION_RE.sub("", text)
    return text


def _user_entry_text(content) -> str:
    """Extract keepable text from a user record's message.content: a bare
    string, or the text blocks of a list (tool_result and other block types
    dropped)."""
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
    else:
        parts = []
    return _strip_wrapped_spans("\n".join(parts)).strip()


def _tool_use_line(block: dict) -> str:
    name = block.get("name", "")
    tool_input = block.get("input")
    description = tool_input.get("description") if isinstance(tool_input, dict) else None
    if not description:
        description = str(tool_input if tool_input is not None else "")[:60]
    return f"⚙ {name}: {description}"


def _assistant_entries(content, timestamp) -> list[dict]:
    """Assistant message.content blocks -> display entries: consecutive text
    blocks join into one 'assistant' entry, each tool_use becomes its own
    'tool' entry (in place, preserving block order), thinking is dropped."""
    entries: list[dict] = []
    text_parts: list[str] = []

    def flush() -> None:
        if text_parts:
            entries.append({"role": "assistant", "timestamp": timestamp, "text": "\n".join(text_parts)})
            text_parts.clear()

    for block in content or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "thinking":
            continue
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            flush()
            entries.append({"role": "tool", "timestamp": timestamp, "text": _tool_use_line(block)})
    flush()
    return entries


def read_conversation(path: Path, limit: int = 40, max_bytes: int = CONVERSATION_MAX_BYTES) -> list[dict]:
    """Return up to `limit` recent conversation entries, in chronological
    order, walking the transcript backwards from its end until `limit`
    entries are found (or `max_bytes` have been read).

    Each entry is `{"role": "user" | "assistant" | "tool", "timestamp": ...,
    "text": ...}`. User records with isMeta or isSidechain set are skipped,
    as are assistant records with isSidechain set; system-reminder and
    task-notification spans are stripped from user text; entries left empty
    after stripping are dropped.
    """
    collected: list[list[dict]] = []  # per record, newest record first
    count = 0
    for line in _lines_reversed(path, max_bytes):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if record.get("isSidechain"):
            continue

        rtype = record.get("type")
        timestamp = record.get("timestamp")

        if rtype == "user":
            if record.get("isMeta"):
                continue
            content = (record.get("message") or {}).get("content")
            text = _user_entry_text(content)
            if not text:
                continue
            batch = [{"role": "user", "timestamp": timestamp, "text": text}]
        elif rtype == "assistant":
            content = (record.get("message") or {}).get("content")
            batch = _assistant_entries(content, timestamp)
        else:
            continue
        if not batch:
            continue
        collected.append(batch)
        count += len(batch)
        if count >= limit:
            break

    entries: list[dict] = []
    for batch in reversed(collected):
        entries.extend(batch)
    return entries[-limit:]


def subagent_activity(
    transcript_path: Path, session_id: str, main_last_turn_ts: datetime | None, tail_bytes: int = TAIL_BYTES
) -> tuple[str, datetime] | None:
    """A background Agent call ends the main turn, so the main transcript
    says end_turn while the agent works in its own file under
    <slug>/<session-id>/subagents/. Return the newest (last_turn,
    last_turn_ts) of a subagent still in progress, or None. Files not
    modified since the main transcript's last turn are skipped: the agent's
    completion notification starts a new main turn, which overtakes them."""
    subagents_dir = transcript_path.parent / session_id / "subagents"
    if not subagents_dir.is_dir():
        return None
    main_ts = main_last_turn_ts.timestamp() if main_last_turn_ts is not None else 0.0
    newest: tuple[str, datetime] | None = None
    for path in subagents_dir.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < main_ts:
                continue
        except OSError:
            continue
        sub = tail_read_transcript(path, tail_bytes, include_sidechain=True)
        ts = sub.get("last_turn_ts")
        if sub.get("last_turn") != "in_progress" or ts is None:
            continue
        if newest is None or ts > newest[1]:
            newest = ("in_progress", ts)
    return newest


def _collapse(text: str, limit: int) -> str:
    return _WS_RE.sub(" ", text).strip()[:limit]


def _tool_input_summary(block: dict, limit: int = 200) -> str:
    name = block.get("name", "")
    tool_input = block.get("input") or {}
    if isinstance(tool_input, dict):
        detail = tool_input.get("command") or tool_input.get("file_path") or str(tool_input)
    else:
        detail = str(tool_input)
    return _collapse(f"{name}: {detail}", limit)


INTERRUPT_MARKER = "[Request interrupted by user"


def _is_interrupt_record(record: dict) -> bool:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.startswith(INTERRUPT_MARKER)
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").startswith(INTERRUPT_MARKER) for b in content)
    return False


def compute_waiting_on(
    transcript: dict, registry_status: str, idle_minutes: float | None, subagents_running: int
) -> dict | None:
    """What an idle session looks stuck on, in precedence order: an open
    AskUserQuestion beats a plain pending tool_use (permission) beats a
    trailing question in finished prose, then the registry's own "waiting"
    status, and only when none of those apply do running subagents count as
    the wait reason.

    The registry check exists because Claude Code appends an AskUserQuestion
    (and a permission-gated tool call) to the transcript only after the
    dialog is answered: while the dialog is up, the transcript still ends at
    the previous tool result, and status "waiting" is the only signal."""
    newest = transcript.get("newest_assistant")
    resolved = transcript.get("resolved_tool_use_ids", set())

    if transcript.get("interrupted"):
        return {"kind": "interrupted", "detail": "turn interrupted by user, waiting for a new prompt"}

    if newest is not None:
        for block in newest["tool_uses"]:
            if block.get("name") == "AskUserQuestion" and block.get("id") not in resolved:
                questions = (block.get("input") or {}).get("questions") or []
                texts = [q.get("question", "") for q in questions if isinstance(q, dict)]
                return {"kind": "ask_user_question", "detail": " | ".join(t for t in texts if t)[:300]}

        pending = [b for b in newest["tool_uses"] if b.get("id") not in resolved]
        if pending and registry_status != "busy" and idle_minutes is not None and idle_minutes >= PERMISSION_IDLE_MINUTES:
            return {"kind": "permission", "detail": _tool_input_summary(pending[-1])}

        if newest.get("stop_reason") == "end_turn":
            text = (newest.get("text") or "").strip()
            if text.endswith("?"):
                sentences = re.split(r"(?<=[.!?])\s+", text)
                return {"kind": "question_text", "detail": _collapse(sentences[-1] if sentences else text, 300)}

    if registry_status == "waiting":
        return {"kind": "prompt", "detail": "a question or permission dialog is open (not in the transcript yet)"}
    if subagents_running > 0:
        return {"kind": "subagents_running", "detail": str(subagents_running)}
    return None


def count_running_subagents(slug_dir: Path, session_id: str, last_activity_at: datetime | None) -> int:
    subagents_dir = slug_dir / session_id / "subagents"
    if not subagents_dir.is_dir():
        return 0
    cutoff = last_activity_at.timestamp() if last_activity_at is not None else 0.0
    count = 0
    for path in subagents_dir.glob("*.jsonl"):
        try:
            count += path.stat().st_mtime > cutoff
        except OSError:
            continue
    return count


def _mark_seen_if_unread(state_store: StateStore, session_id: str, transcript: dict) -> SessionState:
    """The session's tab is the current iTerm2 tab, so you are looking at
    it: record a finished answer as seen. Writes only when there is
    something unread, so a quiet current tab costs no state write."""
    saved = state_store.get(session_id)
    ts = transcript.get("last_turn_ts")
    if transcript.get("last_turn") == "end_turn" and ts is not None and (saved.seen_at is None or ts > saved.seen_at):
        state_store.set_seen(session_id, datetime.now())
        saved = state_store.get(session_id)
    return saved


def _auto_unpark(state_store: StateStore, session_id: str, last_turn_ts: datetime | None) -> SessionState:
    """Clear an expired park once the session has had a turn after the due
    time -- otherwise the row stays `due` forever after you resume it.
    Turns before the due time leave the park alone."""
    saved = state_store.get(session_id)
    if saved.parked_until is not None and last_turn_ts is not None and last_turn_ts > saved.parked_until:
        state_store.set_parked(session_id, None)
        saved = state_store.get(session_id)
    return saved


def _all_pids_by_tty(timeout: float = 2.0) -> dict[str, int]:
    """One 'ps -e' call mapping tty -> lowest (session-leader) pid, so
    resolving every unmatched pane's pid costs one subprocess instead of
    one per pane. Empty on any ps failure -- never raises."""
    try:
        out = subprocess.run(
            ["ps", "-e", "-o", "pid=,tty="],
            capture_output=True, text=True, timeout=timeout,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    result: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        pid_str, tty_raw = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if tty_raw == "??":
            continue
        tty = tty_raw if tty_raw.startswith("/dev/") else f"/dev/{tty_raw}"
        if tty not in result or pid < result[tty]:
            result[tty] = pid
    return result


_IDLE_SHELLS = {"zsh", "-zsh", "bash", "-bash", "sh", "login", "ps"}


def busy_ttys(timeout: float = 2.0) -> set[str]:
    """Ttys whose foreground process group is running something other than
    the shell itself -- one 'ps -e' call. The '+' flag in STAT marks the
    foreground group; a tty we can't read is treated as free."""
    try:
        out = subprocess.run(
            ["ps", "-e", "-o", "tty=,stat=,comm="],
            capture_output=True, text=True, timeout=timeout,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return set()
    busy: set[str] = set()
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        tty_raw, stat, command = parts
        if tty_raw == "??" or "+" not in stat:
            continue
        name = Path(command.split()[0]).name if command.strip() else ""
        if name in _IDLE_SHELLS:
            continue
        busy.add(tty_raw if tty_raw.startswith("/dev/") else f"/dev/{tty_raw}")
    return busy


def _cwds_for_pids(pids: list[int], timeout: float = 2.0) -> dict[int, str]:
    """One 'lsof' call for the cwd of several pids at once. A pid lsof
    can't see (permission, already gone) is simply absent -- never raises,
    never blocks more than `timeout`."""
    pids = sorted({p for p in pids if p})
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-Fn"],
            capture_output=True, text=True, timeout=timeout,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    result: dict[int, str] = {}
    current_pid: int | None = None
    for line in out.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                current_pid = int(value)
            except ValueError:
                current_pid = None
        elif tag == "n" and current_pid is not None:
            result[current_pid] = value
    return result


def _window_number(windows: dict, window_id: str | None) -> int | None:
    info = windows.get(window_id) if window_id else None
    return getattr(info, "number", None) if info else None


def _shell_sessions(
    iterm_lister: iterm_mod.ItermLister,
    live_ttys: set[str],
    now: datetime,
    pid_by_tty: Callable[[], dict[str, int]],
    cwd_by_pids: Callable[[list[int]], dict[int, str]],
) -> list[Session]:
    """One synthetic Session per iTerm2 pane whose tty has no live Claude
    registry entry -- the left-join half of gather_sessions. session_id is
    stable across refreshes (`tty:<tty>`) since app.py uses it as the
    DataTable row key."""
    unmatched = [tab for tty, tab in iterm_lister.tabs().items() if tty not in live_ttys]
    windows = iterm_lister.windows()
    if not unmatched:
        return []
    tty_pids = pid_by_tty()
    pids = [tty_pids.get(tab.tty, 0) for tab in unmatched]
    cwds = cwd_by_pids(pids)

    sessions = []
    for tab in unmatched:
        pid = tty_pids.get(tab.tty, 0)
        sessions.append(
            Session(
                session_id=f"tty:{tab.tty}",
                pid=pid,
                cwd=getattr(tab, "path", None) or (cwds.get(pid, "") if pid else ""),
                name=tab.title,
                name_source="iterm",
                status="idle",
                status_updated_at=now,
                updated_at=now,
                started_at=now,
                is_shell=True,
                has_transcript=False,
                tty=tab.tty,
                window_id=getattr(tab, "window_id", None),
                tab_index=getattr(tab, "tab_index", None),
                window_number=_window_number(windows, getattr(tab, "window_id", None)),
                iterm_tab_id=tab.tab_id,
                iterm_title=tab.title,
            )
        )
    return sessions


def gather_sessions(
    state_store: StateStore,
    iterm_lister: iterm_mod.ItermLister,
    sessions_dir: Path | None = None,
    projects_dir: Path | None = None,
    status_snapshots: dict[str, dict] | None = None,
    pid_by_tty: Callable[[], dict[str, int]] = _all_pids_by_tty,
    cwd_by_pids: Callable[[list[int]], dict[int, str]] = _cwds_for_pids,
    accounts: list[Account] | None = None,
) -> list[Session]:
    """The main join: registry + transcript + podbay state + iTerm tab +
    status-line snapshot for every live session of every account, left-joined
    with every iTerm2 pane -- a pane with no matching registry session
    becomes a synthetic SHELL row (see _shell_sessions) so every open terminal
    shows up, Claude or not. `status_snapshots` lets a caller that already
    read them (e.g. to also find the newest one for the header) pass the dict
    in rather than have it re-read here; when omitted they're read fresh.
    `accounts` defaults to every config dir on the machine (accounts.discover);
    `sessions_dir`/`projects_dir` instead name one account's directories, for
    a caller that has only those."""
    if status_snapshots is None:
        status_snapshots = read_status_snapshots()
    if sessions_dir is not None or projects_dir is not None:
        registries = [(DEFAULT_LABEL, sessions_dir or SESSIONS_DIR, projects_dir or PROJECTS_DIR)]
    else:
        registries = [(a.label, a.sessions_dir, a.projects_dir) for a in (accounts or discover())]

    sessions: list[Session] = []
    entries = [(label, entry, projects) for label, registry, projects in registries for entry in read_registry(registry)]
    pids = [int(e["pid"]) for _label, e, _projects in entries]
    tabs_by_pid = iterm_lister.tabs_for_pids(pids)

    for account_label, entry, projects_dir in entries:
        session_id = entry["sessionId"]
        pid = int(entry["pid"])
        cwd = entry.get("cwd", "")

        transcript = {}
        main_last_turn_ts = None
        turn_ended = None
        waiting_on = None
        path = transcript_path_for(cwd, session_id, projects_dir)
        if path is not None:
            transcript = tail_read_transcript(path)
            main_last_turn_ts = transcript.get("last_turn_ts")
            newest_assistant = transcript.get("newest_assistant")
            turn_ended = True if transcript.get("interrupted") else (
                newest_assistant.get("stop_reason") == "end_turn" if newest_assistant is not None else None)
            idle_minutes = (
                round((datetime.now() - main_last_turn_ts).total_seconds() / 60, 1)
                if main_last_turn_ts is not None else None
            )
            subagents_running = count_running_subagents(path.parent, session_id, main_last_turn_ts)
            waiting_on = compute_waiting_on(transcript, entry.get("status", "idle"), idle_minutes, subagents_running)
            if transcript.get("last_turn") != "in_progress":
                sub = subagent_activity(path, session_id, transcript.get("last_turn_ts"))
                if sub is not None:
                    transcript["last_turn"], transcript["last_turn_ts"] = sub

        saved = _auto_unpark(state_store, session_id, transcript.get("last_turn_ts"))
        tab = tabs_by_pid.get(pid)
        if tab is not None and tab.selected:
            saved = _mark_seen_if_unread(state_store, session_id, transcript)
        snapshot = status_snapshots.get(session_id)

        sessions.append(
            Session(
                session_id=session_id,
                pid=pid,
                cwd=cwd,
                account=account_label,
                remote_session_id=entry.get("bridgeSessionId") or None,
                name=entry.get("name", ""),
                name_source=entry.get("nameSource", ""),
                status=entry.get("status", "idle"),
                status_updated_at=_ms_to_dt(entry.get("statusUpdatedAt")) or datetime.now(),
                updated_at=_ms_to_dt(entry.get("updatedAt")) or datetime.now(),
                started_at=_ms_to_dt(entry.get("startedAt")) or datetime.now(),
                git_branch=transcript.get("git_branch"),
                recap=transcript.get("recap"),
                recap_ts=transcript.get("recap_ts"),
                last_prompt=transcript.get("last_prompt"),
                last_turn=transcript.get("last_turn"),
                last_turn_ts=transcript.get("last_turn_ts"),
                tab_busy=tab.busy if tab else None,
                context_pct=snapshot.get("context_pct") if snapshot else None,
                model=(snapshot.get("model") if snapshot else None) or model_name_from_id(transcript.get("model_id")),
                effort=snapshot.get("effort") if snapshot else None,
                note=saved.note,
                parked_until=saved.parked_until,
                seen_at=saved.seen_at,
                has_transcript=path is not None,
                repos_touched=sorted(transcript.get("repos_touched", set())),
                repos_edited=sorted(transcript.get("repos_edited", set())),
                background_tasks=list(transcript.get("background_tasks", {}).values()),
                waiting_on=waiting_on,
                turn_ended=turn_ended,
                iterm_tab_id=tab.tab_id if tab else None,
                tty=tab.tty if tab else None,
                window_id=tab.window_id if tab else None,
                tab_index=tab.tab_index if tab else None,
                window_number=_window_number(iterm_lister.windows(), tab.window_id if tab else None),
                iterm_title=tab.title if tab else None,
            )
        )

    live_ttys = {tab.tty for tab in tabs_by_pid.values() if tab is not None}
    sessions.extend(_shell_sessions(iterm_lister, live_ttys, datetime.now(), pid_by_tty, cwd_by_pids))
    return sessions
