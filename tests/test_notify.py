"""`podbay notify`: the configured command runs with the text as its last
argument, and nothing that goes wrong in it reaches the caller as a crash."""

import json
import stat
import subprocess
import sys

import pytest

from podbay import config
from podbay import notify

# conftest stubs subprocess.run for every test so nothing shells out to the
# real claude; these tests run their own throwaway scripts, so they get the
# real one back. Captured at collection, before any fixture has patched it.
_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _real_subprocess(monkeypatch):
    monkeypatch.setattr(notify.subprocess, "run", _REAL_RUN)


def _script(tmp_path, body: str):
    path = tmp_path / "notify-command"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_the_text_is_one_argument_after_the_commands_own(tmp_path):
    out = tmp_path / "argv.json"
    script = _script(tmp_path, f'{sys.executable} -c "import json,sys; json.dump(sys.argv[1:], open(sys.argv[0] and \'{out}\', \'w\'))" "$@"')
    text = "Publish now, or wait for the new levels? It's 'your' call: https://example.test/x"
    sent, reason = notify.send(text, f'{script} "Head Jeeves" --tag ""')
    assert (sent, reason) == (True, "")
    assert json.loads(out.read_text()) == ["Head Jeeves", "--tag", "", text]


def test_tilde_in_the_program_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert notify.argv("~/bin/push --tag x", "hi") == [str(tmp_path / "bin" / "push"), "--tag", "x", "hi"]


def test_no_command_means_off_and_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    assert notify.send("hello") == (False, "off")
    assert notify.send("hello", "   ") == (False, "off")


def test_the_command_comes_from_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    out = tmp_path / "got.txt"
    script = _script(tmp_path, f'printf %s "$1" > "{out}"')
    config.set_value("notify-command", str(script))
    assert notify.send("one line") == (True, "")
    assert out.read_text() == "one line"


def test_failures_are_one_line_and_logged_never_raised(tmp_path, caplog):
    caplog.set_level("ERROR", logger="podbay.notify")

    failing = _script(tmp_path, 'echo "no device accepted the notification" >&2; exit 1')
    sent, reason = notify.send("x", str(failing))
    assert not sent
    assert reason == f"{failing} exited 1: no device accepted the notification"

    sent, reason = notify.send("x", str(tmp_path / "missing"))
    assert not sent and "could not be run" in reason

    slow = _script(tmp_path, "sleep 5")
    sent, reason = notify.send("x", str(slow), timeout=0.2)
    assert not sent and reason == f"{slow} did not finish in 0.2 s"

    sent, reason = notify.send("x", 'unbalanced "quote')
    assert not sent and "does not parse" in reason

    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 4


def test_cmd_notify_prints_one_line_and_exits_1_when_nothing_was_sent(tmp_path, monkeypatch, capsys):
    from podbay.app import cmd_notify

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    assert cmd_notify("hello") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "nothing sent: no notify command is set (podbay config notify-command <command>)\n"

    script = _script(tmp_path, "exit 0")
    config.set_value("notify-command", str(script))
    assert cmd_notify("hello") == 0
    assert capsys.readouterr().out == "sent\n"

    config.set_value("notify-command", str(tmp_path / "missing"))
    assert cmd_notify("hello") == 1
    assert capsys.readouterr().err.startswith("not sent: ")
