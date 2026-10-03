"""Podbay's own deterministic session inventory: the JSON payload, table and
status views another agent reads instead of the TUI. Built entirely from
Session objects gather_sessions() already produces -- no direct file reads
here."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .model import HOME_BASE, Session, is_head_jeeves, repo_groups
from .sources import REPOS_DIR

_WS_RE = re.compile(r"\s+")


def _collapse(text: str, limit: int) -> str:
    return _WS_RE.sub(" ", text).strip()[:limit]


def repo_for_cwd(cwd: str) -> str:
    if not cwd:
        return ""
    p = Path(cwd)
    try:
        rel = p.relative_to(REPOS_DIR)
        return rel.parts[0] if rel.parts else p.name
    except ValueError:
        return p.name


def work_repo(s: Session) -> str:
    for repos in (s.repos_edited, s.repos_touched):
        others = sorted(r for r in repos if r != HOME_BASE)
        if others:
            return others[0]
    return repo_for_cwd(s.cwd)


def _session_dict(s: Session, now: datetime) -> dict:
    # One clock for state and age: activity_at counts the subagents too.
    idle_minutes = round(s.age_seconds(now) / 60, 1) if s.has_transcript else None
    return {
        "name": s.name,
        "state": s.derive_status(now),
        "age_seconds": s.age_seconds(now),
        "parked_until": s.parked_until.isoformat() if s.parked_until else None,
        # What the session is about: the tab title Claude Code sets from the
        # first prompt. The handle to use when talking about it.
        "title": s.title,
        "head_jeeves": is_head_jeeves(s),
        # Who started it with `podbay open`: "head-jeeves" marks the ones
        # Head Jeeves closes when they are done.
        "opened_by": s.opened_by,
        "account": s.account,
        "remote_url": s.remote_url,
        "tab": s.terminal,
        "short_id": s.session_id[:6],
        "session_id": s.session_id,
        "pid": s.pid,
        "cwd": s.cwd,
        "repo": repo_for_cwd(s.cwd),
        # The repo the session works in: edited first, else touched, the home
        # base only when nothing else. What the board shows.
        "work_repo": work_repo(s),
        "repos_touched": sorted(s.repos_touched),
        "repos_edited": sorted(s.repos_edited),
        "git_branch": s.git_branch,
        "registry_status": s.status,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "last_activity_at": s.activity_at.isoformat() if s.has_transcript else None,
        "idle_minutes": idle_minutes,
        "turn_ended": s.turn_ended,
        "last_text": _collapse(s.recap, 300) if s.recap else None,
        "waiting_on": s.waiting_on,
        "context_pct": s.context_pct,
        "model": s.model,
        "has_transcript": s.has_transcript,
    }


def inventory_payload(sessions: list[Session], exclude: set[str]) -> dict:
    """The deterministic inventory: registry+transcript join already done by
    gather_sessions(), filtered to real Claude sessions (shell panes carry no
    registry entry and are excluded), minus anything in `exclude` (matched by
    session id, short id, or name), sorted by repo then name."""
    now = datetime.now()
    kept = [
        s for s in sessions
        if not s.is_shell
        and s.session_id not in exclude
        and s.session_id[:6] not in exclude
        and s.name not in exclude
    ]
    kept.sort(key=lambda s: (repo_for_cwd(s.cwd), s.name))

    session_dicts = [_session_dict(s, now) for s in kept]
    groups = repo_groups(kept)
    waiting = [s["name"] for s in session_dicts if s["waiting_on"] is not None]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "self": None,
        "sessions": session_dicts,
        "repo_groups": groups,
        "waiting": waiting,
    }


def render_table(payload: dict) -> str:
    sessions = payload["sessions"]
    groups = payload["repo_groups"]
    headers = ["TAB", "TITLE", "NAME", "ACCT", "REPOS", "STATUS", "IDLE", "CTX%", "WAITING", "LAST"]
    rows = []
    for s in sessions:
        repos = [r + ("*" if r in s["repos_edited"] else "") for r in s["repos_touched"]]
        rows.append([
            f"#{s['tab']}" if s.get("tab") else "-",
            (s.get("title") or "-")[:40], s["name"] or s["short_id"], s.get("account") or "-", ",".join(repos) or "-", s["registry_status"],
            f"{s['idle_minutes']}m" if s["idle_minutes"] is not None else "-",
            f"{s['context_pct']:.0f}" if isinstance(s["context_pct"], (int, float)) else "-",
            s["waiting_on"]["kind"] if s["waiting_on"] else "-",
            (s["last_text"] or "-")[:60],
        ])
    widths = [max(len(h), *(len(str(r[i])) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines += ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) for row in rows]
    lines += [f"same repo: {g['repo']} -> {', '.join(g['sessions'])}" for g in groups]
    return "\n".join(lines)


def _local_hhmm(iso_ts: str | None) -> str:
    if not iso_ts:
        return "?"
    try:
        return datetime.fromisoformat(iso_ts).strftime("%H:%M")
    except ValueError:
        return "?"


def _repos_label(s: dict) -> list[str]:
    """The repos a session works in, the home base left out unless it is the
    only one."""
    repos = sorted(set(s["repos_touched"]) - {HOME_BASE})
    return repos or ([HOME_BASE] if HOME_BASE else ["-"])


def render_status(payload: dict) -> str:
    sessions = payload["sessions"]
    groups = payload["repo_groups"]
    lines: list[str] = []

    for s in sessions:
        if not s["waiting_on"]:
            continue
        repos = _repos_label(s)
        idle = s["idle_minutes"]
        idle_str = f"{idle:g}" if idle is not None else "?"
        detail = (s["waiting_on"].get("detail") or "")[:120]
        lines.append(
            f"{s['name']} · {','.join(repos)} · waiting on: {s['waiting_on']['kind']} {detail}"
            f" · since {_local_hhmm(s['last_activity_at'])} ({idle_str} min)"
        )

    for s in sessions:
        if s["waiting_on"] or not s["turn_ended"]:
            continue
        repos = _repos_label(s)
        last = (s["last_text"] or "")[:100]
        lines.append(f"{s['name']} · {','.join(repos)} · done: {last}")

    busy_names = [s["name"] for s in sessions if not s["waiting_on"] and s["turn_ended"] is not True]
    if busy_names:
        lines.append(f"busy: {', '.join(busy_names)}")

    for g in groups:
        lines.append(f"same repo: {g['repo']} ({', '.join(g['sessions'])})")

    return "\n".join(lines)
