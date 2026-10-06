"""The mood gauge scores only what the user typed. The first prompt of a
session `podbay open` started and the text `podbay send` typed for a Claude
session are recorded at the source and left out of a session's prompts."""

from datetime import datetime

from podbay import app as app_mod
from podbay import opened as opened_mod

from tests.test_app import _selection_session
from tests.test_open import ZSH_PROMPT, _patch_open, _shell

FIRST = "The work is in a repo.\n\nThe user is not happy with the collisions, why is this STILL broken?!"


def test_the_launch_prompt_and_sent_text_are_dropped_and_the_users_words_kept():
    at = datetime.now()
    opened_mod.record("/dev/ttys008", "sora-physics", "head-jeeves", at, prompt=FIRST)
    opened_mod.set_session("/dev/ttys008", at, "abc")
    opened_mod.record_sent("abc", "stop, fix the tests first", "head-jeeves", at)
    opened_mod.record_sent("other", "unrelated order", "head-jeeves", at)
    session = _selection_session("abc", at, tty="/dev/ttys008", started_at=at)

    marked = opened_mod.agent_text(session, opened_mod.read(), opened_mod.read_sent())
    assert marked == {FIRST, "stop, fix the tests first"}
    prompts = [FIRST, "looks fine", "stop, fix the tests first", "what the fuck"]
    assert opened_mod.user_prompts(prompts, marked) == ["looks fine", "what the fuck"]


def test_a_launch_without_a_prompt_or_from_a_shell_marks_nothing():
    at = datetime.now()
    opened_mod.record("/dev/ttys008", "plain", None, at)
    opened_mod.set_session("/dev/ttys008", at, "abc")
    session = _selection_session("abc", at, tty="/dev/ttys008", started_at=at)
    assert opened_mod.agent_text(session, opened_mod.read(), []) == set()
    assert opened_mod.user_prompts(["why is this STILL broken?!"], set()) == ["why is this STILL broken?!"]


def test_open_records_the_whole_first_prompt_it_handed_claude(tmp_path, monkeypatch):
    free = _shell("free", "/dev/ttys008")
    started = _selection_session("new", datetime.now(), tty="/dev/ttys008")
    started.cwd = str(tmp_path)
    scans = [[free]]
    _patch_open(monkeypatch, tmp_path, lambda: scans.pop(0) if scans else [started], {"/dev/ttys008": ZSH_PROMPT})

    app_mod.cmd_open(str(tmp_path), None, "fixer", "Fix it STILL?!", wait=5)

    (entry,) = opened_mod.read()
    assert entry["prompt"] == "Fix it STILL?!"
    assert entry["session_id"] == "new"


def test_send_from_a_claude_session_is_recorded_and_from_a_shell_is_not(monkeypatch):
    head = _selection_session("head-jeeves", datetime.now(), tty="/dev/ttys001", pid=501)
    target = _selection_session("sora", datetime.now(), tty="/dev/ttys002", pid=502)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [head, target])
    monkeypatch.setattr(app_mod.iterm_mod, "ItermLister", lambda *_a, **_k: None)
    monkeypatch.setattr(app_mod.iterm_mod, "get_tty_for_pid", lambda pid: "/dev/ttys002")
    typed = []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text_result", lambda tty, text: typed.append((tty, text)) or "sent")

    monkeypatch.setattr(app_mod, "_ancestor_pids", lambda pid: [4242, head.pid, 1])
    app_mod.cmd_send("sora", "stop and run the tests")
    monkeypatch.setattr(app_mod, "_ancestor_pids", lambda pid: [4242, 1])
    app_mod.cmd_send("sora", "ship it")

    assert [text for _, text in typed] == ["stop and run the tests", "ship it"]
    (entry,) = opened_mod.read_sent()
    assert (entry["session_id"], entry["text"], entry["by"]) == (target.session_id, "stop and run the tests", "head-jeeves")


def test_a_session_not_yet_joined_to_its_launch_still_has_the_first_prompt_dropped():
    # The first scan after `podbay open` can see the session before it has a
    # tty or a recorded id. Its one prompt is Head Jeeves' task, relaying the
    # user's feedback in quotes, and one agent turn follows: nothing is heated.
    from podbay import mood

    task = (
        "The work is in a repo.\n\nthe game: the starting car understeers. The user played it and says: "
        "the basic car really just understeers right now. The speed now is like a really upgraded car, "
        "STILL too fast?! again!!"
    )
    at = datetime.now()
    opened_mod.record("/dev/ttys008", "early-pace", "head-jeeves", at, prompt=task)
    session = _selection_session("abc", at, tty=None, started_at=at)

    assert opened_mod.launch(session, opened_mod.read()) is None  # not joined yet
    assert mood.is_hot([task])  # the text alone would read heated
    marked = opened_mod.agent_text(session, opened_mod.read(), opened_mod.read_sent())
    assert not mood.is_hot(opened_mod.user_prompts([task], marked))
    # a prompt the user types after the agent answered is still judged
    assert mood.is_hot(opened_mod.user_prompts([task, "what the fuck"], marked))


def test_a_launch_prompt_claimed_by_another_session_is_not_dropped_here():
    at = datetime.now()
    opened_mod.record("/dev/ttys008", "one", "head-jeeves", at, prompt=FIRST)
    opened_mod.set_session("/dev/ttys008", at, "other")
    session = _selection_session("abc", at, tty="/dev/ttys009", started_at=at)
    assert opened_mod.agent_text(session, opened_mod.read(), []) == set()


def _send_with(monkeypatch, outcomes):
    target = _selection_session("sora", datetime.now(), tty="/dev/ttys002", pid=502)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [target])
    monkeypatch.setattr(app_mod.iterm_mod, "ItermLister", lambda *_a, **_k: None)
    monkeypatch.setattr(app_mod.iterm_mod, "get_tty_for_pid", lambda pid: "/dev/ttys002")
    monkeypatch.setattr(app_mod, "_ancestor_pids", lambda pid: [1])
    tries = []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text_result", lambda tty, text: tries.append(text) or outcomes.pop(0))
    return tries


def test_send_retries_once_when_the_gate_was_busy_and_nothing_was_typed(monkeypatch):
    tries = _send_with(monkeypatch, ["busy", "sent"])
    app_mod.cmd_send("sora", "go")
    assert tries == ["go", "go"]


def test_send_does_not_retry_after_a_timeout_and_says_the_text_may_have_arrived(monkeypatch, capsys):
    import pytest

    tries = _send_with(monkeypatch, ["timeout", "sent"])
    with pytest.raises(SystemExit):
        app_mod.cmd_send("sora", "go")
    assert tries == ["go"]
    err = capsys.readouterr().err
    assert "did not answer" in err and "may have arrived" in err and "could not find" not in err


def test_send_says_no_tab_only_when_iterm_answered_with_no_match(monkeypatch, capsys):
    import pytest

    _send_with(monkeypatch, ["missing"])
    with pytest.raises(SystemExit):
        app_mod.cmd_send("sora", "go")
    assert "could not find an iTerm2 tab" in capsys.readouterr().err
