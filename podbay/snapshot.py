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
    runs      clavesa pipeline run timings by phase: every pipeline per
              night for 30 nights, and the last 30 runs of each pipeline,
              from the runs.json file in `podbay config mobile-runs-file`
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
from datetime import date, datetime, timedelta
from pathlib import Path

from . import board, machine
from .model import SHELL, asks_user

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
            full = item.get("full") or ""
            out.append({
                "id": line_id, "project": item["project"], "target": item["target"], "text": item["text"],
                "link": item.get("link"), "steps": [str(s) for s in item.get("steps") or [] if str(s).strip()],
                "when": item.get("when") or "", "options": item.get("options") or [],
                "asks": bool(item.get("asks")),
                # The whole text, only when the folded text is shorter: the app unfolds on a tap.
                "full": full if full and full != item["text"] else None,
            })
            lines[line_id] = {"target": item["target"], "quote": _quote(item["text"])}
        sections[kind] = out
    return sections, lines


def sessions_part(payload: dict, sections: dict) -> list[dict]:
    """Every live session but Head Jeeves and plain shells, for the app's
    Machine tab: name, target, state, whether it asks the user something,
    and its board line when it has one. Asking sessions first."""
    on_board = {str(i["id"]) for items in sections.values() for i in items}
    out = []
    for s in payload.get("sessions") or []:
        if s.get("head_jeeves") or s.get("state") == SHELL:
            continue
        sid = str(s.get("session_id") or "")
        out.append({
            "name": str(s.get("title") or s.get("name") or ""),
            "target": board._target(s),
            "state": str(s.get("state") or ""),
            "asks": asks_user(s.get("waiting_on")),
            "line": sid if sid in on_board else None,
        })
    return sorted(out, key=lambda r: (not r["asks"], r["target"]))


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


# The phases of one run, in the order the app stacks them. "nodes" is the
# whole node time of a run made before clavesa v2.22.0, which has no
# per-node phases; "bookkeeping" is what the phases do not cover.
RUN_PHASES = ["startup", "metastore", "spark", "reads", "transform", "writes", "stats", "nodes", "bookkeeping"]
RUN_NIGHTS = 30  # the all-pipelines chart
RUN_KEEP = 30  # runs per pipeline chart
RUN_MEDIAN_OF = 7  # the runs before the latest that its median is taken from


