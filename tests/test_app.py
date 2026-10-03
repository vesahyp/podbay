from datetime import datetime

from rich.text import Text

from podbay.app import RECAP_COLUMN, _ctx_cell, _ctx_color, _header_text, build_rows
from podbay.model import Session

NOW = datetime(2026, 9, 9, 12, 0)


def _make_session(*, name="s", context_pct=None) -> Session:
    return Session(
        session_id=name,
        pid=1,
        cwd="/tmp",
        name=name,
        name_source="derived",
        status="idle",
        status_updated_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        last_turn="end_turn",
        context_pct=context_pct,
    )


def test_ctx_color_thresholds():
    assert _ctx_color(0) == "green"
    assert _ctx_color(40) == "green"
    assert _ctx_color(41) == "yellow"
    assert _ctx_color(70) == "yellow"
    assert _ctx_color(71) == "bright_red"
    assert _ctx_color(90) == "bright_red"
    assert _ctx_color(91) == "red"
    assert _ctx_color(100) == "red"


def test_ctx_cell_blank_when_unknown():
    assert _ctx_cell(None) == ""


def test_ctx_cell_renders_percent_with_threshold_colour():
    cell = _ctx_cell(47)
    assert isinstance(cell, Text)
    assert cell.plain == "47%"
    assert cell.style == "yellow"


def test_header_text_colours_percentages_by_the_shared_threshold():
    from podbay.app import HAL_HEADER_GRAY

    segments = [("5H ", None), ("91%", 91.0), ("  ·  ", None), ("7D Opus ", None), ("8%", 8.0)]
    text = _header_text(segments)

    assert text.plain == "5H 91%  ·  7D Opus 8%"
    spans = {(s.start, s.end): s.style for s in text.spans}
    assert spans[(0, 3)] == HAL_HEADER_GRAY  # "5H "
    assert spans[(3, 6)] == _ctx_color(91.0)  # "91%" -> red
    assert spans[(19, 21)] == _ctx_color(8.0)  # "8%" -> green


def test_header_text_colours_projections_by_their_own_tone():
    from podbay.app import PROJECTION_TONE_STYLES

    segments = [("7D ", None), ("17%", 17.0), (" (", None), ("~80% at reset", "ok"), (", ", None), ("out 1d early", "alert"), (")", None)]
    text = _header_text(segments)

    spans = {text.plain[s.start:s.end]: s.style for s in text.spans}
    assert spans["~80% at reset"] == PROJECTION_TONE_STYLES["ok"]  # a fifth to spare is green, not red
    assert spans["out 1d early"] == PROJECTION_TONE_STYLES["alert"]


def test_build_rows_ctx_column_present_and_blank():
    known = _make_session(name="known", context_pct=85)
    unknown = _make_session(name="unknown", context_pct=None)

    rows = build_rows([known, unknown], NOW)
    by_id = {r["session_id"]: r for r in rows}

    assert by_id["known"]["ctx"].plain == "85%"
    assert by_id["known"]["ctx"].style == "bright_red"
    assert by_id["unknown"]["ctx"] == ""


def test_cursor_follows_session_when_rows_resort(monkeypatch, tmp_path):
    """Selection must stick to the session, not the row index, when a refresh re-sorts the table."""
    import asyncio
    from datetime import datetime, timedelta
    from podbay import app as app_mod
    from podbay.model import Session
    from podbay.state import StateStore

    now = datetime.now()

    def make(sid, status, last_turn):
        return Session(
            session_id=sid, pid=1, cwd="/x", name=sid, name_source="derived",
            status=status, status_updated_at=now - timedelta(minutes=5),
            updated_at=now, started_at=now - timedelta(hours=1),
            last_turn=last_turn, last_turn_ts=now,
        )

    batches = [
        [make("a", "idle", "end_turn"), make("b", "idle", "end_turn")],
        [make("a", "busy", "in_progress"), make("b", "idle", "end_turn")],  # a drops below b
    ]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: batches[0])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert application._row_keys[0] == "a"
            selected = application._selected_session()
            assert selected is not None and selected.session_id == "a"
            application._apply_refresh(batches[1], datetime.now())
            await pilot.pause()
            assert application._row_keys[-1] == "a"
            selected = application._selected_session()
            assert selected is not None and selected.session_id == "a"

    asyncio.run(run())


def test_render_transcript_renders_assistant_markdown():
    from io import StringIO

    from rich.console import Console

    from podbay.app import _render_transcript

    entries = [
        {"role": "user", "timestamp": "t", "text": "fix the **thing** in `foo.py`"},
        {"role": "assistant", "timestamp": "t", "text": "Done. **Bold** and `code` render.\n\n- one\n- two"},
        {"role": "tool", "timestamp": "t", "text": "⚙ Bash: ls"},
    ]
    console = Console(file=StringIO(), width=80, force_terminal=True, color_system="truecolor")
    console.print(_render_transcript(entries))
    out = console.file.getvalue()

    assert "▶ VESA  fix the **thing** in `foo.py`" in out  # user text stays literal
    assert "● HAL" in out
    assert "**Bold**" not in out and "`code`" not in out  # markdown consumed
    assert "\x1b[1mBold" in out  # bold escape emitted
    assert "•" in out and "one" in out  # list bullet drawn
    assert "⚙ Bash: ls" in out


