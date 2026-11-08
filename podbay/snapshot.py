"""`podbay snapshot`: the state the phone app shows, as one JSON object
(docs/adr/0001-podbay-mobile.md). The podbay screen writes it every minute
and uploads it when it changed (mobile.py); the API hands it to the app.

    board     the board's lines by section (board.collect), each with a
              stable id, plus a one-line summary
    machine   power, Docker, load, memory, disk now and over 24 hours at
              10 minute steps, the user's launchd agents
    accounts  each Claude account: login end, the 5 hour and 7 day windows,
              and which account a new session should use
    nightly   every cron job wrapped by a `cronrun <label>` line in the
              crontab: one cell per night for the last 7 nights, with the
              run's exit code, length and log tail
    runs      clavesa pipeline minutes per night and workspace, when a
              runs.json URL is configured
    lines     every id the app can reply to, with the target and quote the
              API puts in front of the text; the API never takes these from
              the phone
    repos     the repos a new session can start in

Everything here is pure except the small readers at the bottom (power,
Docker, launchd, crontab, runs.json), which return None or empty when a
command is missing, so a machine without them still gets a snapshot.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from . import board, machine

VERSION = 1
NIGHTS = 7
STEP_SECONDS = 600  # the 24 hour charts: one point per 10 minutes
LOG_LINES = 40
QUOTE_CHARS = 160

# Shapes of secrets that a log line can print by mistake. Each match is
# replaced by [removed] before the snapshot leaves the Mac.
_SECRET_RES = [
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S+"),
    # Long runs of key characters. "/" is left out so a file path survives.
    re.compile(r"[A-Za-z0-9+=_-]{40,}"),
]


def scrub(line: str) -> str:
    """One log line with anything shaped like a key or token removed."""
    for pattern in _SECRET_RES:
        if pattern.groups >= 2:
            line = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[removed]", line)
        else:
            line = pattern.sub("[removed]", line)
    return line


def _quote(text: str | None) -> str:
    return board.clean_line(text, QUOTE_CHARS) if text else ""


# --- board -------------------------------------------------------------------


def board_part(payload: dict, now: datetime, headlines: dict[str, dict]) -> tuple[dict, dict]:
    """The board's sections for the app, and the lines map entries for them."""
    found = board.collect(payload, now, headlines)
    lines: dict[str, dict] = {}
    sections: dict[str, list[dict]] = {}
    for kind, items in found.items():
        out = []
        for item in items:
            line_id = str(item["id"])
            if not line_id:
                continue
            out.append({
                "id": line_id, "project": item["project"], "target": item["target"], "text": item["text"],
                "link": item.get("link"), "steps": [str(s) for s in item.get("steps") or [] if str(s).strip()],
                "when": item.get("when") or "", "options": item.get("options") or [],
            })
            lines[line_id] = {"target": item["target"], "quote": _quote(item["text"])}
        sections[kind] = out
    return sections, lines


# --- machine -----------------------------------------------------------------


