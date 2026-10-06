"""Podbay's own deterministic session inventory: the JSON payload, table and
status views another agent reads instead of the TUI. Built entirely from
Session objects gather_sessions() already produces -- no direct file reads
here."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import machine, voice
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
        # What it runs on now, from the status line; and what `podbay open
        # --model` asked for at launch (None: the account's default).
        "model": s.model,
        "opened_model": s.opened_model,
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


# The last scan that finished, kept so an inventory asked for while the
# machine is starved answers at once from it, marked stale, instead of
# waiting minutes on iTerm2 (2026-10-06: 120 s and no answer).
SNAPSHOT_PATH = machine.STATE_DIR / "inventory.json"
BUDGET_SECONDS = 20.0
MACHINE_BUDGET_SECONDS = 6.0


def save_snapshot(payload: dict, path: Path | None = None) -> None:
    path = path or SNAPSHOT_PATH
    tmp = path.with_name(f"{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, default=str))
        os.replace(tmp, path)
    except OSError:
        pass


def load_snapshot(path: Path | None = None) -> dict | None:
    try:
        data = json.loads((path or SNAPSHOT_PATH).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("sessions"), list) else None


def apply_exclude(payload: dict, exclude: set[str]) -> dict:
    """`payload` without the sessions in `exclude` (matched like
    inventory_payload does), its waiting list and repo groups cut to match."""
    if not exclude:
        return dict(payload)
    sessions = [
        s for s in payload["sessions"]
        if s.get("session_id") not in exclude and s.get("short_id") not in exclude and s.get("name") not in exclude
    ]
    names = {s["name"] for s in sessions}
    groups = [{**g, "sessions": [n for n in g["sessions"] if n in names]} for g in payload.get("repo_groups", [])]
    return {
        **payload,
        "sessions": sessions,
        "repo_groups": [g for g in groups if len(g["sessions"]) >= 2],
        "waiting": [n for n in payload.get("waiting", []) if n in names],
    }


def _within(fn: Callable, budget: float):
    """(finished, value, error): `fn` run on a daemon thread that is left
    behind when it does not finish within `budget` seconds."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 -- any failure is an answer too
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(budget)
    return "value" in box, box.get("value"), box.get("error")


def build_payload(
    gather: Callable[[], list[Session]], exclude: set[str],
    budget: float = BUDGET_SECONDS, path: Path | None = None,
    machine_budget: float = MACHINE_BUDGET_SECONDS,
) -> dict:
    """The inventory, live when the scan finishes within `budget` seconds
    (and then saved as the snapshot), otherwise the last saved one with
    "stale": true, how old it is and why. The machine's health is in it
    either way, under "machine"."""
    sample_done, sample, _ = _within(machine.snapshot, machine_budget)
    health = sample if sample_done and sample else {}
    done, sessions, error = _within(gather, budget)
    if done:
        full = inventory_payload(sessions, set())
        save_snapshot(full, path)
        payload = apply_exclude(full, exclude)
        payload["stale"] = False
    else:
        reason = machine.overload_reason(health) or (f"scan failed: {error}" if error else None)
        snapshot = load_snapshot(path)
        if snapshot is not None:
            payload = apply_exclude(snapshot, exclude)
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot["generated_at"])).total_seconds()
            except (KeyError, ValueError, TypeError):
                age = None
            payload["stale_age_seconds"] = round(age) if age is not None else None
            payload["stale_note"] = voice.inventory_stale(age if age is not None else 0, reason)
        else:
            payload = {
                "generated_at": datetime.now(timezone.utc).isoformat(), "self": None,
                "sessions": [], "repo_groups": [], "waiting": [], "stale_age_seconds": None,
                "stale_note": voice.inventory_unavailable(reason),
            }
        payload["stale"] = True
        payload["stale_reason"] = reason
    payload["machine"] = health
    return payload


def machine_lines(payload: dict) -> list[str]:
    """The machine as plain lines, and the stale note first when there is
    one: what the table and status views print under or above the sessions."""
    lines = [payload["stale_note"]] if payload.get("stale") and payload.get("stale_note") else []
    health = payload.get("machine") or {}
    if health:
        lines.append("machine: " + voice.machine_line(health))
        for label, key in (("cpu", "cpu"), ("memory", "mem")):
            line = voice.top_line(label, (health.get("top") or {}).get(key, []), key)
            if line:
                lines.append(line)
    return lines


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
    lines += machine_lines(payload)
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

    if payload.get("stale") and payload.get("stale_note"):
        lines.append(payload["stale_note"])
    elif (payload.get("machine") or {}).get("overloaded"):
        lines.append(f"machine is overloaded ({payload['machine']['overloaded']})")

    return "\n".join(lines)