def test_hal_markdown_theme_replaces_cyan_code_style():
    from io import StringIO

    from rich.console import Console

    from podbay.app import HAL_MARKDOWN_THEME, _render_transcript

    console = Console(file=StringIO(), width=80, force_terminal=True, color_system="truecolor", theme=HAL_MARKDOWN_THEME)
    console.print(_render_transcript([{"role": "assistant", "timestamp": "t", "text": "run `ls`"}]))
    out = console.file.getvalue()

    assert "\x1b[36m" not in out and "\x1b[1;36" not in out  # no default cyan
    assert "255;176;0" in out  # HAL amber applied to inline code


def test_focusing_transcript_pane_marks_session_seen(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime, timedelta
    from textual.containers import VerticalScroll
    from podbay import app as app_mod
    from podbay.model import Session
    from podbay.state import StateStore

    now = datetime.now()
    session = Session(
        session_id="a", pid=1, cwd="/x", name="a", name_source="derived",
        status="idle", status_updated_at=now - timedelta(minutes=5),
        updated_at=now, started_at=now - timedelta(hours=1),
        last_turn="end_turn", last_turn_ts=now - timedelta(minutes=1),
    )
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(app_mod.sources, "transcript_path_for", lambda *_a, **_k: None)
    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert session.unread is True
            application.query_one("#transcript", VerticalScroll).focus()
            await pilot.pause()
            assert session.unread is False
            assert store.get("a").seen_at is not None

    asyncio.run(run())


def _selection_session(sid, now, **overrides):
    from datetime import timedelta

    from podbay.model import Session

    fields = dict(
        session_id=sid, pid=1, cwd="/x", name=sid, name_source="derived",
        status="idle", status_updated_at=now - timedelta(minutes=5),
        updated_at=now, started_at=now - timedelta(hours=1),
        last_turn="end_turn", last_turn_ts=now,
    )
    fields.update(overrides)
    return Session(**fields)


def test_toggle_selection_marks_and_unmarks_the_highlighted_row(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from textual.widgets import DataTable
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [_selection_session("a", now), _selection_session("b", now)]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            key = application._row_keys[0]
            assert key not in application._selected

            await pilot.press("space")
            await pilot.pause()
            assert key in application._selected
            table = application.query_one("#table", DataTable)
            # the mark lives in the State cell, replacing the status glyph
            cell = table.get_cell(key, application._col_keys[1])
            assert (cell.plain if hasattr(cell, "plain") else cell).startswith(app_mod.SEL_GLYPH)

            await pilot.press("space")
            await pilot.pause()
            assert key not in application._selected
            cell = table.get_cell(key, application._col_keys[1])
            assert not (cell.plain if hasattr(cell, "plain") else cell).startswith(app_mod.SEL_GLYPH)

    asyncio.run(run())


def test_clear_selection_empties_selection_and_marks(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from textual.widgets import DataTable
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [_selection_session("a", now), _selection_session("b", now)]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application._selected = {"a", "b"}
            application.action_clear_selection()
            await pilot.pause()
            assert application._selected == set()
            table = application.query_one("#table", DataTable)
            for key in application._row_keys:
                cell = table.get_cell(key, application._col_keys[1])
                assert not (cell.plain if hasattr(cell, "plain") else cell).startswith(app_mod.SEL_GLYPH)

    asyncio.run(run())


def test_selection_survives_refresh_and_drops_stale_keys(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [_selection_session("a", now), _selection_session("b", now)]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application._selected = {"a", "b"}

            application._apply_refresh(sessions, datetime.now())  # same rows: selection survives
            await pilot.pause()
            assert application._selected == {"a", "b"}

            application._apply_refresh([sessions[1]], datetime.now())  # "a" is gone
            await pilot.pause()
            assert application._selected == {"b"}

    asyncio.run(run())


def test_arrange_excludes_podbays_own_tty_row(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    own = _selection_session("own", now)
    own.tty = "/dev/ttys000"
    own.window_id = "w-own"
    other = _selection_session("other", now)
    other.tty = "/dev/ttys001"
    other.window_id = "w-other"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [own, other])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    calls = []
    monkeypatch.setattr(
        app_mod.iterm_mod,
        "set_window_bounds",
        lambda window_id, bounds: calls.append((window_id, bounds)) or True,
        raising=False,
    )

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        application._own_tty = "/dev/ttys000"
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application._selected = {"own", "other"}
            application.action_arrange()
            await pilot.pause()

    asyncio.run(run())
    assert [call[0] for call in calls] == ["w-other"]


def test_arrange_notifies_when_set_window_bounds_missing(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    other = _selection_session("other", now)
    other.tty = "/dev/ttysXYZ"
    other.window_id = "w-other"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [other])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.delattr(app_mod.iterm_mod, "set_window_bounds", raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        application._own_tty = None
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application._selected = {"other"}
            application.action_arrange()  # must not raise even without iterm.set_window_bounds
            await pilot.pause()

    asyncio.run(run())


def test_open_claude_on_shell_row_sends_into_existing_tty_without_opening_window(monkeypatch, tmp_path):
    import asyncio
    import shlex
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.app import PromptScreen
    from podbay.state import StateStore

    now = datetime.now()
    shell_session = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    shell_session.tty = "/dev/ttys005"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [shell_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    send_calls = []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: send_calls.append((tty, text)) or True)

    def _boom(*_a, **_k):
        raise AssertionError("open_window must not be called for a shell row")

    monkeypatch.setattr(app_mod.iterm_mod, "open_window", _boom, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_open_claude()
            await pilot.pause()
            # the prompt offers the shell's own directory; Enter takes it
            assert isinstance(application.screen, PromptScreen)
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert send_calls == [("/dev/ttys005", f"cd {shlex.quote(shell_session.cwd)} && claude")]


def test_open_claude_elsewhere_prompts_and_opens_window(monkeypatch, tmp_path):
    import asyncio
    import shlex
    from datetime import datetime
    from textual.widgets import Input
    from podbay import app as app_mod
    from podbay.app import PromptScreen
    from podbay.state import StateStore

    now = datetime.now()
    session = _selection_session("a", now)
    session.cwd = "/Users/vesa/Repositories/kafka-infra"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    open_calls = []
    write_calls = []
    monkeypatch.setattr(app_mod.iterm_mod, "open_window", lambda *_a, **_k: open_calls.append(1) or "win-1", raising=False)
    monkeypatch.setattr(
        app_mod.iterm_mod,
        "write_text_to_window",
        lambda window_id, text: write_calls.append((window_id, text)) or True,
        raising=False,
    )

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_open_claude()
            await pilot.pause()
            assert isinstance(application.screen, PromptScreen)
            input_widget = application.screen.query_one("#prompt-input", Input)
            assert input_widget.value == "/Users/vesa/Repositories/kafka-infra"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert open_calls == [1]
    assert write_calls == [("win-1", f"cd {shlex.quote('/Users/vesa/Repositories/kafka-infra')} && claude")]


def test_open_claude_prompts_defaulting_to_home_when_nothing_selected(monkeypatch, tmp_path):
    import asyncio
    import os
    from podbay import app as app_mod
    from podbay.app import PromptScreen
    from podbay.state import StateStore

    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_open_claude()
            await pilot.pause()
            assert isinstance(application.screen, PromptScreen)
            from textual.widgets import Input
            assert application.screen.query_one("#prompt-input", Input).value == os.path.expanduser("~")
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())


def test_resume_modal_lists_both_groups_and_opens_selected_entries(monkeypatch, tmp_path):
    import asyncio
    import shlex
    from dataclasses import dataclass
    from datetime import datetime, timedelta
    from podbay import app as app_mod
    from podbay.app import ResumeScreen
    from podbay.state import StateStore

    now = datetime.now()

    @dataclass
    class FakePastSession:
        session_id: str
        cwd: str
        project_dir: str
        ended_at: datetime
        title: str
        size_bytes: int = 0

    class FakeHistory:
        def list_past_sessions(self, exclude_ids=None, limit=100, since_days=30, **_kw):
            self.exclude_ids = exclude_ids
            return [FakePastSession("hist-b", "/x/hist", "/proj", now - timedelta(days=2), "old task b")]

    fake_history = FakeHistory()
    monkeypatch.setattr(app_mod, "history_mod", fake_history, raising=False)

    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    store = StateStore(tmp_path / "s.json")

    open_calls = []
    write_calls = []
    monkeypatch.setattr(
        app_mod.iterm_mod,
        "open_window",
        lambda command=None, **_k: open_calls.append(command) or f"win-{len(open_calls)}",
        raising=False,
    )
    monkeypatch.setattr(
        app_mod.iterm_mod,
        "write_text_to_window",
        lambda window_id, text: write_calls.append((window_id, text)) or True,
        raising=False,
    )

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            # The app's own automatic refresh (no live sessions here) already
            # wrote an empty snapshot; seed it now, as if this were the first
            # refresh after a reboot finding the on-disk snapshot from before.
            store.set_snapshot(
                [
                    {
                        "session_id": "snap-a",
                        "cwd": "/x/snap",
                        "title": "old task a",
                        "window_id": None,
                        "bounds": None,
                        "seen_at": (now - timedelta(hours=1)).isoformat(),
                    }
                ],
                now,
            )
            application.action_resume()
            await pilot.pause()
            assert isinstance(application.screen, ResumeScreen)
            screen = application.screen
            # entries: [divider "Before the restart", snap-a, divider "Older sessions", hist-b]
            assert [e.get("label", e.get("session_id")) for e in screen._entries] == [
                "Before the restart", "snap-a", "Older sessions", "hist-b",
            ]
            await pilot.press("tab")  # search box has focus by default; move to the table to tick rows
            await pilot.press("space")  # select snap-a (cursor starts on first pickable row)
            await pilot.press("down")  # -> divider
            await pilot.press("down")  # -> hist-b
            await pilot.press("space")  # select hist-b
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    # no terminal was free here, so each entry got a window of its own, with
    # the command handed to the window at creation
    assert set(open_calls) == {
        f"cd {shlex.quote('/x/snap')} && claude --resume {shlex.quote('snap-a')}",
        f"cd {shlex.quote('/x/hist')} && claude --resume {shlex.quote('hist-b')}",
    }
    assert write_calls == []


def test_resume_with_nothing_to_restore_notifies_and_does_not_open_modal(monkeypatch, tmp_path):
    import asyncio
    from podbay import app as app_mod
    from podbay.app import ResumeScreen
    from podbay.state import StateStore

    class FakeHistory:
        def list_past_sessions(self, exclude_ids=None, limit=100, since_days=30, **_kw):
            return []

    monkeypatch.setattr(app_mod, "history_mod", FakeHistory(), raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_resume()
            await pilot.pause()
            assert not isinstance(application.screen, ResumeScreen)

    asyncio.run(run())


def test_fuzzy_score_matches_subsequence_and_prefers_tighter_matches():
    from podbay.app import _fuzzy_score

    assert _fuzzy_score("Partner events GA docs", "pevdocs") is not None
    assert _fuzzy_score("Partner events GA docs", "zzz") is None
    tight = _fuzzy_score("pandas docs", "pandas")
    loose = _fuzzy_score("some other pandas-ish docs", "pandas")
    assert tight < loose


def test_filter_rows_ranks_by_fuzzy_match_quality_then_recency():
    from datetime import datetime, timedelta
    from podbay.app import _filter_rows

    now = datetime(2026, 9, 17, 12, 0)
    rows = [
        {"session_id": "a", "cwd": "/x/repo", "title": "Partner events GA docs", "sort_ts": now - timedelta(days=1)},
        {"session_id": "b", "cwd": "/x/repo", "title": "unrelated task", "sort_ts": now},
        {"session_id": "c", "cwd": "/x/pevdocs-old", "title": "another old task", "sort_ts": now - timedelta(days=5)},
    ]
    # "b" never matches "pevdocs" as a subsequence; "c" has it as a contiguous
    # run in the directory name, a tighter match than "a"'s scattered one.
    assert [r["session_id"] for r in _filter_rows(rows, "pevdocs")] == ["c", "a"]


def test_filter_rows_keeps_recency_order_for_empty_query():
    from datetime import datetime, timedelta
    from podbay.app import _filter_rows

    now = datetime(2026, 9, 17, 12, 0)
    rows = [
        {"session_id": "old", "cwd": "/x", "title": "t", "sort_ts": now - timedelta(days=1)},
        {"session_id": "new", "cwd": "/x", "title": "t", "sort_ts": now},
    ]
    assert _filter_rows(rows, "") == rows


def _seed_resume_snapshot(store, now, entries):
    store.set_snapshot(
        [
            {
                "session_id": e["session_id"], "cwd": e["cwd"], "title": e["title"],
                "window_id": None, "bounds": None, "seen_at": now.isoformat(),
            }
            for e in entries
        ],
        now,
    )


def test_resume_search_input_filters_the_listed_groups_as_you_type(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.app import ResumeScreen
    from podbay.state import StateStore
    from textual.widgets import Input

    now = datetime.now()
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            _seed_resume_snapshot(store, now, [
                {"session_id": "a", "cwd": "/x/repo-a", "title": "fix the flaky kafka test"},
                {"session_id": "b", "cwd": "/x/repo-b", "title": "write the partner docs"},
            ])
            application.action_resume()
            await pilot.pause()
            screen = application.screen
            assert isinstance(screen, ResumeScreen)
            search = screen.query_one("#resume-search", Input)
            assert application.focused is search  # typing goes straight to the search box

            for ch in "kafka":
                await pilot.press(ch)
            await pilot.pause()
            assert [e["session_id"] for e in screen._entries if not e.get("divider")] == ["a"]

    asyncio.run(run())


def test_resume_conversation_matches_merge_in_and_dedupe_against_shown_rows(monkeypatch, tmp_path):
    import asyncio
    from dataclasses import dataclass
    from datetime import datetime, timedelta
    from podbay import app as app_mod
    from podbay.app import ResumeScreen, RESUME_CONVERSATION_LABEL
    from podbay.state import StateStore

    now = datetime.now()

    @dataclass
    class FakeSession:
        session_id: str
        cwd: str
        title: str
        ended_at: datetime

    @dataclass
    class FakeMatch:
        session: FakeSession
        snippet: str
        where: str

    # "a" is already offered via the snapshot group -- a conversation hit on
    # it must not appear twice; "z" is genuinely new and should show up.
    dup_match = FakeMatch(FakeSession("a", "/x/repo-a", "fix the flaky kafka test", now), "kafka test flaked again", "conversation")
    new_match = FakeMatch(FakeSession("z", "/x/other", "some other session", now - timedelta(days=3)), "we discussed kafka lag today", "conversation")

    class FakeHistory:
        def list_past_sessions(self, exclude_ids=None, limit=100, since_days=30, **_kw):
            return []

        def search_sessions(self, query, limit=40, **kwargs):
            return [dup_match, new_match]

    monkeypatch.setattr(app_mod, "history_mod", FakeHistory(), raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            _seed_resume_snapshot(store, now, [
                {"session_id": "a", "cwd": "/x/repo-a", "title": "fix the flaky kafka test"},
            ])
            application.action_resume()
            await pilot.pause()
            screen = application.screen
            assert isinstance(screen, ResumeScreen)

            for ch in "kafka":
                await pilot.press(ch)
            await pilot.pause(app_mod.RESUME_SEARCH_DEBOUNCE + 0.1)  # let the worker fire
            await application.workers.wait_for_complete()
            await pilot.pause()

            labels = [e.get("label", e.get("session_id")) for e in screen._entries]
            ids = [e.get("session_id") for e in screen._entries if not e.get("divider")]
            assert ids.count("a") == 1  # not duplicated into the conversation group
            assert RESUME_CONVERSATION_LABEL in labels
            conv_idx = labels.index(RESUME_CONVERSATION_LABEL)
            assert screen._entries[conv_idx + 1]["session_id"] == "z"

    asyncio.run(run())


def test_resume_escape_clears_query_before_cancelling(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.app import ResumeScreen
    from podbay.state import StateStore
    from textual.widgets import Input

    now = datetime.now()
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            _seed_resume_snapshot(store, now, [
                {"session_id": "a", "cwd": "/x/repo-a", "title": "fix the flaky kafka test"},
            ])
            application.action_resume()
            await pilot.pause()
            screen = application.screen
            search = screen.query_one("#resume-search", Input)

            for ch in "zzz":
                await pilot.press(ch)
            await pilot.pause()
            assert search.value == "zzz"
            assert [e["session_id"] for e in screen._entries if not e.get("divider")] == []

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(application.screen, ResumeScreen)  # first escape only clears the query
            assert search.value == ""
            assert [e["session_id"] for e in screen._entries if not e.get("divider")] == ["a"]

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(application.screen, ResumeScreen)  # second escape cancels the modal

    asyncio.run(run())


def test_resume_space_types_a_space_into_search_not_a_toggle(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.app import ResumeScreen
    from podbay.state import StateStore
    from textual.widgets import Input

    now = datetime.now()
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            _seed_resume_snapshot(store, now, [
                {"session_id": "a", "cwd": "/x/repo-a", "title": "fix the flaky kafka test"},
            ])
            application.action_resume()
            await pilot.pause()
            screen = application.screen
            assert isinstance(screen, ResumeScreen)
            search = screen.query_one("#resume-search", Input)

            for key in ["k", "a", "space", "t", "e", "s", "t"]:
                await pilot.press(key)
            await pilot.pause()
            assert search.value == "ka test"
            assert screen._selected == set()  # the space typed a character; it did not tick the highlighted row

    asyncio.run(run())


def test_apply_refresh_writes_snapshot_excluding_shell_rows(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    claude_session = _selection_session("a", now)
    claude_session.cwd = "/proj/a"
    shell_session = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [claude_session, shell_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    snapshot = store.get_snapshot()
    assert [e["session_id"] for e in snapshot] == ["a"]
    assert snapshot[0]["cwd"] == "/proj/a"
    assert snapshot[0]["seen_at"]


def test_apply_refresh_snapshot_picks_up_window_bounds_from_iterm_cache(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    claude_session = _selection_session("a", now)
    claude_session.window_id = "w1"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [claude_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    class FakeLister:
        _windows = {"w1": SimpleNamespace(bounds=(1, 2, 3, 4))}

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, iterm_lister=FakeLister(), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    snapshot = store.get_snapshot()
    assert snapshot[0]["window_id"] == "w1"
    assert snapshot[0]["bounds"] == [1, 2, 3, 4]


def test_shell_row_rejects_park_without_raising(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.app import PromptScreen
    from podbay.state import StateStore

    now = datetime.now()
    shell_session = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [shell_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_park()  # must not raise and must not open the park prompt
            await pilot.pause()
            assert not isinstance(application.screen, PromptScreen)

    asyncio.run(run())


def test_shell_row_rejects_note_without_raising(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.app import PromptScreen
    from podbay.state import StateStore

    now = datetime.now()
    shell_session = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [shell_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_note()  # must not raise and must not open the note prompt
            await pilot.pause()
            assert not isinstance(application.screen, PromptScreen)

    asyncio.run(run())


def test_build_rows_styles_shell_row_gray():
    from podbay import app as app_mod

    shell = _selection_session("sh", NOW, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell.is_shell = True

    rows = build_rows([shell], NOW)
    row = rows[0]
    for key in ("state", "age", "model", "title", "recap", "parked"):
        cell = row[key]
        # blank cells (e.g. parked) carry no style to render, only non-empty ones do
        if isinstance(cell, Text):
            assert cell.style == app_mod.ROW_STYLES[app_mod.SHELL]


def test_message_on_shell_row_sends_directly_into_its_tty(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from textual.widgets import Input
    from podbay import app as app_mod
    from podbay.app import PromptScreen
    from podbay.state import StateStore

    now = datetime.now()
    shell_session = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    shell_session.tty = "/dev/ttys005"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [shell_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    send_calls = []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: send_calls.append((tty, text)) or True)

    def _boom(*_a, **_k):
        raise AssertionError("get_tty_for_pid must not be called for a shell row")

    monkeypatch.setattr(app_mod.iterm_mod, "get_tty_for_pid", _boom)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_message()
            await pilot.pause()
            assert isinstance(application.screen, PromptScreen)
            # the prompt names the shell terminal, not a Claude session
            assert shell_session.title in application.screen._prompt or shell_session.cwd in application.screen._prompt
            input_widget = application.screen.query_one("#prompt-input", Input)
            input_widget.value = "hello shell"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert send_calls == [("/dev/ttys005", "hello shell")]


def test_message_on_claude_row_still_resolves_tty_from_pid(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from textual.widgets import Input
    from podbay import app as app_mod
    from podbay.app import PromptScreen
    from podbay.state import StateStore

    now = datetime.now()
    session = _selection_session("a", now)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    monkeypatch.setattr(app_mod.iterm_mod, "get_tty_for_pid", lambda pid: "/dev/ttys009")
    send_calls = []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: send_calls.append((tty, text)) or True)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.action_message()
            await pilot.pause()
            assert isinstance(application.screen, PromptScreen)
            assert application.screen._prompt == f"Message to {session.title}:"
            input_widget = application.screen.query_one("#prompt-input", Input)
            input_widget.value = "hello claude"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert send_calls == [("/dev/ttys009", "hello claude")]


def test_transcript_pane_renders_shell_output_for_shell_row(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from textual.widgets import Static
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    shell_session = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    shell_session.tty = "/dev/ttys005"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [shell_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(app_mod.iterm_mod, "read_session_text", lambda tty, **_k: f"output from {tty}")

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            body = application.query_one("#transcript-body", Static)
            captured = []
            monkeypatch.setattr(body, "update", lambda content: captured.append(content))
            application._refresh_transcript(force=True)
            # the read is debounced and runs on a thread, so the first draw is
            # a placeholder and the output only lands after both complete
            await pilot.pause(app_mod.SHELL_READ_DEBOUNCE + 0.1)
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert captured
            text = "".join(
                c.plain if hasattr(c, "plain") else str(c) for c in captured
            )
            assert "output from /dev/ttys005" in text

    asyncio.run(run())


def test_shell_output_is_read_once_while_cached(monkeypatch, tmp_path):
    """Scrolling back onto a shell row must not re-run the 250ms read."""
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    shell_session = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    shell_session.tty = "/dev/ttys005"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [shell_session])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    reads = []
    monkeypatch.setattr(
        app_mod.iterm_mod,
        "read_session_text",
        lambda tty, **_k: reads.append(tty) or f"output from {tty}",
    )

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            for _ in range(3):
                application._refresh_transcript(force=True)
                await pilot.pause(app_mod.SHELL_READ_DEBOUNCE + 0.1)
                await application.workers.wait_for_complete()
            assert reads == ["/dev/ttys005"]

    asyncio.run(run())


def test_podbays_own_terminal_is_not_listed(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    own = _selection_session("own", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    own.is_shell = True
    own.tty = "/dev/ttys000"
    other = _selection_session("other", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    other.is_shell = True
    other.tty = "/dev/ttys001"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [own, other])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        application._own_tty = "/dev/ttys000"
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert application._row_keys == ["other"]

    asyncio.run(run())


def test_open_claude_prefers_a_free_terminal_over_a_new_window(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    claude_row = _selection_session("claude", now)
    claude_row.tty = "/dev/ttys003"
    free = _selection_session("free", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    free.is_shell = True
    free.tty = "/dev/ttys004"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [claude_row, free])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(app_mod.sources, "busy_ttys", lambda *_a, **_k: {"/dev/ttys003"})

    sent = []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: sent.append((tty, text)) or True)
    monkeypatch.setattr(app_mod.iterm_mod, "focus_tty", lambda tty: True)
    windows = []
    monkeypatch.setattr(
        app_mod.iterm_mod, "open_window", lambda *_a, **_k: windows.append("new") or "w1", raising=False
    )

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        application._own_tty = None
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application.query_one("#table").move_cursor(row=0)  # the Claude row, not a shell
            await pilot.pause()
            application.action_open_claude()
            await pilot.pause()
            await pilot.press("enter")  # accept the prompted directory
            await pilot.pause()

    asyncio.run(run())
    assert windows == []
    assert sent and sent[0][0] == "/dev/ttys004"
    assert sent[0][1].endswith("&& claude")


def test_resume_uses_a_free_terminal_before_opening_a_window(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime, timedelta
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    free = _selection_session("free", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    free.is_shell = True
    free.tty = "/dev/ttys004"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [free])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(app_mod.sources, "busy_ttys", lambda *_a, **_k: set())

    sent = []
    windows = []
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: sent.append((tty, text)) or True)
    monkeypatch.setattr(app_mod.iterm_mod, "focus_tty", lambda tty: True)
    monkeypatch.setattr(
        app_mod.iterm_mod, "open_window", lambda command=None, **_k: windows.append(command) or "w1", raising=False
    )

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        application._own_tty = None
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            entry = {
                "session_id": "old-1",
                "cwd": "/Users/vesa/jeeves",
                "title": "t",
                "sort_ts": now - timedelta(hours=2),
            }
            application._resume_entries([entry])
            await pilot.pause()

    asyncio.run(run())
    assert windows == []  # the free terminal was used instead of a new window
    assert sent == [("/dev/ttys004", "cd /Users/vesa/jeeves && claude --resume old-1")]


def test_table_filter_narrows_rows_including_a_fuzzy_non_substring_hit(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from textual.widgets import Input
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [
        _selection_session("a", now, name="fix the flaky kafka test", cwd="/x/repo-a"),
        _selection_session("b", now, name="write the partner docs", cwd="/x/repo-b"),
    ]
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert set(application._row_keys) == {"a", "b"}

            await pilot.press("/")
            await pilot.pause()
            input_widget = application.query_one("#table-filter", Input)
            assert application.focused is input_widget
            assert input_widget.display is True

            # "kfka" is a subsequence of "...flaky kafka..." but never a
            # literal substring -- a genuine fuzzy (non-substring) hit.
            for ch in "kfka":
                await pilot.press(ch)
            await pilot.pause()
            assert application._visible_row_keys == ["a"]
            # the real underlying set is untouched by the filter
            assert set(application._row_keys) == {"a", "b"}

    asyncio.run(run())


def test_table_filter_conversation_hit_from_worker_is_marked(monkeypatch, tmp_path):
    import asyncio
    from dataclasses import dataclass
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()

    @dataclass
    class FakeSession:
        session_id: str

    @dataclass
    class FakeMatch:
        session: FakeSession
        snippet: str
        where: str

    class FakeHistory:
        def search_sessions(self, query, limit=40, **kwargs):
            assert kwargs.get("include_ids") == {"a"}  # shells are never asked
            return [FakeMatch(FakeSession("a"), "we discussed quokkas at length", "conversation")]

    sessions = [_selection_session("a", now, name="totally unrelated title", cwd="/x/repo-a")]
    monkeypatch.setattr(app_mod, "history_mod", FakeHistory(), raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("/")
            for ch in "quokka":
                await pilot.press(ch)
            await pilot.pause()
            # no title/dir match yet -- the fuzzy pass alone finds nothing
            assert application._visible_rows == []

            await pilot.pause(app_mod.RESUME_SEARCH_DEBOUNCE + 0.1)  # let the worker fire
            await application.workers.wait_for_complete()
            await pilot.pause()

            assert application._visible_row_keys == ["a"]
            assert "a" in application._conv_hit_ids
            table = application.query_one("#table", app_mod.DataTable)
            cell = table.get_cell("a", application._col_keys[RECAP_COLUMN])  # Recap column
            text = cell.plain if hasattr(cell, "plain") else cell
            assert text.startswith("[chat]")

    asyncio.run(run())


def test_table_filter_shell_row_still_matches_by_title(monkeypatch, tmp_path):
    """Shell rows have no transcript to search -- they can only ever be
    found through the fuzzy title/directory pass, never lost from it."""
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    shell = _selection_session("sh", now, last_turn=None, last_turn_ts=None, has_transcript=False,
                                name="scratch terminal", cwd="/x/scratch")
    shell.is_shell = True
    other = _selection_session("a", now, name="unrelated claude session", cwd="/x/repo-a")
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [shell, other])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("/")
            for ch in "scratch":
                await pilot.press(ch)
            await pilot.pause()
            assert application._visible_row_keys == ["sh"]

    asyncio.run(run())


def test_table_filter_escape_restores_full_list_and_refocuses_table(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from textual.widgets import DataTable, Input
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [
        _selection_session("a", now, name="fix the flaky kafka test", cwd="/x/repo-a"),
        _selection_session("b", now, name="write the partner docs", cwd="/x/repo-b"),
    ]
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("/")
            for ch in "kafka":
                await pilot.press(ch)
            await pilot.pause()
            assert application._visible_row_keys == ["a"]

            await pilot.press("escape")
            await pilot.pause()
            assert application._filtering is False
            assert set(application._visible_row_keys) == {"a", "b"}
            input_widget = application.query_one("#table-filter", Input)
            assert input_widget.value == ""
            assert input_widget.display is False
            assert application.focused is application.query_one("#table", DataTable)

    asyncio.run(run())


def test_table_filter_survives_a_refresh(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [
        _selection_session("a", now, name="fix the flaky kafka test", cwd="/x/repo-a"),
        _selection_session("b", now, name="write the partner docs", cwd="/x/repo-b"),
    ]
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("/")
            for ch in "kafka":
                await pilot.press(ch)
            await pilot.pause()
            assert application._visible_row_keys == ["a"]

            application._apply_refresh(sessions, datetime.now())
            await pilot.pause()
            assert application._filter_query == "kafka"  # the refresh did not wipe the filter
            assert application._visible_row_keys == ["a"]

    asyncio.run(run())


def test_table_filter_selection_survives_filter_change(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [
        _selection_session("a", now, name="fix the flaky kafka test", cwd="/x/repo-a"),
        _selection_session("b", now, name="write the partner docs", cwd="/x/repo-b"),
    ]
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application._selected = {"a"}
            application._redraw_state_cell("a")

            await pilot.press("/")
            for ch in "partner":
                await pilot.press(ch)
            await pilot.pause()
            assert application._visible_row_keys == ["b"]  # "a" is filtered out of view
            assert application._selected == {"a"}  # but still selected

            await pilot.press("escape")
            await pilot.pause()
            assert application._selected == {"a"}
            table = application.query_one("#table", app_mod.DataTable)
            cell = table.get_cell("a", application._col_keys[1])
            text = cell.plain if hasattr(cell, "plain") else cell
            assert text.startswith(app_mod.SEL_GLYPH)

    asyncio.run(run())


def test_table_filter_no_match_shows_explicit_state(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [_selection_session("a", now, name="fix the flaky kafka test", cwd="/x/repo-a")]
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("/")
            for ch in "zzzzz":
                await pilot.press(ch)
            await pilot.pause()

            assert application._visible_rows == []
            assert application._selected_session() is None
            table = application.query_one("#table", app_mod.DataTable)
            assert table.row_count == 1  # an explicit placeholder, not a silently empty table
            cell = table.get_cell_at((0, app_mod.TITLE_COLUMN))
            text = cell.plain if hasattr(cell, "plain") else cell
            assert text == "no match"

    asyncio.run(run())


def test_table_filter_snapshot_keeps_hidden_session(monkeypatch, tmp_path):
    """The snapshot Resume reads after a reboot must list every live Claude
    session, even one a filter is currently hiding from the table."""
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    sessions = [
        _selection_session("a", now, name="fix the flaky kafka test", cwd="/x/repo-a"),
        _selection_session("b", now, name="write the partner docs", cwd="/x/repo-b"),
    ]
    monkeypatch.setattr(app_mod, "history_mod", None, raising=False)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: sessions)
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    store = StateStore(tmp_path / "s.json")

    async def run():
        application = app_mod.PodbayApp(state_store=store, no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("/")
            for ch in "kafka":
                await pilot.press(ch)
            await pilot.pause()
            assert application._visible_row_keys == ["a"]  # "b" is hidden by the filter

            # a refresh tick while the filter is still active
            application._apply_refresh(sessions, datetime.now())
            await pilot.pause()

    asyncio.run(run())
    snapshot_ids = {e["session_id"] for e in store.get_snapshot()}
    assert snapshot_ids == {"a", "b"}


def test_arrange_uses_the_live_display_frame(monkeypatch, tmp_path):
    """The frame must come from the displays attached right now: arranging
    onto a remembered monitor puts windows off screen once undocked."""
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    a = _selection_session("a", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    a.is_shell, a.tty, a.window_id = True, "/dev/ttys001", "w1"
    b = _selection_session("b", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    b.is_shell, b.tty, b.window_id = True, "/dev/ttys002", "w2"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [a, b])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(
        app_mod.screens,
        "arrange_display",
        lambda: app_mod.screens.Display(display_id=7, bounds=(-810, -1440, 2630, 0), is_main=False),
    )
    calls = []
    monkeypatch.setattr(
        app_mod.iterm_mod, "set_window_bounds", lambda wid, bounds: calls.append((wid, bounds)) or True
    )

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        application._own_tty = None
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            application._selected = {"a", "b"}
            application.action_arrange()
            await pilot.pause()

    asyncio.run(run())
    assert calls == [("w1", (-810, -1440, 910, 0)), ("w2", (910, -1440, 2630, 0))]


def test_open_claude_cursor_follows_the_terminal_onto_the_new_claude_row(monkeypatch, tmp_path):
    """The shell row's key is its tty; the Claude row that replaces it has a
    new session id. The highlight must land on that new row, not on row 0."""
    import asyncio
    from datetime import datetime
    from textual.widgets import DataTable
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    busy = _selection_session("busy", now)
    shell_session = _selection_session("tty:/dev/ttys005", now, last_turn=None, last_turn_ts=None, has_transcript=False)
    shell_session.is_shell = True
    shell_session.tty = "/dev/ttys005"
    sessions = [busy, shell_session]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: list(sessions))
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    monkeypatch.setattr(app_mod.iterm_mod, "send_text", lambda tty, text: True)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            table = application.query_one("#table", DataTable)
            shell_row = application._visible_row_keys.index("tty:/dev/ttys005")
            table.move_cursor(row=shell_row)
            application.action_open_claude()
            await application.workers.wait_for_complete()
            await pilot.pause()

            # Claude registered in that terminal: a new session id, same tty,
            # sorted above the busy session because it is the newest.
            fresh = _selection_session("fresh", now, last_turn=None, last_turn_ts=None, has_transcript=False)
            fresh.tty = "/dev/ttys005"
            sessions[:] = [fresh, busy]
            application.trigger_refresh()
            await application.workers.wait_for_complete()
            await pilot.pause()

            assert application._visible_row_keys[table.cursor_row] == "fresh"
            assert application._follow_tty is None

            # the follow is spent: moving the cursor elsewhere sticks across refreshes
            table.move_cursor(row=application._visible_row_keys.index("busy"))
            application.trigger_refresh()
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert application._visible_row_keys[table.cursor_row] == "busy"

    asyncio.run(run())


def test_redraw_keeps_the_cursor_index_when_the_highlighted_row_vanishes(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime, timedelta
    from textual.widgets import DataTable
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    a = _selection_session("a", now, last_turn_ts=now)
    b = _selection_session("b", now, last_turn_ts=now - timedelta(minutes=1))
    c = _selection_session("c", now, last_turn_ts=now - timedelta(minutes=2))
    sessions = [a, b, c]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: list(sessions))
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            table = application.query_one("#table", DataTable)
            table.move_cursor(row=application._visible_row_keys.index("c"))
            assert table.cursor_row == 2

            sessions[:] = [a, b]
            application.trigger_refresh()
            await application.workers.wait_for_complete()
            await pilot.pause()

            assert table.cursor_row == 1  # the neighbour, not row 0

    asyncio.run(run())


# -- Repos / Wait columns -----------------------------------------------------


def test_build_rows_repos_column_stars_edits_and_marks_shared_repos():
    a = _selection_session("a", NOW, repos_touched=["kafka-infra", "jeeves"], repos_edited=["kafka-infra"])
    b = _selection_session("b", NOW, repos_touched=["kafka-infra"], repos_edited=[])
    solo = _selection_session("c", NOW, repos_touched=["data-platform"], repos_edited=["data-platform"])

    def plain(cell):
        return cell.plain if isinstance(cell, Text) else cell

    rows = build_rows([a, b, solo], NOW)
    by_id = {r["session_id"]: r for r in rows}

    # jeeves dropped (not shared/edited-relevant here), kafka-infra starred
    # (edited) and marked shared (both a and b touch it)
    assert plain(by_id["a"]["repos"]) == "⇄ kafka-infra*"
    assert plain(by_id["b"]["repos"]) == "⇄ kafka-infra"
    # solo touches a repo nobody else does: starred for the edit, no shared marker
    assert plain(by_id["c"]["repos"]) == "data-platform*"


def test_build_rows_wait_column_abbreviates_kinds():
    ask = _selection_session("ask", NOW, waiting_on={"kind": "ask_user_question"})
    perm = _selection_session("perm", NOW, waiting_on={"kind": "permission"})
    question = _selection_session("q", NOW, waiting_on={"kind": "question_text"})
    sub = _selection_session("sub", NOW, waiting_on={"kind": "subagents_running"})
    none_waiting = _selection_session("none", NOW, waiting_on=None)

    def plain(cell):
        return cell.plain if isinstance(cell, Text) else cell

    rows = build_rows([ask, perm, question, sub, none_waiting], NOW)
    by_id = {r["session_id"]: r for r in rows}

    assert plain(by_id["ask"]["wait"]) == "ask"
    assert plain(by_id["perm"]["wait"]) == "perm"
    assert plain(by_id["q"]["wait"]) == "q?"
    assert plain(by_id["sub"]["wait"]) == "sub"
    assert plain(by_id["none"]["wait"]) == ""


# -- cmd_inventory -------------------------------------------------------------


def test_cmd_inventory_status_output_shape(monkeypatch, capsys):
    from podbay import app as app_mod

    session = _selection_session(
        "a", NOW, repos_touched=["data-platform"],
        waiting_on={"kind": "permission", "detail": "Bash: ls /tmp"},
    )
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [session])

    app_mod.cmd_inventory(False, True, [])

    out = capsys.readouterr().out
    assert "waiting on: permission" in out
    assert "Bash: ls /tmp" in out


# -- coordinate / route actions ------------------------------------------------


