"""The board screen in the TUI (key b): the same state.json the phone app
reads (snapshot.py), drawn as text, so the Mac and the phone always show
the same lines (docs/adr/0002-one-phone-app.md).

Sections, in the app's order: decisions, ready to test, finished today, in
progress, the machine (power, disk, load over 24 hours, memory, Docker,
each account's login and windows), the nightly jobs (7 cells a job, today
on the right), the clavesa run timings (minutes per night for 30 nights,
the last night by phase, one line per pipeline) and the thread with Head
Jeeves. Pure: render() takes the state and the messages and returns a
rich Text.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text

AMBER = "#ffb000"
RED = "#e0201f"
GREEN = "#6fcf86"
DIM = "#7a7060"
FG = "#d9c9a0"
BLUE = "#6aa8f0"

SPARK = "▁▂▃▄▅▆▇█"
CELL = {
    "ok": ("■", GREEN), "fail": ("■", RED), "skip": ("■", AMBER), "miss": ("□", RED),
    "pending": ("·", DIM), "none": (" ", DIM), "unknown": ("░", DIM),
}
PHASE_COLOR = {"startup": "#3987e5", "metastore": "#d95926", "spark": "#199e70", "reads": "#c98500",
               "transform": "#d55181", "writes": "#008300", "stats": "#9085e9", "nodes": "#4f5560",
               "bookkeeping": "#6b7078"}
MSG_STATE = {"queued": "queued", "delivering": "sending", "delivered": "delivered", "answered": "answered",
             "expired": "not sent"}


def _epoch(value) -> float | None:
    """Seconds since the epoch from a number or an ISO string."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return None


def _span(seconds: float) -> str:
    seconds = max(0, seconds)
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    if seconds < 86400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} d"


def spark(values: list[float], top: float | None = None) -> str:
    """One block character per value, scaled to `top` (the largest value
    when None). A zero is a space, so a night with no runs is empty."""
    top = top if top else max(values or [0]) or 1
    out = []
    for v in values:
        if not v:
            out.append(" ")
            continue
        out.append(SPARK[min(len(SPARK) - 1, max(0, round(v / top * (len(SPARK) - 1))))])
    return "".join(out)


def _bar(pct: float | None, width: int = 20) -> tuple[str, str]:
    pct = max(0.0, min(100.0, float(pct or 0)))
    full = round(pct / 100 * width)
    return "█" * full, "░" * (width - full)


def _heading(t: Text, title: str, count: int | None = None, style: str = DIM) -> None:
    t.append("\n" + title.upper(), style=f"bold {style}")
    if count is not None:
        t.append(f"  {count}", style=FG)
    t.append("\n")


def _line_head(t: Text, item: dict) -> None:
    t.append(f"  {item.get('project') or ''}", style=f"bold {AMBER}")
    target = str(item.get("target") or "")
    if target and target != item.get("project"):
        t.append(f"  {target}", style=DIM)
    t.append("\n")


def _board(t: Text, sections: dict) -> None:
    decisions = sections.get("decision") or []
    _heading(t, "Decisions for you", len(decisions), AMBER)
    if not decisions:
        t.append("  Nothing waits on you.\n", style=DIM)
    for d in decisions:
        _line_head(t, d)
        t.append(f"  {d.get('text')}\n", style=FG)
        if d.get("options"):
            t.append("  " + "  ".join(f"[{o}]" for o in d["options"]) + "\n", style=AMBER)
        if d.get("link"):
            t.append(f"  {d['link']}\n", style=AMBER)
    shipped = sections.get("shipped") or []
    _heading(t, "Ready for you to test", len(shipped), GREEN)
    if not shipped:
        t.append("  Nothing new to test today.\n", style=DIM)
    for d in shipped:
        _line_head(t, d)
        t.append(f"  {d.get('text')}\n", style=FG)
        if d.get("link"):
            t.append(f"  {d['link']}\n", style=AMBER)
        for i, step in enumerate(d.get("steps") or [], 1):
            t.append(f"    {i}. {step}\n", style=DIM)
    finished = sections.get("finished") or []
    if finished:
        _heading(t, "Finished today", len(finished))
        for d in finished:
            _line_head(t, d)
            t.append(f"  {d.get('text')}\n", style=FG)
    progress = sections.get("progress") or []
    _heading(t, "In progress", len(progress))
    if not progress:
        t.append("  Nothing running.\n", style=DIM)
    for d in progress:
        _line_head(t, d)
        t.append(f"  {d.get('text')}", style=FG)
        if d.get("when"):
            t.append(f"  · {d['when']}", style=DIM)
        t.append("\n")


