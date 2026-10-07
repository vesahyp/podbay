"""`podbay open`: the prompt goes through a file, a terminal is free only
at an empty prompt, every session starts in the home base, and the command
waits for claude to come up."""

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from podbay import app as app_mod
from podbay import iterm
from podbay.accounts import Account

from tests.test_app import _selection_session

ZSH_PROMPT = "➜  jeeves git:(main) "


@pytest.mark.parametrize("screen", [
    f"last login\n{ZSH_PROMPT}\n\n\n",
    "user@host ~ $ ",
    "% ",
])
def test_a_fresh_prompt_is_free(screen):
    assert iterm.screen_at_empty_prompt(screen)


@pytest.mark.parametrize("screen", [
    f"{ZSH_PROMPT}cd /x && claude 'a very long prompt cut at 1024\ndquote> \n\n",
    f"{ZSH_PROMPT}echo $(\ncmdsubst quote> ",
    "$ echo 'open\n> ",
    "",
    None,
])
def test_a_continuation_line_or_an_unread_screen_is_not_free(screen):
    assert not iterm.screen_at_empty_prompt(screen)


def test_the_typed_command_stays_short_and_hands_claude_the_whole_prompt(tmp_path):
    prompt = "Fix the 'open' bug; \"quotes\", $HOME and `ticks` stay literal.\n" + "x" * 3000
    prompt_file = app_mod._write_prompt_file(prompt, tmp_path / "prompts")
    account = Account("claude", Path.home() / ".claude")
    command = app_mod.open_command(account, str(tmp_path), "fixer", prompt_file)

    assert len(command) < 300
    # The shell, not the terminal, expands the file: run the line with a
    # stand-in claude that prints what it was given. (Popen: conftest
    # stubs subprocess.run.)
    shell = subprocess.Popen(
        ["bash", "-c", f"claude() {{ printf '%s\\0' \"$@\"; }}; {command}"],
        stdout=subprocess.PIPE, text=True,
    )
    out, _ = shell.communicate()
    assert shell.returncode == 0
    assert out.split("\0")[:-1] == ["-n", "fixer", prompt]


def test_old_prompt_files_are_dropped(tmp_path):
    import os
    import time

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    old = prompts / "old.txt"
    old.write_text("x")
    week_ago = time.time() - 8 * 86400
    os.utime(old, (week_ago, week_ago))
    app_mod._write_prompt_file("new", prompts)
    assert not old.exists()
    assert len(list(prompts.glob("*.txt"))) == 1


def test_a_session_for_another_repo_starts_in_the_home_base(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod.sources, "REPOS_DIR", tmp_path)
    (tmp_path / "jeeves").mkdir()
    (tmp_path / "site").mkdir()

    launch_dir, prompt = app_mod.open_launch(str(tmp_path / "site"), "Build it.")
    assert launch_dir == str(tmp_path / "jeeves")
    assert prompt == f"The work is in {tmp_path / 'site'}.\n\nBuild it."

    assert app_mod.open_launch(str(tmp_path / "jeeves"), "Hi.") == (str(tmp_path / "jeeves"), "Hi.")


def test_without_a_home_base_checkout_it_starts_in_the_directory(tmp_path):
    assert app_mod.open_launch(str(tmp_path), "") == (str(tmp_path), "")


def _shell(name: str, tty: str):
    shell = _selection_session(name, datetime.now(), last_turn=None, has_transcript=False, is_shell=True, tty=tty)
    shell.window_number = int(tty[-1])
    return shell


def _patch_open(monkeypatch, tmp_path, sessions, screens):
    monkeypatch.setattr(app_mod, "PROMPTS_DIR", tmp_path / "prompts")
    monkeypatch.setattr(app_mod, "OPEN_POLL_SECONDS", 0)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions())
    monkeypatch.setattr(app_mod.sources, "busy_ttys", lambda *_a, **_k: set())
    monkeypatch.setattr(app_mod.iterm_mod, "ItermLister", lambda *_a, **_k: None)
    monkeypatch.setattr(app_mod.iterm_mod, "read_session_text", lambda tty, max_lines=200: screens.get(tty))
    monkeypatch.setattr(app_mod.sources, "screen_ttys", lambda *_a, **_k: set())
    monkeypatch.setattr(app_mod, "_ancestor_pids", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.iterm_mod, "get_ttys_for_pids", lambda *_a, **_k: {})
    sent, windows = [], []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text_retrying", lambda tty, text: sent.append((tty, text)) or "sent")
    monkeypatch.setattr(
        app_mod.iterm_mod, "open_window_checked",
        lambda command=None, profile=None: windows.append(command) or ("w9", "/dev/ttys009"),
    )
    return sent, windows


