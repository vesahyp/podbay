"""The machine's own health: load, CPU, memory, pressure, swap and the
heaviest processes. podbay breaks when the Mac is starved (2026-10-06: load
average 112 with 300 MB free, and every iTerm2 call timed out), so the
screen, `podbay inventory --json` and the board show it, `podbay open`
refuses to start a session under critical memory pressure, and the error
lines name the overload when it is the cause.

Every read here is one short command with a timeout. A command that fails
leaves its figure out (None); nothing in this module raises or waits long.
A history of one sample a minute lives in its own file, so the chart on the
board and the screen outlives the process that took the samples.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

STATE_DIR = Path.home() / ".local" / "state" / "podbay"
HISTORY_PATH = STATE_DIR / "machine.jsonl"
HISTORY_HOURS = 12
SAMPLE_EVERY = 60  # seconds between two history lines
COMMAND_TIMEOUT = 3.0
TOP_N = 3

# kern.memorystatus_vm_pressure_level
PRESSURE_NAMES = {1: "normal", 2: "warn", 4: "critical"}

_SWAP_RE = re.compile(r"used = ([\d.]+)M")


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def parse_vm_stat(out: str) -> int | None:
    """Free memory in MB: free plus speculative pages, which the kernel
    hands out as free."""
    size = re.search(r"page size of (\d+) bytes", out)
    pages = 0
    seen = False
    for key in ("Pages free", "Pages speculative"):
        m = re.search(rf"{key}:\s+(\d+)", out)
        if m:
            pages += int(m.group(1))
            seen = True
    if not seen or not size:
        return None
    return round(pages * int(size.group(1)) / 1_048_576)


def parse_swap(out: str) -> int | None:
    """Swap in use in MB, from `sysctl -n vm.swapusage`."""
    m = _SWAP_RE.search(out)
    return round(float(m.group(1))) if m else None


def parse_processes(out: str) -> list[dict]:
    """`ps -Ao pid=,pcpu=,rss=,comm=` as {pid, name, cpu, mem_mb}. The name
    is the command's last path component."""
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) != 4:
            continue
        try:
            pid, cpu, rss = int(parts[0]), float(parts[1]), int(parts[2])
        except ValueError:
            continue
        procs.append({"pid": pid, "name": os.path.basename(parts[3]) or parts[3], "cpu": cpu, "mem_mb": round(rss / 1024)})
    return procs


def top_processes(procs: list[dict], n: int = TOP_N) -> dict[str, list[dict]]:
    return {
        "cpu": sorted(procs, key=lambda p: -p["cpu"])[:n],
        "mem": sorted(procs, key=lambda p: -p["mem_mb"])[:n],
    }


def pressure_level() -> str | None:
    out = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    return PRESSURE_NAMES.get(int(out)) if out.isdigit() else None


def sample() -> dict:
    """One reading of the machine now. Figures a command did not deliver are
    None. cpu_pct is the sum of the per-process CPU shares over the cores,
    capped at 100."""
    try:
        load = os.getloadavg()
    except OSError:
        load = (None, None, None)
    cores = os.cpu_count() or 1
    procs = parse_processes(_run(["ps", "-Ao", "pid=,pcpu=,rss=,comm="]))
    cpu = min(100.0, sum(p["cpu"] for p in procs) / cores) if procs else None
    total = _run(["sysctl", "-n", "hw.memsize"]).strip()
    return {
        "at": time.time(),
        "load": {"1m": load[0], "5m": load[1], "15m": load[2]},
        "cores": cores,
        "cpu_pct": round(cpu, 1) if cpu is not None else None,
        "mem_free_mb": parse_vm_stat(_run(["vm_stat"])),
        "mem_total_mb": round(int(total) / 1_048_576) if total.isdigit() else None,
        "pressure": pressure_level(),
        "swap_used_mb": parse_swap(_run(["sysctl", "-n", "vm.swapusage"])),
        "top": top_processes(procs),
    }