def _machine(t: Text, m: dict, accounts: list[dict], now: float) -> None:
    _heading(t, "Machine", style=RED if m.get("overloaded") else DIM)
    if m.get("overloaded"):
        t.append(f"  Overloaded: {m['overloaded']}\n", style=f"bold {RED}")
    power = m.get("power") or {}
    parts = []
    if power.get("source"):
        parts.append(("cable" if power["source"] == "cable" else f"battery {power.get('percent')}%",
                      RED if power["source"] == "battery" and (power.get("percent") or 100) <= 20 else FG))
    if m.get("disk_free_gb") is not None:
        low = m["disk_free_gb"] < (m.get("disk_low_gb") or 0)
        parts.append((f"disk {m['disk_free_gb']} GB free", RED if low else FG))
    if m.get("load") is not None:
        parts.append((f"load {m['load']:.1f} on {m.get('cores')} cores", FG))
    if m.get("mem_free_mb") is not None:
        low = m["mem_free_mb"] < (m.get("mem_low_mb") or 0)
        parts.append((f"memory {m['mem_free_mb']} MB free", RED if low else FG))
    if m.get("docker_up") is not None:
        parts.append(("Docker up" if m["docker_up"] else "Docker down", FG if m["docker_up"] else RED))
    t.append("  ")
    for i, (text, style) in enumerate(parts):
        if i:
            t.append("  ·  ", style=DIM)
        t.append(text, style=style)
    t.append("\n")
    loads = [p.get("load") or 0 for p in m.get("history") or []]
    if loads:
        t.append("  load, 24 h  ", style=DIM)
        t.append(spark(loads[-72:], max(max(loads), m.get("overload_at") or 0)), style=BLUE)
        t.append(f"  overload at {m.get('overload_at'):g}\n", style=DIM)
    for a in accounts:
        t.append(f"  {a.get('account'):<12}", style=f"bold {FG}")
        if a.get("logged_out"):
            t.append("logged out, run /login\n", style=RED)
            continue
        ends = _epoch(a.get("login_ends_at"))
        if ends:
            t.append(f"login ends in {_span(ends - now)}", style=RED if ends - now < 86400 else DIM)
        if a.get("suggested"):
            t.append("  · next session here", style=GREEN)
        t.append("\n")
        for w in a.get("windows") or []:
            full, empty = _bar(w.get("pct"))
            pct = float(w.get("pct") or 0)
            t.append(f"    {w.get('name')[:16]:<16} ", style=DIM)
            t.append(full, style=RED if pct >= 90 else AMBER if pct >= 70 else GREEN)
            t.append(empty, style="#2a2520")
            resets = _epoch(w.get("resets_at"))
            t.append(f" {pct:3.0f}%" + (f"  resets in {_span(resets - now)}" if resets else "") + "\n", style=DIM)


def _nightly(t: Text, n: dict) -> None:
    days = n.get("days") or []
    jobs = n.get("jobs") or []
    _heading(t, "Nightly jobs, the last 7 nights")
    if not jobs:
        t.append("  No cronrun jobs in the crontab.\n", style=DIM)
        return
    width = max(len(j.get("label") or "") for j in jobs) + 2
    t.append(" " * (width + 2) + " ".join(datetime.fromisoformat(d).strftime("%a")[:2] for d in days) + "\n", style=DIM)
    for j in jobs:
        t.append(f"  {j.get('label', ''):<{width}}", style=FG)
        for c in j.get("cells") or []:
            ch, style = CELL.get(c.get("state"), ("?", DIM))
            t.append(f"{ch}  ", style=style)
        t.append(f"{j.get('at', '')}\n", style=DIM)
    t.append("  ", style=DIM)
    for state, word in (("ok", "passed"), ("fail", "failed"), ("skip", "skipped"), ("miss", "did not run"), ("unknown", "no record")):
        ch, style = CELL[state]
        t.append(ch, style=style)
        t.append(f" {word}  ", style=DIM)
    t.append("\n")


