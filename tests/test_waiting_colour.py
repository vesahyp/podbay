"""A session that asks the user a question or waits on a permission dialog
is orange wherever podbay shows sessions, in one colour
(model.WAITING_ORANGE): the session list, the board screen, the snapshot
the phone app reads, and the app's --waiting token."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from rich.text import Text

from podbay import board, boardview, snapshot
from podbay.app import WAITING_STYLE, build_rows
from podbay.model import ASKS_USER_KINDS, WAITING_ORANGE, Session, asks_user

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 8, 12, 0)
QUESTION = {"kind": "ask_user_question", "detail": "Rewrite the reader or patch it?"}


def _payload_session(sid, waiting_on=None, state="needs_you"):
    return {"session_id": sid, "short_id": sid, "name": sid, "title": f"title {sid}", "tab": sid[-1], "repo": "ledger",
            "work_repo": "ledger", "state": state, "age_seconds": 60, "has_transcript": True, "waiting_on": waiting_on,
            "last_text": "Working on it.", "head_jeeves": False}


def _tui_session(sid, waiting_on=None):
    return Session(session_id=sid, pid=1, cwd="/x", name=sid, name_source="derived", status="idle",
                   status_updated_at=NOW - timedelta(minutes=5), updated_at=NOW, started_at=NOW - timedelta(hours=1),
                   last_turn="end_turn", last_turn_ts=NOW, waiting_on=waiting_on)


def test_question_and_permission_ask_the_user_and_nothing_else_does():
    for kind in ASKS_USER_KINDS:
        assert asks_user({"kind": kind})
    assert set(ASKS_USER_KINDS) == set(board._ASK_KINDS), "the board's decisions and the orange are one definition"
    for kind in ("subagents_running", "interrupted", None):
        assert not asks_user({"kind": kind})
    assert not asks_user(None)


def test_the_app_token_is_the_same_orange():
    css = (ROOT / "mobile" / "app" / "app.css").read_text()
    assert re.search(r"--waiting:\s*(#[0-9a-fA-F]{6})", css).group(1).lower() == WAITING_ORANGE


def test_the_session_list_draws_an_asking_session_orange():
    rows = {r["session_id"]: r for r in build_rows([_tui_session("ask", QUESTION), _tui_session("calm")], NOW)}
    for key in ("state", "title"):
        cell = rows["ask"][key]
        assert isinstance(cell, Text) and cell.style == WAITING_STYLE
        calm = rows["calm"][key]
        assert not (isinstance(calm, Text) and calm.style == WAITING_STYLE)


def test_the_snapshot_marks_the_line_and_the_session_row():
    payload = {"sessions": [_payload_session("s1", QUESTION), _payload_session("s2", None, state="working")]}
    state = snapshot.build(payload=payload, headlines={}, quota_payload=None, health=None, power=None, docker_up=None,
                           agents=[], crontab_text="", cron_dir=None, runs_json=None, now=NOW)
    decision = state["board"]["sections"]["decision"]
    assert [d["id"] for d in decision] == ["s1"] and decision[0]["asks"] is True
    assert all(not d["asks"] for d in state["board"]["sections"]["progress"])
    rows = state["sessions"]
    assert rows[0]["asks"] is True and rows[0]["line"] == "s1", "asking sessions come first, with their line"
    assert [r["asks"] for r in rows] == [True, False]


def test_the_board_screen_draws_an_asking_line_orange():
    state = {"generated_at": 0, "board": {"summary": "", "sections": {
        "decision": [{"id": "s1", "project": "ledger", "target": "#1 ledger", "text": "Rewrite or patch?", "asks": True}],
        "progress": [{"id": "s2", "project": "atlas", "target": "#2 atlas", "text": "Tile cache", "asks": False}],
    }}, "machine": {}, "accounts": [], "nightly": {"days": [], "jobs": []}}
    text = boardview.render(state, now=0)
    orange = {text.plain[span.start:span.end].strip() for span in text.spans if WAITING_ORANGE in str(span.style)}
    assert "? ledger" in orange and "Rewrite or patch?" in orange
    assert not any("atlas" in s or "Tile cache" in s for s in orange)
