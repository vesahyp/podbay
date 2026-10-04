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
    return SimpleNamespace(_quitting=False, _polls=polls or {}, _code_version=version, _restart=False, exit=lambda: exited.append(1)), exited


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
