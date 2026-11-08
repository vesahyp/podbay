"""The board screen draws the same state.json the phone app reads."""

import pytest

from podbay import boardview

NOW = 1_794_150_000.0

STATE = {
    "generated_at": NOW - 90,
    "mac": "Toren",
    "board": {"summary": "1 decision waits for you.", "sections": {
        "decision": [{"id": "s1", "project": "sora", "target": "#6 sora", "text": "Start line or checkpoint?",
                      "options": ["Start line", "Checkpoint"], "link": None, "steps": []}],
        "shipped": [{"id": "shipped:x", "project": "podbay", "target": "podbay (session ended)", "text": "App live",
                     "link": "https://app.example.test", "steps": ["Open it", "Tap Nightly"]}],
        "progress": [{"id": "s2", "project": "atlas", "target": "#3 atlas", "text": "Tile cache", "when": "working now"}],
        "finished": [],
    }},
    "machine": {"load": 2.0, "cores": 8, "overload_at": 16.0, "mem_free_mb": 900, "mem_low_mb": 400,
                "disk_free_gb": 10, "disk_low_gb": 15, "overloaded": None, "docker_up": False,
                "power": {"source": "battery", "percent": 64}, "history": [{"at": NOW - 600, "load": 1.0}, {"at": NOW, "load": 4.0}]},
    "accounts": [{"account": "claude", "logged_out": False, "login_ends_at": NOW + 3 * 86400, "suggested": True,
                  "windows": [{"name": "5 hours", "pct": 56.0, "resets_at": "2026-11-08T16:40:00+00:00"}]},
                 {"account": "other", "logged_out": True, "windows": []}],
    "nightly": {"days": ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08"],
                "jobs": [{"label": "keitos", "at": "07:00", "cells": [{"state": s} for s in
                                                                    ("ok", "ok", "fail", "miss", "skip", "unknown", "ok")]}]},
    "runs": {"generated_at": "2026-11-08T10:45:00+02:00", "count": 3,
             "phases": ["startup", "metastore", "spark", "reads", "transform", "writes", "stats", "nodes", "bookkeeping"],
             "nights": [{"d": "2026-11-07", "n": 0, "f": 0, "p": 0, "v": []},
                        {"d": "2026-11-08", "n": 2, "f": 1, "p": 2, "v": [1, 0, 1, 2, 0, 1, 4, 0, 1]}],
             "pipelines": [{"name": "keitos-traffic · traffic", "latest_ms": 600000, "median_ms": 180000, "median_of": 2,
                            "runs": [{"at": "2026-11-08 04:00", "f": 1, "v": [1, 0, 1, 2, 0, 1, 4, 0, 1]}]}]},
}


def test_render_has_every_section_of_the_app():
    text = boardview.render(STATE, [], now=NOW).plain
    for heading in ("DECISIONS FOR YOU", "READY FOR YOU TO TEST", "IN PROGRESS", "THREAD WITH HEAD JEEVES",
                    "MACHINE", "NIGHTLY JOBS", "PIPELINE TIME"):
        assert heading in text
    assert "Toren · made 2 min ago" in text
    assert "[Start line]  [Checkpoint]" in text and "1. Open it" in text and "working now" in text
    assert "battery 64%" in text and "disk 10 GB free" in text and "Docker down" in text
    assert "login ends in 3 d" in text and "logged out, run /login" in text and "56%" in text
    assert "■  ■  ■  □  ■  ░  ■  07:00" in text
    assert "last night 10 min, 2 runs, 1 failed" in text and "keitos-traffic · traffic" in text
    assert "median 3.0" in text and "failed" in text


def test_render_without_messages_leaves_the_thread_out_and_without_state_says_so():
    assert "THREAD" not in boardview.render(STATE, None, now=NOW).plain
    assert "No state yet" in boardview.render(None).plain


def test_the_thread_is_the_chat_in_time_order():
    """docs/adr/0002 Update 2026-11-08: oldest first, each item at its own
    time, an answer at replied_at, a line reply once and without its answer."""
    msgs = [
        {"sk": "3", "line": "s1", "target": "#6 sora", "text": "Start line", "state": "answered",
         "created": NOW - 300, "reply": "Told sora: start line.", "replied_at": NOW - 200},
        {"sk": "2", "from": "head-jeeves", "text": "Disk cleaned.", "state": "said", "created": NOW - 500},
        {"sk": "1", "line": "head-jeeves", "target": "head-jeeves", "text": "What runs tonight?", "state": "answered",
         "created": NOW - 600, "reply": "The usual sweep from 01:00.", "replied_at": NOW - 100},
    ]
    text = boardview.render(STATE, msgs, now=NOW).plain
    order = [text.index(x) for x in ("What runs tonight?", "Disk cleaned.", "on #6 sora: Start line", "The usual sweep from 01:00.")]
    assert order == sorted(order)
    assert "Told sora: start line." not in text


def test_spark_scales_and_leaves_zero_empty():
    assert boardview.spark([0, 1, 2, 4]) == " ▃▅█"


@pytest.mark.asyncio
async def test_the_b_key_opens_the_board_screen(monkeypatch):
    from podbay import app as app_mod
    from podbay.app import BoardScreen, PodbayApp

    monkeypatch.setattr(app_mod, "gather_snapshot", lambda now=None: STATE)
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("b")
        await pilot.pause()
        assert isinstance(app.screen, BoardScreen)
        body = app.screen.query_one("#board-body")
        assert "DECISIONS FOR YOU" in str(body.render())
        assert app_mod.SNAPSHOT_PATH.exists()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, BoardScreen)