def test_open_skips_a_terminal_stuck_on_a_continuation_line(tmp_path, monkeypatch, capsys):
    stuck = _shell("stuck", "/dev/ttys007")
    free = _shell("free", "/dev/ttys008")
    started = _selection_session("new", datetime.now(), tty="/dev/ttys008")
    started.cwd = str(tmp_path)
    scans = [[stuck, free]]
    screens = {"/dev/ttys007": f"{ZSH_PROMPT}claude 'cut\ndquote> ", "/dev/ttys008": ZSH_PROMPT}
    sent, windows = _patch_open(monkeypatch, tmp_path, lambda: scans.pop(0) if scans else [stuck, started], screens)

    app_mod.cmd_open(str(tmp_path), None, "fixer", "Fix it.", wait=5)

    assert windows == []
    assert [tty for tty, _ in sent] == ["/dev/ttys008"]
    assert '"$(cat ' in sent[0][1] and "Fix it." not in sent[0][1]
    assert "claude is up in terminal #8" in capsys.readouterr().out


def test_open_takes_a_new_window_when_no_terminal_is_at_a_prompt(tmp_path, monkeypatch):
    stuck = _shell("stuck", "/dev/ttys007")
    started = _selection_session("new", datetime.now(), tty="/dev/ttys009")
    started.cwd = str(tmp_path)
    scans = [[stuck]]
    screens = {"/dev/ttys007": "> ", "/dev/ttys009": ""}
    sent, windows = _patch_open(monkeypatch, tmp_path, lambda: scans.pop(0) if scans else [stuck, started], screens)

    app_mod.cmd_open(str(tmp_path), None, None, "Hello.", wait=5)

    assert sent == []
    assert len(windows) == 1


def test_open_reports_failure_with_the_screen_tail(tmp_path, monkeypatch, capsys):
    free = _shell("free", "/dev/ttys008")
    screens = {"/dev/ttys008": ZSH_PROMPT}
    _patch_open(monkeypatch, tmp_path, lambda: [free], screens)
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: next(ticks))
    screens_after = f"{ZSH_PROMPT}claude\nzsh: command not found: claude\n{ZSH_PROMPT}"
    monkeypatch.setattr(app_mod.iterm_mod, "send_text_retrying", lambda tty, text: screens.update({tty: screens_after}) or "sent")

    with pytest.raises(SystemExit) as exited:
        app_mod.cmd_open(str(tmp_path), None, None, "", wait=30)

    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "did not come up in terminal #8 (/dev/ttys008) within 30 s" in err
    assert "command not found: claude" in err


def test_open_window_with_tty_reads_id_and_tty(monkeypatch):
    class Done:
        returncode = 0
        stdout = "4242\t/dev/ttys011\n"

    monkeypatch.setattr(iterm.subprocess, "run", lambda cmd, **kw: Done())
    assert iterm.open_window_with_tty("ls") == ("4242", "/dev/ttys011")
    assert iterm.open_window("ls") == "4242"


def test_the_launch_command_is_the_session_command_not_keystrokes(monkeypatch):
    seen = {}

    class Done:
        returncode = 0
        stdout = "1\t/dev/ttys011\n"

    monkeypatch.setattr(iterm.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd) or Done())
    iterm.open_window_with_tty("cd /x && claude -n it")
    script = "\n".join(seen["cmd"])
    assert "write text" not in script and "activate" not in script
    assert seen["cmd"][-1] == (
        "/usr/bin/env DISABLE_UPDATE_PROMPT=true DISABLE_AUTO_UPDATE=true "
        "/bin/zsh -lic 'cd /x && claude -n it; exec /bin/zsh -l'"
    )


