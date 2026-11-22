"""podbay send checks that Claude Code took the text as a prompt, and
podbay screen says when the input box shows text podbay did not type.

2026-11-21: clavesa-abac's box showed "go on with 1b" twice, and it read as
a send whose Enter got lost. No one had sent it: it was Claude Code's prompt
suggestion, dim text that a plain screen read cannot tell from typed text."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from podbay import app as app_mod
from podbay import iterm as iterm_mod
from podbay import opened as opened_mod
from podbay import sources

from tests.test_app import _selection_session

# The screen podbay screen read on 2026-11-22, cut to its last lines.
SUGGESTION_SCREEN = """\
⏺ Compacted while idle, before the prompt cache expired · turn off with "idleCompaction": false in
  settings.json
                                                                       ✔ Update installed · /restart to apply
──────────────────────────────────────────────────────────────────────────────────────── clavesa-abac ─
❯ go on with 1b
───────────────────────────────────────────────────────────────────────────────────────────────────────
  [urbangreen] clavesa-dev git:(main) ~3 +1  ctx:0%  7d:34% (23h 39m)
  ⏵⏵ auto mode on (shift+tab to cycle)"""

EMPTY_SCREEN = SUGGESTION_SCREEN.replace("❯ go on with 1b", "❯ ")

LONG = "Head Jeeves answers. Use only what Lake Formation does natively and hide the tagged columns per audience."


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _user(text, at, **extra) -> dict:
    return {"type": "user", "timestamp": _stamp(at), "message": {"role": "user", "content": text}, **extra}


def _write(path, *records):
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---- the transcript check ----


def test_a_user_record_after_the_send_counts_and_one_before_it_does_not(tmp_path):
    path = tmp_path / "t.jsonl"
    since = datetime.now()
    _write(path, _user(LONG, since - timedelta(minutes=5)))
    assert not sources.prompt_recorded(path, LONG, since)
    _write(path, _user(LONG, since + timedelta(seconds=1)))
    assert sources.prompt_recorded(path, LONG, since)


def test_a_busy_session_queues_the_prompt_and_that_counts(tmp_path):
    path = tmp_path / "t.jsonl"
    since = datetime.now()
    _write(path, {"type": "queue-operation", "operation": "enqueue", "timestamp": _stamp(since), "content": LONG})
    assert sources.prompt_recorded(path, LONG, since)


def test_a_long_prompt_matches_through_line_breaks(tmp_path):
    path = tmp_path / "t.jsonl"
    since = datetime.now()
    _write(path, _user(LONG.replace(" per ", "\nper "), since))
    assert sources.prompt_recorded(path, LONG, since)


def test_a_short_prompt_must_be_the_whole_message(tmp_path):
    path = tmp_path / "t.jsonl"
    since = datetime.now()
    _write(path, _user("Done. Next I start slice 1b.", since))
    assert not sources.prompt_recorded(path, ".", since)
    _write(path, _user("go on\n.", since))  # two queued prompts joined
    assert sources.prompt_recorded(path, ".", since)


def test_tool_results_meta_and_compact_summaries_do_not_count(tmp_path):
    path = tmp_path / "t.jsonl"
    since = datetime.now()
    _write(
        path,
        {"type": "user", "timestamp": _stamp(since), "message": {"content": [{"type": "tool_result", "content": LONG}]}},
        _user(LONG, since, isMeta=True),
        _user(LONG, since, isCompactSummary=True),
        _user(LONG, since, isSidechain=True),
    )
    assert not sources.prompt_recorded(path, LONG, since)


def test_a_slash_command_matches_its_recorded_arguments(tmp_path):
    path = tmp_path / "t.jsonl"
    since = datetime.now()
    text = "/head-jeeves event clavesa-abac: clavesa-abac has reported"
    recorded = "<command-message>head-jeeves</command-message>\n<command-name>/head-jeeves</command-name>\n<command-args>event clavesa-abac: clavesa-abac has reported</command-args>"
    _write(path, _user(recorded, since))
    assert sources.prompt_recorded(path, text, since)


# ---- the input box ----


def test_the_input_box_text_is_read_from_the_last_box():
    assert iterm_mod.input_box_text(SUGGESTION_SCREEN) == "go on with 1b"
    assert iterm_mod.input_box_text(EMPTY_SCREEN) == ""
    assert iterm_mod.input_box_text("$ ls\nfile") is None
    queued = EMPTY_SCREEN.replace("❯ ", "❯ Press up to edit queued messages ")
    assert iterm_mod.input_box_text(queued) == ""


def test_a_wrapped_long_text_in_the_box_is_held():
    screen = EMPTY_SCREEN.replace("❯ ", "❯ " + LONG[:50] + "\n  " + LONG[50:])
    box = iterm_mod.input_box_text(screen)
    assert iterm_mod.box_holds(box, LONG)
    assert not iterm_mod.box_holds(box, "something else entirely")
    assert iterm_mod.box_holds("[Pasted text #1 +12 lines]", LONG)
    assert not iterm_mod.box_holds("", LONG)


def test_enter_alone_does_not_clear_the_line_first():
    script = "\n".join(iterm_mod.ENTER_SCRIPT_LINES)
    assert "character id" not in script and 'write text ""' in script


# ---- podbay send ----


@pytest.fixture
def send_env(monkeypatch, tmp_path):
    """A target session with a transcript in tmp_path, a fake tab whose
    screen the test sets, and a typist that writes nothing by itself."""
    path = tmp_path / "t.jsonl"
    path.write_text("")
    target = _selection_session("sora", datetime.now(), tty="/dev/ttys002", pid=os.getpid())
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [target])
    monkeypatch.setattr(app_mod.iterm_mod, "ItermLister", lambda *_a, **_k: None)
    monkeypatch.setattr(app_mod.iterm_mod, "find_tty", lambda pid: "/dev/ttys002")
    monkeypatch.setattr(app_mod, "_ancestor_pids", lambda pid: [1])
    monkeypatch.setattr(app_mod, "_transcript_of", lambda s: path)
    monkeypatch.setattr(app_mod, "SUBMIT_WAIT", 0.2)
    monkeypatch.setattr(app_mod, "SUBMIT_POLL", 0.05)
    env = {"path": path, "screen": EMPTY_SCREEN, "typed": [], "enters": 0, "on_send": None, "on_enter": None}

    def send(tty, text):
        env["typed"].append(text)
        if env["on_send"]:
            env["on_send"](text)
        return "sent"

    def enter(tty):
        env["enters"] += 1
        if env["on_enter"]:
            env["on_enter"]()
        return True

    monkeypatch.setattr(app_mod.iterm_mod, "send_text_retrying", send)
    monkeypatch.setattr(app_mod.iterm_mod, "press_enter", enter)
    monkeypatch.setattr(app_mod.iterm_mod, "read_session_text", lambda tty, max_lines=200: env["screen"])
    return env


def test_send_is_done_when_the_transcript_takes_the_prompt(send_env, capsys):
    send_env["on_send"] = lambda text: _write(send_env["path"], _user(text, datetime.now()))
    app_mod.cmd_send("sora", LONG)
    assert send_env["enters"] == 0
    assert capsys.readouterr().err == ""


def test_a_lost_enter_is_pressed_again_and_then_taken(send_env, capsys):
    def stuck(text):
        send_env["screen"] = EMPTY_SCREEN.replace("❯ ", "❯ " + text)

    send_env["on_send"] = stuck
    send_env["on_enter"] = lambda: _write(send_env["path"], _user(LONG, datetime.now()))
    app_mod.cmd_send("sora", LONG)
    assert send_env["enters"] == 1
    assert app_mod.TAKEN_ON_ENTER in capsys.readouterr().err


def test_text_that_stays_in_the_box_fails_loudly(send_env, capsys):
    send_env["on_send"] = lambda text: send_env.update(screen=EMPTY_SCREEN.replace("❯ ", "❯ " + text))
    with pytest.raises(SystemExit) as exit_:
        app_mod.cmd_send("sora", "go on with 1b")
    assert exit_.value.code == 1
    assert send_env["enters"] == 1
    assert "did not take it" in capsys.readouterr().err


def test_a_suggestion_in_the_box_is_not_taken_for_the_sent_text(send_env, capsys):
    # The text left the box and the transcript has not shown it yet; the box
    # holds Claude Code's suggestion. No second Enter: it would submit that.
    send_env["on_send"] = lambda text: send_env.update(screen=SUGGESTION_SCREEN)
    app_mod.cmd_send("sora", LONG)
    assert send_env["enters"] == 0
    assert app_mod.LEFT_BOX in capsys.readouterr().err


def test_an_answer_to_a_dialog_is_not_checked_as_a_prompt(send_env, capsys):
    send_env["screen"] = "Do you want to proceed?\n❯ 1. Yes\n  2. No\nEnter to confirm"
    app_mod.cmd_send("sora", "1")
    assert send_env["typed"] == ["1"] and send_env["enters"] == 0
    assert capsys.readouterr().err == ""


# ---- podbay screen ----


def _screen_env(monkeypatch, screen):
    target = _selection_session("abc", datetime.now(), tty="/dev/ttys004", pid=7)
    monkeypatch.setattr(app_mod, "_find_session", lambda t: target)
    monkeypatch.setattr(app_mod.iterm_mod, "find_tty", lambda pid: "/dev/ttys004")
    monkeypatch.setattr(app_mod.iterm_mod, "read_session_text", lambda tty, max_lines=40: screen)


def test_screen_says_when_the_box_holds_text_podbay_did_not_type(monkeypatch, capsys):
    _screen_env(monkeypatch, SUGGESTION_SCREEN)
    app_mod.cmd_screen("abc", 40)
    out = capsys.readouterr()
    assert "❯ go on with 1b" in out.out
    assert "prompt suggestion" in out.err and "go on with 1b" in out.err


def test_screen_is_quiet_about_text_podbay_typed_or_an_empty_box(monkeypatch, capsys):
    opened_mod.record_sent("abc", "go on with 1b", "head-jeeves", datetime.now())
    _screen_env(monkeypatch, SUGGESTION_SCREEN)
    app_mod.cmd_screen("abc", 40)
    assert capsys.readouterr().err == ""
    _screen_env(monkeypatch, EMPTY_SCREEN)
    app_mod.cmd_screen("abc", 40)
    assert capsys.readouterr().err == ""
