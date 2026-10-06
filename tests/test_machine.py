import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest

from podbay import app as app_mod
from podbay import board, inventory, machine, voice
from podbay import iterm as iterm_mod

VM_STAT = """Mach Virtual Memory Statistics: (page size of 4096 bytes)
Pages free:                              100726.
Pages active:                           1448396.
Pages speculative:                         4570.
"""
PS = """  4093 407.8 5675476 /System/Library/Frameworks/V.framework/com.apple.Virtualization.VirtualMachine
  51 58.0 234596 /Users/x/chrome headless shell
  52 43.6 13316 /usr/bin/ffmpeg
  53 1.0 900000 /usr/bin/claude
bad line
"""


def test_parsers_read_the_macos_formats():
    assert machine.parse_vm_stat(VM_STAT) == round((100726 + 4570) * 4096 / 1_048_576)
    assert machine.parse_vm_stat("nothing") is None
    assert machine.parse_swap("total = 8192.00M  used = 7210.50M  free = 981.50M  (encrypted)") == 7210
    procs = machine.parse_processes(PS)
    assert len(procs) == 4 and procs[0]["name"] == "com.apple.Virtualization.VirtualMachine" and procs[0]["mem_mb"] == 5542
    top = machine.top_processes(procs)
    assert [p["pid"] for p in top["cpu"]] == [4093, 51, 52]
    assert [p["pid"] for p in top["mem"]] == [4093, 53, 51]


def test_sample_survives_every_command_failing(monkeypatch):
    monkeypatch.undo()  # the autouse stubs: use the real sample() with failing commands
    monkeypatch.setattr(machine, "_run", lambda cmd: "")
    s = machine.sample()
    assert s["cpu_pct"] is None and s["mem_free_mb"] is None and s["pressure"] is None and s["top"] == {"cpu": [], "mem": []}


def test_run_gives_nothing_on_a_timeout(monkeypatch):
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("vm_stat", 3)

    monkeypatch.setattr(machine.subprocess, "run", slow)
    assert machine._run(["vm_stat"]) == ""


def test_the_incident_reads_as_overloaded():
    incident = {"load": {"1m": 30.0}, "cores": 8, "pressure": "warn", "mem_free_mb": 300, "swap_used_mb": 7000}
    assert "load average 30 on 8 cores" in machine.overload_reason(incident)
    assert machine.overload_reason({"load": {"1m": 1.0}, "cores": 8, "pressure": "normal", "mem_free_mb": 2000}) is None
    assert "critical" in machine.overload_reason({"load": {"1m": 1.0}, "cores": 8, "pressure": "critical", "mem_free_mb": 90})
    assert machine.overload_reason({"load": {"1m": 1.0}, "cores": 8, "pressure": "warn", "mem_free_mb": 300, "swap_used_mb": 5}) == "only 300 MB of memory free"
    assert machine.overload_reason(None) is None


def test_history_is_throttled_and_cut_to_the_window(tmp_path):
    path = tmp_path / "h.jsonl"
    old = {"at": time.time() - 20 * 3600, "load": 1, "cpu": 1, "free": 1, "swap": 0, "p": "normal"}
    path.write_text(json.dumps(old) + "\n")
    os.utime(path, (time.time() - 600, time.time() - 600))
    s = {"at": time.time(), "load": {"1m": 2.0}, "cpu_pct": 5.0, "mem_free_mb": 900, "swap_used_mb": 1, "pressure": "normal"}
    assert machine.record(s, path) is True
    assert machine.record(s, path) is False  # under a minute since the last line
    points = machine.history(path=path)
    assert len(points) == 1 and points[0]["load"] == 2.0 and points[0]["free"] == 900
    path.write_text("not json\n" + json.dumps(points[0]) + "\n")
    assert len(machine.history(path=path)) == 1


def test_sparkline():
    assert machine.sparkline([0, 4, 8], top=8) == "▁▅█"
    assert machine.sparkline([None, 8], top=8) == " █"
    assert machine.sparkline([]) == "" and machine.sparkline([None]) == ""
    assert len(machine.sparkline(list(range(100)), width=10)) == 10


def test_open_refuses_under_critical_memory_pressure(monkeypatch, capsys):
    monkeypatch.setattr(machine, "memory_critical", lambda: {"pressure": "critical", "mem_free_mb": 90})
    started = []
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *a, **k: started.append(1) or [])
    with pytest.raises(SystemExit) as exited:
        app_mod.cmd_open("/tmp", None, None, "")
    assert exited.value.code == 1 and started == []
    err = capsys.readouterr().err.strip()
    assert "Memory pressure is critical, 90 MB free" in err and "\n" not in err