def _num(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def run_phases(run: dict) -> list[float]:
    """Minutes per RUN_PHASES for one run of `clavesa pipeline timings`.
    Startup is the run's own setup steps; spark, reads, transform, writes
    and stats add up the nodes' own phases; bookkeeping is the rest of the
    total, never below zero."""
    p = run.get("phases") or {}
    nodes = [n for n in run.get("nodes") or [] if isinstance(n, dict)]
    timed = [n["timing"]["phases"] for n in nodes if isinstance(n.get("timing"), dict) and isinstance(n["timing"].get("phases"), dict)]

    def nodes_sum(key: str) -> float:
        return sum(_num(ph.get(key)) for ph in timed)

    v = dict.fromkeys(RUN_PHASES, 0.0)
    v["startup"] = sum(_num(p.get(k)) for k in ("startup", "prepare", "lock", "image", "container", "boot"))
    v["metastore"] = _num(p.get("metastore"))
    if timed:
        v["spark"] = nodes_sum("spark_session") + nodes_sum("catalog")
        v["reads"] = _num(p.get("inputs")) + nodes_sum("inputs")
        v["transform"] = nodes_sum("transform")
        v["writes"] = nodes_sum("write")
        v["stats"] = nodes_sum("stats")
    else:
        v["nodes"] = _num(p.get("nodes"))
    v["bookkeeping"] = max(0.0, _num(run.get("total_ms")) - sum(v.values()))
    return [round(v[k] / 60000, 2) for k in RUN_PHASES]


def runs_part(runs_json: dict | None) -> dict | None:
    """The clavesa run timings for the app's Nightly tab, from the
    runs.json the home repo's `runs-export` writes: every pipeline per
    night for RUN_NIGHTS nights ending at the latest run, and one chart per
    pipeline with its last RUN_KEEP runs, the latest run time and the
    median of the RUN_MEDIAN_OF runs before it. Minutes per phase in
    RUN_PHASES order."""
    if not runs_json:
        return None
    runs = sorted((r for r in runs_json.get("runs") or [] if isinstance(r, dict) and r.get("started_at")),
                  key=lambda r: str(r["started_at"]))
    if not runs:
        return None
    per_day: dict[str, dict] = {}
    groups: dict[str, list[dict]] = {}
    for r in runs:
        day = str(r["started_at"])[:10]
        v = run_phases(r)
        failed = r.get("status") != "SUCCEEDED"
        a = per_day.setdefault(day, {"v": [0.0] * len(RUN_PHASES), "n": 0, "f": 0, "pipes": set()})
        a["v"] = [x + y for x, y in zip(a["v"], v)]
        a["n"] += 1
        a["f"] += int(failed)
        a["pipes"].add(f"{r.get('workspace')}/{r.get('pipeline')}")
        groups.setdefault(f"{r.get('workspace') or '?'} · {r.get('pipeline') or '?'}", []).append(
            {"at": str(r["started_at"])[:16].replace("T", " "), "f": int(failed), "ms": int(_num(r.get("total_ms"))), "v": v})
    last = date.fromisoformat(str(runs[-1]["started_at"])[:10])
    nights = []
    for i in range(RUN_NIGHTS - 1, -1, -1):
        d = (last - timedelta(days=i)).isoformat()
        a = per_day.get(d)
        nights.append({"d": d, "n": a["n"], "f": a["f"], "p": len(a["pipes"]), "v": [round(x, 2) for x in a["v"]]} if a
                      else {"d": d, "n": 0, "f": 0, "p": 0, "v": []})
    pipelines = []
    for name, rs in groups.items():
        prev = [x["ms"] for x in rs[-RUN_MEDIAN_OF - 1:-1]]
        prev.sort()
        median = (prev[len(prev) // 2] if len(prev) % 2 else (prev[len(prev) // 2 - 1] + prev[len(prev) // 2]) / 2) if prev else None
        pipelines.append({"name": name, "latest_ms": rs[-1]["ms"], "median_ms": median, "median_of": len(prev),
                          "runs": [{k: x[k] for k in ("at", "f", "v")} for x in rs[-RUN_KEEP:]]})
    pipelines.sort(key=lambda g: -g["latest_ms"])
    return {"generated_at": runs_json.get("generated_at"), "phases": RUN_PHASES, "count": len(runs),
            "nights": nights, "pipelines": pipelines}


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
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    sections, lines = board_part(payload, now, headlines)
    nightly, nightly_lines = nightly_part(crontab_text, cron_dir, now)
    lines.update(nightly_lines)
    lines["machine"] = {"target": "machine", "quote": "the Mac"}
    # The Thread tab's chat: a message to Head Jeeves himself, about no line.
    lines["head-jeeves"] = {"target": "head-jeeves", "quote": ""}
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
        "sessions": sessions_part(payload, sections),
        "accounts": accounts_part(quota_payload),
        "nightly": nightly,
        "runs": runs_part(runs_json),
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


_runs_cache: dict = {"key": None, "data": None}


def read_runs_json(path: str | None) -> dict | None:
    """runs.json from the file at `path` (podbay config mobile-runs-file),
    read again only when its size or modification time changes. A missing
    or broken file gives None."""
    if not path:
        return None
    p = Path(path).expanduser()
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _runs_cache["key"] != key:
        try:
            _runs_cache["data"] = json.loads(p.read_text())
        except (OSError, ValueError):
            _runs_cache["data"] = None
        _runs_cache["key"] = key
    return _runs_cache["data"]
