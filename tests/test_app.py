from datetime import datetime

from rich.text import Text

from podbay.app import _ctx_cell, _ctx_color, _header_text, build_rows
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

    assert "▶ DAVE  fix the **thing** in `foo.py`" in out  # user text stays literal
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


def test_redraw_keeps_the_cursor_index_when_the_highlighted_row_vanishes(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime, timedelta
    from textual.widgets import DataTable
    from podbay import app as app_mod
    from podbay.state import StateStore

    now = datetime.now()
    a = _selection_session("a", now, last_turn_ts=now - timedelta(minutes=2))
    b = _selection_session("b", now, last_turn_ts=now - timedelta(minutes=1))
    c = _selection_session("c", now, last_turn_ts=now)
    sessions = [a, b, c]
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: list(sessions))
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)

    async def run():
        application = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            table = application.query_one("#table", DataTable)
            table.move_cursor(row=application._row_keys.index("c"))
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