def test_memory_critical_reads_the_pressure_level(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(machine, "_run", lambda cmd: "4\n" if cmd[0] == "sysctl" else VM_STAT)
    assert machine.memory_critical() == {"pressure": "critical", "mem_free_mb": 411}
    monkeypatch.setattr(machine, "_run", lambda cmd: "1\n")
    assert machine.memory_critical() is None


# ---- inventory never blocks -------------------------------------------------


def _session_payload(name="sora"):
    return {
        "generated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), "self": None, "waiting": [name],
        "sessions": [{
            "name": name, "short_id": "abc123", "session_id": "abc123def", "waiting_on": {"kind": "question", "detail": "q"},
            "tab": 3, "title": "t", "account": "claude", "repos_touched": ["r"], "repos_edited": [], "registry_status": "idle",
            "idle_minutes": 4, "context_pct": 10, "last_text": "x", "last_activity_at": None, "turn_ended": True,
        }],
        "repo_groups": [{"repo": "r", "sessions": [name, "other"]}],
    }


def test_inventory_serves_the_last_snapshot_marked_stale_when_the_scan_hangs(monkeypatch, tmp_path):
    path = tmp_path / "snap.json"
    inventory.save_snapshot(_session_payload(), path)
    monkeypatch.setattr(machine, "sample", lambda: {"load": {"1m": 112.0}, "cores": 8, "pressure": "warn", "at": time.time()})
    import threading

    release = threading.Event()
    started = time.monotonic()
    payload = inventory.build_payload(lambda: release.wait(30), set(), budget=0.2, path=path, machine_budget=2)
    release.set()
    assert time.monotonic() - started < 5
    assert payload["stale"] is True and payload["sessions"][0]["name"] == "sora"
    assert "overloaded" in payload["stale_note"] and "load average 112" in payload["stale_note"]
    assert payload["stale_age_seconds"] > 3600
    assert payload["machine"]["overloaded"].startswith("load average 112")
    assert "snapshot" in inventory.render_status(payload) and "snapshot" in inventory.render_table(payload)


def test_a_stale_inventory_still_honours_exclude(tmp_path):
    path = tmp_path / "snap.json"
    inventory.save_snapshot(_session_payload(), path)
    payload = inventory.build_payload(lambda: (_ for _ in ()).throw(RuntimeError("iTerm2 stuck")), {"sora"}, budget=1, path=path)
    assert payload["stale"] and payload["sessions"] == [] and payload["waiting"] == [] and payload["repo_groups"] == []
    assert payload["stale_reason"] == "scan failed: iTerm2 stuck"


def test_no_snapshot_and_no_scan_is_an_empty_answer_with_a_reason(tmp_path):
    payload = inventory.build_payload(lambda: (_ for _ in ()).throw(RuntimeError("x")), set(), budget=1, path=tmp_path / "none.json")
    assert payload["stale"] and payload["sessions"] == [] and "no snapshot" in payload["stale_note"]


def test_a_live_scan_is_fresh_and_becomes_the_snapshot(tmp_path):
    path = tmp_path / "snap.json"
    payload = inventory.build_payload(lambda: [], set(), path=path)
    assert payload["stale"] is False and payload["machine"]["load"]["1m"] == 1.2
    assert inventory.load_snapshot(path)["sessions"] == []
    assert "machine: load 1.2/1.0 on 8" in inventory.render_table(payload)


# ---- retries and one-line errors ---------------------------------------------


def test_find_tty_retries_while_the_process_is_alive(monkeypatch):
    answers = [None, None, "/dev/ttys004"]
    monkeypatch.setattr(iterm_mod, "get_tty_for_pid", lambda pid: answers.pop(0))
    assert iterm_mod.find_tty(1) == "/dev/ttys004"


def test_find_tty_gives_up_at_once_for_a_dead_process(monkeypatch):
    calls = []
    monkeypatch.setattr(iterm_mod, "get_tty_for_pid", lambda pid: calls.append(pid))
    monkeypatch.setattr(iterm_mod, "_pid_alive", lambda pid: False)
    assert iterm_mod.find_tty(999999) is None and len(calls) == 1


def test_send_retries_a_busy_gate_up_to_three_times_and_never_a_timeout(monkeypatch):
    outcomes = ["busy", "busy", "sent"]
    monkeypatch.setattr(iterm_mod, "send_text_result", lambda tty, text: outcomes.pop(0))
    assert iterm_mod.send_text_retrying("/dev/ttys001", "x") == "sent"
    outcomes[:] = ["timeout", "sent"]
    assert iterm_mod.send_text_retrying("/dev/ttys001", "x") == "timeout"
    assert outcomes == ["sent"]


def test_a_missing_tab_is_retried_only_on_an_overloaded_machine(monkeypatch):
    outcomes = ["missing", "sent"]
    monkeypatch.setattr(iterm_mod, "send_text_result", lambda tty, text: outcomes.pop(0))
    assert iterm_mod.send_text_retrying("/dev/ttys001", "x") == "missing"
    monkeypatch.setattr(machine, "overloaded_now", lambda: "load average 30 on 8 cores")
    assert iterm_mod.send_text_retrying("/dev/ttys001", "x") == "sent"


def test_open_window_retries_a_busy_gate_then_raises(monkeypatch):
    calls = []

    def busy(cmd, timeout):
        calls.append(1)
        raise iterm_mod.GateBusy("taken")

    monkeypatch.setattr(iterm_mod, "_gated_run", busy)
    with pytest.raises(iterm_mod.GateBusy):
        iterm_mod.open_window_checked("ls")
    assert len(calls) == 4
    assert iterm_mod.open_window_with_tty("ls") is None


def test_open_names_the_overload_in_one_line(monkeypatch, capsys):
    monkeypatch.setattr(machine, "overloaded_now", lambda: "load average 30 on 8 cores")
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *a, **k: [])
    monkeypatch.setattr(app_mod.sources, "busy_ttys", lambda: set())
    monkeypatch.setattr(app_mod, "_protected_ttys", lambda sessions: set())
    monkeypatch.setattr(iterm_mod, "open_window_checked", lambda command=None, profile=None: (_ for _ in ()).throw(iterm_mod.GateBusy("taken")))
    with pytest.raises(SystemExit) as exited:
        app_mod.cmd_open("/tmp", None, None, "")
    assert exited.value.code == 1
    err = capsys.readouterr().err.strip()
    assert "could not open a new iTerm2 window" in err and "overloaded (load average 30 on 8 cores)" in err and "\n" not in err