def downsample(points: list[dict], now: float, hours: int = 24, step: int = STEP_SECONDS) -> list[dict]:
    """The history at one point per `step`: the last sample in each step,
    oldest first. Steps with no sample are left out."""
    start = now - hours * 3600
    buckets: dict[int, dict] = {}
    for p in points:
        at = p.get("at")
        if not isinstance(at, (int, float)) or at < start:
            continue
        buckets[int((at - start) // step)] = p
    return [
        {"at": round(p["at"]), "load": p.get("load"), "free": p.get("free"), "disk": p.get("disk")}
        for _, p in sorted(buckets.items())
    ]


def machine_part(health: dict | None, power: dict | None, docker_up: bool | None, agents: list[dict], now: float) -> dict:
    health = health or {}
    cores = health.get("cores") or os.cpu_count() or 1
    return {
        "load": (health.get("load") or {}).get("1m"),
        "cores": cores,
        "overload_at": cores * machine.LOAD_FACTOR,
        "pressure": health.get("pressure"),
        "mem_free_mb": health.get("mem_free_mb"),
        "mem_low_mb": machine.LOW_FREE_MB,
        "disk_free_gb": health.get("disk_free_gb"),
        "disk_used_pct": health.get("disk_used_pct"),
        "disk_low_gb": machine.DISK_LOW_GIB,
        "overloaded": health.get("overloaded"),
        "power": power,
        "docker_up": docker_up,
        "agents": agents,
        "history": downsample(health.get("history") or [], now),
    }


# --- accounts ----------------------------------------------------------------


def accounts_part(quota_payload: dict | None) -> list[dict]:
    out = []
    data = quota_payload or {}
    heavy = ((data.get("suggested") or {}).get("heavy") or {}).get("account")
    for a in data.get("accounts") or []:
        windows = []
        for key, name in (("five_hour", "5 hours"), ("seven_day", "Week")):
            w = a.get(key)
            if w:
                windows.append({"name": name, "pct": w.get("used_pct"), "resets_at": w.get("resets_at")})
        for m in a.get("models") or []:
            if isinstance(m, dict) and m.get("used_pct") is not None:
                windows.append({"name": f"Week, {m.get('model') or m.get('name') or 'model'}",
                                "pct": m.get("used_pct"), "resets_at": m.get("resets_at")})
        out.append({
            "account": a.get("account"), "logged_out": bool(a.get("logged_out")),
            "login_ends_at": a.get("login_ends_at"), "windows": windows,
            "suggested": a.get("account") == heavy,
        })
    return out


# --- nightly -----------------------------------------------------------------

_CRONRUN_RE = re.compile(r"\S*/cronrun\s+(\S+)")


def _field_matches(field: str, value: int, low: int, high: int) -> bool:
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, raw = part.split("/", 1)
            step = int(raw) if raw.isdigit() else 1
        if part == "*":
            lo, hi = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(part)
        if lo <= value <= hi and (value - lo) % step == 0:
            return True
    return False


def parse_crontab(text: str) -> list[dict]:
    """The jobs that run through cronrun: label, the time of day as HH:MM,
    and the day fields, from crontab text. Other lines are left out."""
    jobs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        fields = line.split(None, 5)
        if len(fields) < 6:
            continue
        match = _CRONRUN_RE.search(fields[5])
        if not match or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        jobs.append({"label": match.group(1), "minute": int(fields[0]), "hour": int(fields[1]),
                     "dom": fields[2], "month": fields[3], "dow": fields[4]})
    return jobs


def scheduled_on(job: dict, day: date) -> bool:
    cron_dow = (day.weekday() + 1) % 7  # cron: 0 is Sunday
    dow_ok = _field_matches(job["dow"], cron_dow, 0, 7) or (cron_dow == 0 and _field_matches(job["dow"], 7, 0, 7))
    return (_field_matches(job["dom"], day.day, 1, 31) and _field_matches(job["month"], day.month, 1, 12) and dow_ok)


def _read_runs(cron_dir: Path, label: str) -> list[dict]:
    """The job's runs: the .jsonl history, plus the last-run .json when the
    history does not have it yet (a job that has not run since cronrun
    started keeping history)."""
    runs: list[dict] = []
    try:
        for line in (cron_dir / f"{label}.jsonl").read_text().splitlines():
            try:
                runs.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    try:
        last = json.loads((cron_dir / f"{label}.json").read_text())
        if isinstance(last, dict) and not any(r.get("started") == last.get("started") for r in runs):
            runs.append(last)
    except (OSError, ValueError):
        pass
    return runs


def _run_day(run: dict) -> date | None:
    try:
        return datetime.strptime(str(run.get("started"))[:19], "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        return None


def run_state(run: dict) -> str:
    if int(run.get("rc") or 0) != 0:
        return "fail"
    return "skip" if "SKIP" in str(run.get("status") or "").upper() else "ok"


def nightly_part(crontab_text: str, cron_dir: Path | None, now: datetime) -> tuple[dict, dict]:
    """One row per cronrun job, one cell per night for the last NIGHTS days
    (today last): ok, fail, skip, miss (scheduled and its time passed with
    no run), pending (later today), none (not scheduled that day) or
    unknown (before the job's first run on record)."""
    days = [(now - timedelta(days=NIGHTS - 1 - i)).date() for i in range(NIGHTS)]
    jobs = []
    lines: dict[str, dict] = {}
    for job in sorted(parse_crontab(crontab_text), key=lambda j: (j["hour"], j["minute"], j["label"])):
        runs = _read_runs(cron_dir, job["label"]) if cron_dir else []
        by_day: dict[date, dict] = {}
        for run in runs:
            d = _run_day(run)
            if d is not None:
                by_day[d] = run  # the last run of a day wins
        # Before the first run on record nothing is known: the history may
        # simply not reach back that far.
        known_from = min(by_day) if by_day else None
        cells = []
        for d in days:
            run = by_day.get(d)
            due = datetime.combine(d, datetime.min.time()).replace(hour=job["hour"], minute=job["minute"])
            if run is None and scheduled_on(job, d) and (known_from is None or d < known_from) and due <= now:
                cell = {"state": "unknown"}
            elif run is not None:
                tail = run.get("log_tail") or ([run["tail"]] if run.get("tail") else [])
                cell = {"state": run_state(run), "rc": int(run.get("rc") or 0), "started": run.get("started"),
                        "elapsed": run.get("elapsed_seconds"), "status": run.get("status") or "",
                        "log": [scrub(str(x)) for x in tail[-LOG_LINES:]]}
            elif not scheduled_on(job, d):
                cell = {"state": "none"}
            elif due > now:
                cell = {"state": "pending"}
            else:
                cell = {"state": "miss"}
            cell["day"] = d.isoformat()
            cells.append(cell)
            if cell["state"] in ("ok", "fail", "skip", "miss"):
                word = {"ok": "passed", "fail": f"failed, exit {cell.get('rc')}", "skip": "skipped", "miss": "did not run"}[cell["state"]]
                lines[f"nightly:{job['label']}:{d.isoformat()}"] = {
                    "target": "nightly", "quote": f"{job['label']} on {d:%a %-d %b}: {word}"}
        names = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        dow = "" if job["dow"] == "*" else f"{names[int(job['dow'])] if job['dow'].isdigit() and int(job['dow']) < 8 else job['dow']} "
        jobs.append({"label": job["label"], "at": f"{dow}{job['hour']:02d}:{job['minute']:02d}", "cells": cells})
    return {"days": [d.isoformat() for d in days], "jobs": jobs}, lines


# --- clavesa runs ------------------------------------------------------------


def runs_page(runs_url: str | None) -> str | None:
    """The chart page beside a published runs.json: .../data/runs.json is
    read by .../runs.html."""
    if runs_url and runs_url.endswith("/data/runs.json"):
        return runs_url[: -len("data/runs.json")] + "runs.html"
    return None


def runs_part(runs_json: dict | None, now: datetime, page: str | None = None) -> dict | None:
    """Pipeline minutes per night and workspace for the last NIGHTS days,
    from the runs.json `make runs-publish` exports. Workspaces outside the
    five largest go into "other"."""
    if not runs_json:
        return None
    days = [(now - timedelta(days=NIGHTS - 1 - i)).date().isoformat() for i in range(NIGHTS)]
    totals: dict[str, dict[str, float]] = {}
    for r in runs_json.get("runs") or []:
        day = str(r.get("started_at") or "")[:10]
        if day not in days or not r.get("total_ms"):
            continue
        ws = str(r.get("workspace") or "?")
        totals.setdefault(ws, {}).setdefault(day, 0.0)
        totals[ws][day] += r["total_ms"] / 60000
    order = sorted(totals, key=lambda w: -sum(totals[w].values()))
    keep, rest = order[:5], order[5:]
    series = [{"name": w, "minutes": [round(totals[w].get(d, 0.0), 1) for d in days]} for w in keep]
    if rest:
        series.append({"name": "other", "minutes": [round(sum(totals[w].get(d, 0.0) for w in rest), 1) for d in days]})
    return {"days": days, "series": series, "generated_at": runs_json.get("generated_at"), "page": page}


# --- the whole object --------------------------------------------------------


def build(
    *,
    payload: dict,
    headlines: dict[str, dict],
    quota_payload: dict | None,
    health: dict | None,
    power: dict | None,
    docker_up: bool | None,
    agents: list[dict],
    crontab_text: str,
    cron_dir: Path | None,
    runs_json: dict | None,
    repos: list[str] | None = None,
    runs_url: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    sections, lines = board_part(payload, now, headlines)
    nightly, nightly_lines = nightly_part(crontab_text, cron_dir, now)
    lines.update(nightly_lines)
    lines["machine"] = {"target": "machine", "quote": "the Mac"}
    repos = sorted(set(repos or []) | {str(s.get("work_repo") or s.get("repo")) for s in payload.get("sessions") or []
                                       if s.get("work_repo") or s.get("repo")})
    decisions = len(sections.get("decision", []))
    working = len(sections.get("progress", []))
    summary = " ".join(x for x in (
        ("1 decision waits for you." if decisions == 1 else f"{decisions} decisions wait for you.") if decisions else "Nothing waits on you.",
        (f"{working} in progress." if working else ""),
    ) if x)
    return {
        "v": VERSION,
        "generated_at": round(now.timestamp()),
        "mac": payload.get("mac") or "",
        "board": {"sections": sections, "summary": summary},
        "machine": machine_part(health, power, docker_up, agents, now.timestamp()),
        "accounts": accounts_part(quota_payload),
        "nightly": nightly,
        "runs": runs_part(runs_json, now, runs_page(runs_url)),
        "lines": lines,
        "repos": repos,
    }


def fingerprint(state: dict) -> str:
    """What decides whether the snapshot changed: everything except the
    time it was made."""
    return json.dumps({k: v for k, v in state.items() if k != "generated_at"}, sort_keys=True, default=str)


# --- readers -----------------------------------------------------------------


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


_BATT_RE = re.compile(r"(\d+)%;\s*([^;]+);")


def parse_power(out: str) -> dict | None:
    """`pmset -g batt`: the power source and, on a laptop, the charge."""
    if not out.strip():
        return None
    source = "cable" if "AC Power" in out else "battery" if "Battery Power" in out else None
    match = _BATT_RE.search(out)
    return {"source": source, "percent": int(match.group(1)) if match else None,
            "state": match.group(2).strip() if match else None}


def read_power() -> dict | None:
    return parse_power(_run(["pmset", "-g", "batt"]))


def read_docker() -> bool | None:
    """True when the Docker daemon answers, False when it does not, None
    when there is no docker command."""
    for path in ("/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
        if os.path.exists(path):
            try:
                return subprocess.run([path, "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                                      timeout=6).returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False
    return None


def read_agents(directory: Path | None = None) -> list[dict]:
    """The user's own launchd agents (~/Library/LaunchAgents): label,
    whether a process runs now, and the last exit status."""
    directory = directory or Path.home() / "Library" / "LaunchAgents"
    labels = []
    for plist in sorted(directory.glob("*.plist")):
        try:
            label = plistlib.loads(plist.read_bytes()).get("Label")
        except Exception:  # noqa: BLE001 -- one bad plist must not hide the rest
            continue
        if label:
            labels.append(label)
    listing: dict[str, tuple[str, str]] = {}
    for line in _run(["launchctl", "list"]).splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            listing[parts[2]] = (parts[0], parts[1])
    out = []
    for label in labels:
        pid, status = listing.get(label, ("", ""))
        out.append({"label": label.rsplit(".", 1)[-1], "loaded": label in listing,
                    "running": pid not in ("", "-"), "last_exit": int(status) if status.lstrip("-").isdigit() else None})
    return out


def read_repos(parent: Path) -> list[str]:
    """The git repos in `parent` (sources.REPOS_DIR): where a new session
    can start."""
    try:
        return sorted(p.name for p in parent.iterdir() if (p / ".git").exists() and not p.name.startswith("."))
    except OSError:
        return []


def read_crontab() -> str:
    return _run(["crontab", "-l"])


_runs_cache: dict = {"at": 0.0, "data": None}


def read_runs_json(url: str | None, max_age: float = 3600) -> dict | None:
    """runs.json from `url`, fetched at most once an hour."""
    if not url:
        return None
    if time.time() - _runs_cache["at"] < max_age:
        return _runs_cache["data"]
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 -- a configured https URL
            _runs_cache["data"] = json.loads(resp.read())
    except (OSError, ValueError):
        pass
    _runs_cache["at"] = time.time()
    return _runs_cache["data"]
