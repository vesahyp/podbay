"""`podbay close` and the launch record behind it: who started a session
with `podbay open`, ending a finished one, and closing its terminal."""

from datetime import datetime, timedelta

import pytest

from podbay import app as app_mod
from podbay import iterm
from podbay import opened as opened_mod

from tests.test_app import _selection_session
from tests.test_open import ZSH_PROMPT, _patch_open, _shell


def test_a_launch_names_its_session_by_id_once_recorded():
    at = datetime.now()
    opened_mod.record("/dev/ttys008", "sora-fix", "head-jeeves", at)
    opened_mod.set_session("/dev/ttys008", at, "abc")
    session = _selection_session("abc", at, tty="/dev/ttys009", started_at=at)
    assert opened_mod.opener(session, opened_mod.read()) == "head-jeeves"


def test_before_the_id_is_known_the_tty_and_start_time_name_it():
    at = datetime.now()
    opened_mod.record("/dev/ttys008", "sora-fix", "head-jeeves", at)
    launches = opened_mod.read()
    fresh = _selection_session("new", at, tty="/dev/ttys008", started_at=at + timedelta(seconds=20))
    older = _selection_session("old", at, tty="/dev/ttys008", started_at=at - timedelta(hours=1))
    much_later = _selection_session("later", at, tty="/dev/ttys008", started_at=at + timedelta(hours=1))
    assert opened_mod.opener(fresh, launches) == "head-jeeves"
    assert opened_mod.opener(older, launches) is None
    assert opened_mod.opener(much_later, launches) is None


def test_forget_drops_the_launch_and_old_launches_age_out():
    now = datetime.now()
    opened_mod.record("/dev/ttys001", "old", None, now - timedelta(days=20))
    opened_mod.record("/dev/ttys002", "kept", "head-jeeves", now)
    assert [e["name"] for e in opened_mod.read()] == ["kept"]
    opened_mod.set_session("/dev/ttys002", now, "s2")
    opened_mod.forget("s2")
    assert opened_mod.read() == []


def test_open_records_the_session_that_ran_it(tmp_path, monkeypatch):
    head = _selection_session("hj", datetime.now(), name="head-jeeves", pid=4242)
    free = _shell("free", "/dev/ttys008")
    started = _selection_session("new", datetime.now(), tty="/dev/ttys008")
    started.cwd = str(tmp_path)
    scans = [[head, free]]
    _patch_open(monkeypatch, tmp_path, lambda: scans.pop(0) if scans else [head, started], {"/dev/ttys008": ZSH_PROMPT})
    monkeypatch.setattr(app_mod, "_ancestor_pids", lambda pid: [999, 4242, 1])

    app_mod.cmd_open(str(tmp_path), None, "sora-fix", "Fix it.", wait=5)

    [entry] = opened_mod.read()
    assert (entry["tty"], entry["name"], entry["by"], entry["session_id"]) == ("/dev/ttys008", "sora-fix", "head-jeeves", "new")


def test_open_from_a_plain_shell_records_no_opener(tmp_path, monkeypatch):
    free = _shell("free", "/dev/ttys008")
    _patch_open(monkeypatch, tmp_path, lambda: [free], {"/dev/ttys008": ZSH_PROMPT})
    monkeypatch.setattr(app_mod, "_ancestor_pids", lambda pid: [999, 1])

    app_mod.cmd_open(str(tmp_path), None, None, "", wait=0)

    [entry] = opened_mod.read()
    assert entry["by"] is None


def _patch_close(monkeypatch, sessions, alive_after_term=False):
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.iterm_mod, "ItermLister", lambda *_a, **_k: None)
    monkeypatch.setattr(app_mod, "CLOSE_WAIT_SECONDS", 0)
    signals, closed = [], []
    alive = {s.pid for s in sessions}

    def fake_kill(pid, sig):
        if sig == 0:
            if pid not in alive:
                raise ProcessLookupError
            return
        signals.append((pid, sig))
        if not alive_after_term:
            alive.discard(pid)

    monkeypatch.setattr(app_mod.os, "kill", fake_kill)
    monkeypatch.setattr(app_mod.iterm_mod, "close_tty", lambda tty: closed.append(tty) or True)
    return signals, closed


def test_close_ends_a_finished_session_and_closes_its_terminal(monkeypatch, capsys):
    done = _selection_session("done", datetime.now(), pid=501, tty="/dev/ttys006", window_number=6)
    opened_mod.record("/dev/ttys006", "done", "head-jeeves", datetime.now())
    opened_mod.set_session("/dev/ttys006", datetime.fromisoformat(opened_mod.read()[0]["opened_at"]), "done")
    signals, closed = _patch_close(monkeypatch, [done])

    app_mod.cmd_close("#6", force=False)

    assert signals == [(501, app_mod.signal.SIGTERM)]
    assert closed == ["/dev/ttys006"]
    assert opened_mod.read() == []
    assert opened_mod.closed_ids() == {"done"}
    assert "closed done and terminal #6" in capsys.readouterr().out


def test_close_leaves_a_working_session_alone_unless_forced(monkeypatch, capsys):
    busy = _selection_session("busy", datetime.now(), pid=502, tty="/dev/ttys007", last_turn="in_progress")
    signals, closed = _patch_close(monkeypatch, [busy])

    with pytest.raises(SystemExit):
        app_mod.cmd_close("busy", force=False)
    assert signals == [] and closed == []
    assert "busy is working" in capsys.readouterr().err

    app_mod.cmd_close("busy", force=True)
    assert closed == ["/dev/ttys007"]


def test_close_never_closes_head_jeeves(monkeypatch):
    head = _selection_session("hj", datetime.now(), name="head-jeeves", pid=503, tty="/dev/ttys003")
    signals, closed = _patch_close(monkeypatch, [head])

    with pytest.raises(SystemExit):
        app_mod.cmd_close("head-jeeves", force=True)
    assert signals == [] and closed == []


def test_close_keeps_the_terminal_when_claude_will_not_exit(monkeypatch):
    stuck = _selection_session("stuck", datetime.now(), pid=504, tty="/dev/ttys004")
    _signals, closed = _patch_close(monkeypatch, [stuck], alive_after_term=True)

    with pytest.raises(SystemExit):
        app_mod.cmd_close("stuck", force=False)
    assert closed == []


class _Done:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def test_close_tty_closes_a_window_its_last_session_left_empty(monkeypatch):
    # iTerm2 keeps a window with no tabs after its last session closes, and
    # ignores a close of it for a few seconds: close_tty asks again.
    calls = []
    answers = iter(["closed 17125 last\n", "waiting\n", "waiting\n", "gone\n"])

    def fake_run(cmd, **kw):
        calls.append(cmd[-1])
        return _Done(next(answers))

    monkeypatch.setattr(iterm.subprocess, "run", fake_run)
    monkeypatch.setattr(iterm.time, "sleep", lambda s: None)
    assert iterm.close_tty("/dev/ttys015")
    assert calls == ["/dev/ttys015", "17125", "17125", "17125"]


def test_close_tty_leaves_other_tabs_and_reports_a_missing_tty(monkeypatch):
    calls = []
    answers = iter(["closed 17125 more\n", "missing\n"])
    monkeypatch.setattr(iterm.subprocess, "run", lambda cmd, **kw: calls.append(cmd[-1]) or _Done(next(answers)))
    assert iterm.close_tty("/dev/ttys015")
    assert calls == ["/dev/ttys015"]
    assert not iterm.close_tty("/dev/ttys099")
