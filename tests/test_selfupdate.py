import subprocess
from types import SimpleNamespace

from podbay import app, selfupdate


def _git(path, *args):
    subprocess.check_call(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args], stdout=subprocess.DEVNULL)


def test_code_version_follows_the_commit(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "one")
    first = selfupdate.code_version(tmp_path)
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "two")
    second = selfupdate.code_version(tmp_path)
    assert first and second and first != second
    assert selfupdate.changed(first, second)
    assert not selfupdate.changed(first, first)


def test_unknown_version_never_counts_as_a_change(tmp_path):
    assert selfupdate.code_version(tmp_path) is None
    assert not selfupdate.changed(None, "abc")
    assert not selfupdate.changed("abc", None)


def _screen(version, polls=None):
    exited = []
    return SimpleNamespace(_quitting=False, _polls=polls or {}, _code_version=version, _restart=False, _restart_waiting_since=None, exit=lambda: exited.append(1)), exited


def test_the_screen_restarts_when_the_installed_code_moved(monkeypatch):
    monkeypatch.setattr(selfupdate, "code_version", lambda: "new")
    screen, exited = _screen("old")
    app.PodbayApp._check_code_version(screen)
    assert screen._restart and exited == [1]


def test_the_screen_stays_while_the_code_is_the_same_or_a_poll_runs(monkeypatch):
    monkeypatch.setattr(selfupdate, "code_version", lambda: "new")
    screen, exited = _screen("new")
    app.PodbayApp._check_code_version(screen)
    screen2, exited2 = _screen("old", polls={"usage": 1})
    app.PodbayApp._check_code_version(screen2)
    assert exited == [] and exited2 == [] and not screen2._restart


def test_no_poll_starts_once_the_code_moved_so_the_polls_can_drain(monkeypatch):
    """The scan runs every 3 s and may take most of it: waiting for a moment
    with no poll never ends unless the waiting stops new polls."""
    monkeypatch.setattr(selfupdate, "code_version", lambda: "new")
    screen, exited = _screen("old", polls={"session scan": 1})
    app.PodbayApp._check_code_version(screen)
    assert exited == [] and screen._restart_waiting_since is not None
    screen2 = SimpleNamespace(_quitting=False, _scanning=False, _machine_scanning=False, _usage_scanning=False,
                              _restart_waiting_since=screen._restart_waiting_since)
    app.PodbayApp.trigger_refresh(screen2)
    app.PodbayApp._refresh_machine(screen2)
    app.PodbayApp._refresh_usage(screen2)
    assert not (screen2._scanning or screen2._machine_scanning or screen2._usage_scanning)
    screen._polls.clear()
    app.PodbayApp._check_code_version(screen)
    assert screen._restart and exited == [1]


def test_a_poll_that_never_ends_holds_the_restart_only_for_the_patience(monkeypatch):
    monkeypatch.setattr(selfupdate, "code_version", lambda: "new")
    clock = [1000.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    screen, exited = _screen("old", polls={"machine": 1})
    app.PodbayApp._check_code_version(screen)
    clock[0] += app.RESTART_PATIENCE_SECONDS - 1
    app.PodbayApp._check_code_version(screen)
    assert exited == []
    clock[0] += 2
    screen._polls["open /x"] = 1
    app.PodbayApp._check_code_version(screen)
    assert exited == []  # an open in flight still holds it
    del screen._polls["open /x"]
    app.PodbayApp._check_code_version(screen)
    assert screen._restart and exited == [1]