def test_a_window_that_failed_to_launch_is_closed(tmp_path, monkeypatch, capsys):
    screens = {"/dev/ttys009": ZSH_PROMPT}
    _patch_open(monkeypatch, tmp_path, lambda: [], screens)
    closed = []
    monkeypatch.setattr(app_mod.iterm_mod, "close_tty", lambda tty: closed.append(tty) or True)
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: next(ticks))

    with pytest.raises(SystemExit):
        app_mod.cmd_open(str(tmp_path), None, None, "", wait=30)

    assert closed == ["/dev/ttys009"]


def test_a_reused_terminal_is_not_closed_on_failure(tmp_path, monkeypatch):
    free = _shell("free", "/dev/ttys008")
    _patch_open(monkeypatch, tmp_path, lambda: [free], {"/dev/ttys008": ZSH_PROMPT})
    closed = []
    monkeypatch.setattr(app_mod.iterm_mod, "close_tty", lambda tty: closed.append(tty) or True)
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: next(ticks))

    with pytest.raises(SystemExit):
        app_mod.cmd_open(str(tmp_path), None, None, "", wait=30)

    assert closed == []


def test_a_model_goes_to_claude_as_model(tmp_path):
    account = Account("claude", Path.home() / ".claude")
    command = app_mod.open_command(account, str(tmp_path), "fixer", None, "sonnet")
    shell = subprocess.Popen(
        ["bash", "-c", f"claude() {{ printf '%s\\0' \"$@\"; }}; {command}"],
        stdout=subprocess.PIPE, text=True,
    )
    out, _ = shell.communicate()
    assert out.split("\0")[:-1] == ["-n", "fixer", "--model", "sonnet"]
    assert "--model" not in app_mod.open_command(account, str(tmp_path), "fixer", None)


def test_open_records_the_model_and_the_session_carries_it(tmp_path, monkeypatch, capsys):
    from podbay import opened as opened_mod

    free = _shell("free", "/dev/ttys008")
    started = _selection_session("new", datetime.now(), tty="/dev/ttys008")
    started.cwd = str(tmp_path)
    scans = [[free]]
    sent, _ = _patch_open(monkeypatch, tmp_path, lambda: scans.pop(0) if scans else [free, started], {"/dev/ttys008": ZSH_PROMPT})

    app_mod.cmd_open(str(tmp_path), None, "fixer", "Fix it.", wait=5, model="opus")

    assert "--model opus" in sent[0][1]
    assert "as claude on opus" in capsys.readouterr().out
    entries = opened_mod.read()
    assert entries[-1]["model"] == "opus"
    session = _selection_session("new", datetime.now(), tty="/dev/ttys008")
    session.started_at = datetime.fromisoformat(entries[-1]["opened_at"])
    assert opened_mod.opened_model(session, entries) == "opus"


TRUST_DIALOG = (
    f"{ZSH_PROMPT}claude \"$(cat {{marker}})\"\n"
    " Accessing workspace:\n /Users/someone\n Do you trust the files in this folder?\n"
    " ❯ 1. Yes, proceed\n   2. No, exit\n Enter to confirm · Esc to cancel\n"
)


def _fail_open(monkeypatch, tmp_path, screens, sessions):
    monkeypatch.setattr(app_mod, "_write_prompt_file", lambda prompt, directory=None: tmp_path / "p-1.txt")
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: next(ticks))
    return _patch_open(monkeypatch, tmp_path, sessions, screens)


def test_open_fails_while_the_screen_shows_the_trust_dialog_on_a_reused_terminal(tmp_path, monkeypatch, capsys):
    free = _shell("free", "/dev/ttys008")
    screens = {"/dev/ttys008": ZSH_PROMPT}
    sent, windows = _fail_open(monkeypatch, tmp_path, screens, lambda: [free])
    monkeypatch.setattr(
        app_mod.iterm_mod, "send_text_retrying",
        lambda tty, text: sent.append((tty, text)) or screens.update({tty: TRUST_DIALOG.format(marker=tmp_path / "p-1.txt")}) or "sent",
    )

    with pytest.raises(SystemExit) as exited:
        app_mod.cmd_open(str(tmp_path), None, None, "Hi.", wait=30)

    assert exited.value.code == 1 and windows == []
    err = capsys.readouterr().err
    assert "waiting for an answer in terminal #8" in err and "Do you trust the files" in err
    assert "claude is up" not in capsys.readouterr().out


