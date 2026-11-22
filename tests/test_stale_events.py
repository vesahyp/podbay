from types import SimpleNamespace

from podbay import board
from podbay.app import PodbayApp


def test_each_stale_line_reaches_head_jeeves_once_and_a_failed_send_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "STALE_TOLD_PATH", tmp_path / "stale-told.json")
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