def _runs(t: Text, r: dict | None) -> None:
    _heading(t, "Pipeline time, clavesa")
    if not r or not r.get("nights"):
        t.append("  No run timings on the Mac yet. The 10:45 runs-export writes them.\n", style=DIM)
        return
    nights, phases = r["nights"], r.get("phases") or []
    totals = [sum(x.get("v") or []) for x in nights]
    g = str(r.get("generated_at") or "")
    t.append(f"  exported {g[8:10]}.{g[5:7]} {g[11:16]} · {r.get('count')} runs in 90 days\n" if g else "", style=DIM)
    t.append(f"  {nights[0]['d'][8:10]}.{nights[0]['d'][5:7]} ", style=DIM)
    for x, total in zip(nights, totals):
        t.append(spark([total], max(totals) or 1) if total else " ", style=RED if x.get("f") else BLUE)
    t.append(f" {nights[-1]['d'][8:10]}.{nights[-1]['d'][5:7]}  ", style=DIM)
    t.append(f"top {max(totals):.0f} min a night\n", style=DIM)
    last = nights[-1]
    if last.get("n"):
        total = sum(last["v"]) or 1
        runs_word = "run" if last["n"] == 1 else "runs"
        t.append(f"  last night {total:.0f} min, {last['n']} {runs_word}, {last.get('f', 0)} failed\n  ", style=FG)
        width = 50
        for p, v in zip(phases, last["v"]):
            cells = round(v / total * width)
            if cells:
                t.append("█" * cells, style=PHASE_COLOR.get(p, DIM))
        t.append("\n  ")
        for p, v in zip(phases, last["v"]):
            if v > 0:
                t.append("■ ", style=PHASE_COLOR.get(p, DIM))
                t.append(f"{p} {v:.1f}  ", style=DIM)
        t.append("\n")
    for p in r.get("pipelines") or []:
        runs = p.get("runs") or []
        t.append(f"  {p.get('name', '')[:34]:<34} ", style=FG)
        t.append(f"{spark([sum(x.get('v') or []) for x in runs]):<30}", style=BLUE)
        latest = (p.get("latest_ms") or 0) / 60000
        t.append(f" latest {latest:.1f} min", style=DIM)
        if p.get("median_ms") is not None:
            t.append(f", median {p['median_ms'] / 60000:.1f}", style=DIM)
        if runs and runs[-1].get("f"):
            t.append("  failed", style=RED)
        t.append("\n")


def _thread(t: Text, messages: list[dict] | None) -> None:
    if messages is None:
        return
    _heading(t, "Thread with Head Jeeves", len(messages))
    if not messages:
        t.append("  Nothing in the phone thread yet.\n", style=DIM)
    for m in messages[:12]:
        when = datetime.fromtimestamp(m.get("created") or 0).strftime("%a %H:%M")
        if m.get("from") == "head-jeeves":
            t.append(f"  {when}  ", style=DIM)
            t.append("Head Jeeves: ", style=f"bold {AMBER}")
            t.append(f"{m.get('text')}\n", style=FG)
            continue
        to = "Head Jeeves" if m.get("line") == "head-jeeves" else m.get("target")
        t.append(f"  {when}  to {to}", style=DIM)
        t.append(f"  {MSG_STATE.get(m.get('state'), m.get('state'))}\n", style=RED if m.get("state") == "expired" else DIM)
        t.append(f"    {m.get('text')}\n", style=FG)
        if m.get("reply"):
            t.append("    Head Jeeves: ", style=f"bold {AMBER}")
            t.append(f"{m['reply']}\n", style=FG)


def render(state: dict | None, messages: list[dict] | None = None, now: float | None = None) -> Text:
    """The whole board screen from a state.json object and, when given, the
    thread's messages (newest first)."""
    now = now or datetime.now().timestamp()
    t = Text()
    if not state:
        t.append("No state yet: the screen writes it within a minute.", style=DIM)
        return t
    t.append(f"{state.get('mac') or 'Mac'} · made {_span(now - (state.get('generated_at') or now))} ago", style=DIM)
    t.append(f"  ·  {(state.get('board') or {}).get('summary', '')}\n", style=FG)
    _board(t, (state.get("board") or {}).get("sections") or {})
    _thread(t, messages)
    _machine(t, state.get("machine") or {}, state.get("accounts") or [], now)
    _nightly(t, state.get("nightly") or {})
    _runs(t, state.get("runs"))
    return t