def test_open_fails_while_the_screen_shows_the_trust_dialog_in_a_new_window(tmp_path, monkeypatch, capsys):
    screens = {}
    sent, windows = _fail_open(monkeypatch, tmp_path, screens, lambda: [])
    monkeypatch.setattr(
        app_mod.iterm_mod, "open_window_checked",
        lambda command=None, profile=None: windows.append(command) or screens.update({"/dev/ttys009": TRUST_DIALOG.format(marker=tmp_path / "p-1.txt")}) or ("w9", "/dev/ttys009"),
    )

    with pytest.raises(SystemExit):
        app_mod.cmd_open(str(tmp_path), None, None, "Hi.", wait=30)

    assert len(windows) == 1 and sent == []
    assert "waiting for an answer in a new window" in capsys.readouterr().err


@pytest.mark.parametrize("reused", [True, False])
def test_open_fails_when_the_session_started_outside_the_launch_directory(tmp_path, monkeypatch, capsys, reused):
    free = _shell("free", "/dev/ttys008")
    tty = "/dev/ttys008" if reused else "/dev/ttys009"
    started = _selection_session("new", datetime.now(), tty=tty)
    started.cwd = str(Path.home())
    scans = [[free] if reused else []]
    _patch_open(monkeypatch, tmp_path, lambda: scans.pop(0) if scans else [started], {tty: ZSH_PROMPT})
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: next(ticks))

    with pytest.raises(SystemExit):
        app_mod.cmd_open(str(tmp_path), None, None, "", wait=30)

    err = capsys.readouterr().err
    assert f"started in {Path.home()}" in err and str(tmp_path) in err


def test_a_numbered_menu_or_confirm_footer_waits_for_input_but_the_banner_does_not():
    assert iterm.screen_waits_for_input(TRUST_DIALOG.format(marker="x"))
    assert iterm.screen_waits_for_input("Continue?\n ❯ 1. Yes\n   2. No\n")
    assert not iterm.screen_waits_for_input(f"{ZSH_PROMPT}claude\n ✻ Welcome to Claude Code\n > ")
    assert not iterm.screen_waits_for_input(None)


def test_open_never_types_into_the_terminal_that_runs_the_screen_or_head_jeeves(tmp_path, monkeypatch):
    screen = _shell("screen", "/dev/ttys001")
    hj = _shell("head-jeeves", "/dev/ttys002")
    caller = _shell("caller", "/dev/ttys003")
    scans = [[screen, hj, caller]]
    screens = {t: ZSH_PROMPT for t in ("/dev/ttys001", "/dev/ttys002", "/dev/ttys003")}
    sent, windows = _patch_open(monkeypatch, tmp_path, lambda: scans.pop(0) if scans else [], screens)
    monkeypatch.setattr(app_mod.sources, "screen_ttys", lambda *_a, **_k: {"/dev/ttys001"})
    monkeypatch.setattr(app_mod.iterm_mod, "get_ttys_for_pids", lambda *_a, **_k: {1: "/dev/ttys003"})

    app_mod.cmd_open(str(tmp_path), None, "x", "Go.", wait=0)

    assert sent == []
    assert len(windows) == 1


def test_screen_command_lines():
    from podbay import sources
    assert sources._is_screen_command("uv run --project /r podbay")
    assert sources._is_screen_command("/r/.venv/bin/python /r/.venv/bin/podbay --no-splash")
    assert not sources._is_screen_command("uv run --project /r podbay open /x")
    assert not sources._is_screen_command("podbay")


def test_the_new_window_shell_cannot_ask_the_oh_my_zsh_update_question():
    command = iterm.session_command("claude")
    assert "DISABLE_UPDATE_PROMPT=true" in command
    assert "DISABLE_AUTO_UPDATE=true" in command
    # The variables come before the shell, so its startup files see them.
    assert command.index("DISABLE_UPDATE_PROMPT") < command.index("/bin/zsh")
