from types import SimpleNamespace

from podbay import board
from podbay.app import PodbayApp


def test_each_stale_line_reaches_head_jeeves_once_and_a_failed_send_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "STALE_TOLD_PATH", tmp_path / "stale-told.json")
    monkeypatch.setattr(board, "HEADLINES_PATH", tmp_path / "headlines.json")
    (tmp_path / "headlines.json").write_text('{"s1": {"text": "Done today"}}')
    sent: list[str] = []
    deliver = [False]
    fake = SimpleNamespace(
        _head_jeeves=True, _quitting=False,
        _send_to_head_jeeves=lambda command: sent.append(command) or deliver[0],
        call_from_thread=lambda fn, *args: fn(*args),
    )
    line = {"key": "s1", "kind": "progress", "why": "idle", "words": "the session has been idle for 2h",
            "name": "abac-1", "title": "Access control", "text": "Done today"}
    state = {"board": {"stale": [line]}}
    PodbayApp._tell_stale_lines(fake, state)  # Head Jeeves' tab is gone: not recorded
    deliver[0] = True
    PodbayApp._tell_stale_lines(fake, state)
    PodbayApp._tell_stale_lines(fake, state)
    assert len(sent) == 2 and sent[1].startswith("/head-jeeves event abac-1: The board's progress line for Access control is stale")
    assert board.load_told() == {"s1": "idle"}
    PodbayApp._tell_stale_lines(fake, {"board": {"stale": []}})
    assert board.load_told() == {}


def test_a_stale_line_whose_entry_head_jeeves_deleted_is_not_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "STALE_TOLD_PATH", tmp_path / "stale-told.json")
    monkeypatch.setattr(board, "HEADLINES_PATH", tmp_path / "headlines.json")
    (tmp_path / "headlines.json").write_text('{"kept": {"text": "x"}}')
    sent: list[str] = []
    fake = SimpleNamespace(
        _head_jeeves=True, _quitting=False,
        _send_to_head_jeeves=lambda command: sent.append(command) or True,
        call_from_thread=lambda fn, *args: fn(*args),
    )
    mk = lambda key: {"key": key, "kind": "progress", "why": "idle", "words": "idle", "name": key, "title": key, "text": "t"}
    PodbayApp._tell_stale_lines(fake, {"board": {"stale": [mk("gone"), mk("kept")]}})
    assert len(sent) == 1 and "kept" in sent[0]
    assert board.load_told() == {"kept": "idle"}


def test_exit_from_head_jeeves_is_an_on_purpose_end():
    from podbay.app import _is_exit_order
    head = SimpleNamespace(name="head-jeeves", title="Head Jeeves", cwd=None)
    other = SimpleNamespace(name="abac-1", title="x", cwd=None)
    import podbay.app as app
    orig = app.is_head_jeeves
    app.is_head_jeeves = lambda s: s is head
    try:
        assert _is_exit_order("/exit", head) and _is_exit_order(" /exit\n", head)
        assert not _is_exit_order("/exit", other)
        assert not _is_exit_order("/exit", None)
        assert not _is_exit_order("/compact", head)
    finally:
        app.is_head_jeeves = orig