# Eight cores at load 16 have twice as many runnable tasks as cores: every
# AppleScript call to iTerm2 then waits in a queue.
LOAD_FACTOR = 2.0
LOW_FREE_MB = 400


def overload_reason(s: dict | None) -> str | None:
    """Why the machine is too starved to answer in time, in a few words; None
    when it is not."""
    if not s:
        return None
    if s.get("pressure") == "critical":
        free = s.get("mem_free_mb")
        return f"memory pressure is critical{f', {free} MB free' if free is not None else ''}"
    load = (s.get("load") or {}).get("1m")
    cores = s.get("cores") or 1
    if load is not None and load >= LOAD_FACTOR * cores:
        return f"load average {load:.0f} on {cores} cores"
    free = s.get("mem_free_mb")
    if free is not None and free < LOW_FREE_MB and (s.get("swap_used_mb") or 0) > 0:
        return f"only {free} MB of memory free"
    return None


def overloaded_now() -> str | None:
    """overload_reason for a quick fresh look: the load average alone, plus
    the pressure level. Cheap enough for an error path."""
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = None
    return overload_reason({"load": {"1m": load}, "cores": os.cpu_count() or 1, "pressure": pressure_level()})


def memory_critical() -> dict | None:
    """The sample when memory pressure is critical, else None."""
    level = pressure_level()
    if level != "critical":
        return None
    return {"pressure": level, "mem_free_mb": parse_vm_stat(_run(["vm_stat"]))}


# ---- history -----------------------------------------------------------------


def _slim(s: dict) -> dict:
    """The figures the chart needs, one short line per sample."""
    return {
        "at": s["at"], "load": (s.get("load") or {}).get("1m"), "cpu": s.get("cpu_pct"),
        "free": s.get("mem_free_mb"), "swap": s.get("swap_used_mb"), "p": s.get("pressure"),
    }


def history(hours: float = HISTORY_HOURS, path: Path | None = None, now: float | None = None) -> list[dict]:
    path = path or HISTORY_PATH
    cutoff = (now or time.time()) - hours * 3600
    points = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            point = json.loads(line)
        except ValueError:
            continue
        if isinstance(point, dict) and isinstance(point.get("at"), (int, float)) and point["at"] >= cutoff:
            points.append(point)
    return points


def record(s: dict, path: Path | None = None) -> bool:
    """Append `s` to the history unless a sample is under SAMPLE_EVERY old;
    the file is cut back to HISTORY_HOURS now and then. True when written."""
    path = path or HISTORY_PATH
    try:
        if time.time() - path.stat().st_mtime < SAMPLE_EVERY:
            return False
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = history(HISTORY_HOURS, path)
        keep.append(_slim(s))
        tmp = path.with_name(f"{path.name}.{os.getpid()}")
        tmp.write_text("".join(json.dumps(p) + "\n" for p in keep))
        os.replace(tmp, path)
    except OSError:
        log.warning("machine history not written", exc_info=True)
        return False
    return True


SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float | None], width: int = 24, top: float | None = None) -> str:
    """`values` as block characters, newest on the right, the last `width`
    of them; a gap is a space. Scaled to `top` (or the series' own maximum)."""
    values = values[-width:]
    known = [v for v in values if v is not None]
    if not known:
        return ""
    peak = top if top else max(known)
    if peak <= 0:
        peak = 1.0
    return "".join(
        " " if v is None else SPARK[min(len(SPARK) - 1, max(0, int(v / peak * (len(SPARK) - 1) + 0.5)))]
        for v in values
    )


def snapshot() -> dict:
    """A sample with its history, what `inventory --json` and the board carry.
    Records the sample as a side effect, so the chart grows whenever
    anything asks."""
    s = sample()
    record(s)
    s["history"] = history()
    reason = overload_reason(s)
    s["overloaded"] = reason
    return s