def test_voice_lines_are_single_lines():
    for line in (
        voice.iterm_unreachable("x", "pid 1, /dev/ttys001", "load average 30 on 8 cores", True),
        voice.iterm_tab_missing("x", "pid 1", None),
        voice.open_window_failed(None, True),
        voice.inventory_stale(400, None),
    ):
        assert "\n" not in line


# ---- the board ----------------------------------------------------------------


def test_the_board_machine_room_charts_the_history():
    now = time.time()
    health = {
        **{k: v for k, v in __import__("tests.conftest", fromlist=["FAKE_SAMPLE"]).FAKE_SAMPLE.items()}, "at": now,
        "overloaded": "load average 30 on 8 cores",
        "history": [{"at": now - 3600 * 3 + i * 600, "load": 2 + i, "free": 3000 - i * 100, "p": "normal"} for i in range(18)],
    }
    html = board.render({"sessions": [], "machine": health}, now=__import__("datetime").datetime.now())
    assert html.count("<polyline") == 2 and "Overloaded: load average 30 on 8 cores" in html
    assert "last 3 h" in html and "top cpu: node 40%" in html and "top memory: claude 900 MB" in html
    assert "<polyline" not in board.render({"sessions": []}, now=__import__("datetime").datetime.now())


# ---- the screen -----------------------------------------------------------------


def test_machine_text_shows_figures_sparkline_and_top_processes():
    from tests.conftest import FAKE_SAMPLE

    health = {**FAKE_SAMPLE, "overloaded": "load average 30 on 8 cores", "history": [{"load": v, "free": 2000 - v * 10} for v in (1, 8, 16, 30)]}
    text = app_mod.machine_text(health).plain
    assert "load 1.2/1.0 on 8" in text and "pressure normal" in text and "overloaded: load average 30 on 8 cores" in text
    assert "top cpu: node 40%" in text and "top memory: claude 900 MB" in text and "█" in text
    assert app_mod.machine_text({}).plain == ""


@pytest.mark.asyncio
async def test_the_screen_shows_the_machine_panel():
    app = app_mod.PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "load 1.2" in str(app.query_one("#machine").render())
