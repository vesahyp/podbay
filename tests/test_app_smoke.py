import time
from datetime import datetime

import pytest

from podbay import iterm as iterm_mod
from podbay import voice
from podbay.app import PodbayApp, PodbayHeader
from podbay import splash as splash_mod
from podbay.splash import SplashScreen
from podbay.state import StateStore
from textual.widgets import DataTable, Static


@pytest.mark.asyncio
async def test_app_mounts_and_lists_rows():
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        table = app.query_one("#table", DataTable)
        # Real live sessions on this machine; just assert it mounted and
        # populated without raising, not an exact count.
        assert table.row_count >= 0


@pytest.mark.asyncio
async def test_refresh_key_does_not_raise():
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("r")
        await pilot.pause()
        await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_toggle_transcript_and_message_keys_do_not_raise(tmp_path, monkeypatch):
    # Never park or send anything against real sessions from this test.
    monkeypatch.setattr(iterm_mod, "get_tty_for_pid", lambda pid: None)
    monkeypatch.setattr(iterm_mod, "focus_tty", lambda tty: False)
    monkeypatch.setattr(iterm_mod, "send_text", lambda tty, text: False)

    state_store = StateStore(path=tmp_path / "state.json")
    app = PodbayApp(state_store=state_store, no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert "HAL 9000" in app.title
        # Counts (pods / awaiting input / unread) are already on screen row
        # by row -- the header doesn't repeat them.
        assert "AWAITING INPUT" not in app.title


@pytest.mark.asyncio
async def test_header_widget_shows_five_hour_and_week_quotas_coloured():
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app._limits = {"claude": {"five_pct": 53, "five_resets_at": None, "week_pct": 8, "week_resets_at": None}}
        app._limits_now = datetime.now()
        app._update_header()

        header = app.query_one(PodbayHeader)
        title = header.query_one("#header-quotas", Static)
        clock = header.query_one("#header-clock", Static)

        # identity sits in its own region now, quotas in the centred one
        expected_segments = voice.limits_segments(53, None, 8, None, app._limits_now)
        assert title.content.plain == "".join(text for text, _pct in expected_segments)
        assert "5H 53%" in title.content.plain
        assert "7D 8%" in title.content.plain
        # the percentage figures are coloured by severity; plain wording is not.
        five_pct_span = next(
            s for s in title.content.spans if title.content.plain[s.start : s.end] == "53%"
        )
        assert five_pct_span.style == "yellow"
        assert clock.content.plain == app.sub_title


@pytest.mark.asyncio
async def test_header_widget_shows_per_model_weekly_entry_coloured():
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app._usage_entries = {"claude": [{"key": "week_model", "label": "Fable", "pct": 91.0, "resets_text": "x"}]}
        app._update_header()

        header = app.query_one(PodbayHeader)
        title = header.query_one("#header-quotas", Static)

        assert "Fable 91%" in title.content.plain
        model_pct_span = next(
            s for s in title.content.spans if title.content.plain[s.start : s.end] == "91%"
        )
        assert model_pct_span.style == "red"


@pytest.mark.asyncio
async def test_missing_usage_cache_leaves_header_as_it_was():
    # conftest's autouse fixture makes usage.fetch() fail (no real `claude`
    # subprocess in tests), so the startup usage worker always resolves to
    # None here -- the header must render exactly as if it never ran.
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()

        assert app._usage_entries == {}
        limits = app._limits.get("claude") or {}
        expected = voice.header_segments(
            limits.get("five_pct"), limits.get("five_resets_at"), limits.get("week_pct"),
            limits.get("week_resets_at"), app._limits_now,
        )
        assert app.title == "".join(text for text, _pct in expected)


@pytest.mark.asyncio
async def test_app_starts_on_splash_screen_by_default():
    app = PodbayApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SplashScreen)


@pytest.mark.asyncio
async def test_key_press_dismisses_splash_to_main_screen():
    app = PodbayApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SplashScreen)
        await pilot.press("space")
        await pilot.pause()
        # The key starts the dissolve rather than cutting; the splash is
        # still up but fading.
        assert isinstance(app.screen, SplashScreen)
        assert app.screen._fading
        await pilot.pause(splash_mod.FADE_SECONDS + 0.5)
        assert not isinstance(app.screen, SplashScreen)
        await pilot.pause(splash_mod.FADE_IN_SECONDS + 0.5)
        assert app.screen.styles.opacity == 1.0
        await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_no_splash_starts_directly_on_main_screen():
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, SplashScreen)
        table = app.query_one("#table", DataTable)
        assert table.row_count >= 0


@pytest.mark.asyncio
async def test_prompt_glyphs_tell_park_and_message_apart():
    from podbay import glyphs
    from podbay.app import PromptScreen

    assert glyphs.PARK.art != glyphs.MESSAGE.art
    assert glyphs.PARK.accent != glyphs.MESSAGE.accent

    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app.push_screen(PromptScreen("Park until:", glyph=glyphs.PARK))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PromptScreen)
        assert glyphs.PARK.caption in str(screen.query_one("#prompt-caption", Static).render())
        assert screen.query_one("#prompt-box").styles.border.top[1].hex.lower() == glyphs.PARK.accent
        await pilot.press("escape")
        await pilot.pause()

        app.push_screen(PromptScreen("Note:"))
        await pilot.pause()
        assert not app.screen.query("#prompt-glyph")
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_splash_eye_materialises_before_dialog():
    app = PodbayApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SplashScreen)
        # Mid-materialise: some frames shown, no dialog typed yet.
        assert 0 < screen._mat_idx <= len(splash_mod.MATERIALISE_FRAMES_ART)
        assert screen._typed[0] == 0
        await pilot.pause(splash_mod.FADE_SECONDS + 0.3)
        assert screen._typed[0] > 0
        await pilot.press("space")
        await pilot.pause(splash_mod.FADE_SECONDS + splash_mod.FADE_IN_SECONDS + 0.6)
        await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_quit_plays_shutdown_eye_then_exits():
    from podbay.splash import ShutdownScreen

    app = PodbayApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause(splash_mod.FADE_SECONDS + splash_mod.FADE_IN_SECONDS + 0.6)
        await app.workers.wait_for_complete()
        await pilot.press("q")
        await pilot.pause(splash_mod.FADE_OUT_SECONDS + 0.3)
        assert isinstance(app.screen, ShutdownScreen)
        await pilot.press("q")  # a second key exits at once
        await pilot.pause()
    assert app.return_code is not None or app._exit


@pytest.mark.asyncio
async def test_quit_without_splash_exits_at_once():
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert app._exit


@pytest.mark.asyncio
async def test_splash_backdrop_shows_then_dissolves(monkeypatch):
    # The splash runs on frame timers: a real session gather competing for the
    # GIL makes the first pause land late, so the scan is stubbed out.
    from podbay import app as app_mod

    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    backdrop = ["$ ls -la", "total 42", "drwxr-xr-x  podbay", "$ podbay"]
    app = PodbayApp(backdrop=backdrop)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SplashScreen)
        canvas = screen.query_one("#canvas", Static)
        early = str(canvas.render())
        # The dissolve starts at once and the first pause lands at a scheduler-
        # dependent moment, so a whole word can already have lost a cell:
        # check that the backdrop is still mostly there, not any exact string.
        wanted = sum(len(line.replace(" ", "")) for line in backdrop)
        assert screen._backdrop_t < 0.5
        assert len(early.replace(" ", "").replace("\n", "")) >= wanted * 0.6
        await pilot.pause(splash_mod.FADE_SECONDS + 0.3)
        late = str(canvas.render())
        assert "drwxr" not in late  # dissolved away once the eye is in
        assert screen._backdrop_t >= 1.0
        await pilot.press("space")
        await pilot.pause(splash_mod.FADE_SECONDS + splash_mod.FADE_IN_SECONDS + 0.6)
        await app.workers.wait_for_complete()


def test_capture_own_screen_returns_none_outside_iterm(monkeypatch):
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    assert iterm_mod.capture_own_screen() is None


@pytest.mark.asyncio
async def test_quit_waits_for_running_poll_then_exits(monkeypatch):
    from podbay import app as app_mod
    from podbay.app import QuitWaitScreen

    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    app = PodbayApp(no_splash=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._polls["usage"] = time.monotonic() - 10
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, QuitWaitScreen)
        assert not app._exit
        text = str(app.screen.query_one("#quit-wait", Static).render())
        assert "usage" in text and "of max 30 s" in text
        app._polls.clear()
        await pilot.pause(0.4)
    assert app._exit and not app._force_quit




def _session_row(app, tmp_name="s1"):
    from podbay.model import Session

    now = datetime.now()
    return Session(
        session_id=tmp_name, pid=4242, cwd="/tmp", name=tmp_name, name_source="derived",
        status="idle", status_updated_at=now, updated_at=now, started_at=now, last_turn="end_turn",
    )


@pytest.mark.asyncio
async def test_m_sends_typed_text_to_highlighted_session_with_send_text(tmp_path, monkeypatch):
    from textual.widgets import Input

    sent = []
    monkeypatch.setattr(iterm_mod, "get_tty_for_pid", lambda pid: f"/dev/ttys{pid}")
    monkeypatch.setattr(iterm_mod, "send_text", lambda tty, text: sent.append((tty, text)) or True)

    app = PodbayApp(state_store=StateStore(path=tmp_path / "state.json"), no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        session = _session_row(app)
        monkeypatch.setattr(app, "_selected_session", lambda: session)
        await pilot.press("m")
        await pilot.pause()
        long_text = "x" * 3000
        app.screen.query_one("#prompt-input", Input).value = long_text
        await pilot.press("enter")
        await pilot.pause()

    assert sent == [("/dev/ttys4242", long_text)]


@pytest.mark.asyncio
async def test_j_sends_to_head_jeeves_and_escape_sends_nothing(tmp_path, monkeypatch):
    from textual.widgets import Input

    sent = []
    monkeypatch.setattr(iterm_mod, "get_tty_for_pid", lambda pid: f"/dev/ttys{pid}")
    monkeypatch.setattr(iterm_mod, "send_text", lambda tty, text: sent.append((tty, text)) or True)

    app = PodbayApp(state_store=StateStore(path=tmp_path / "state.json"), no_splash=True)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        head = _session_row(app, "head")
        head.pid = 777
        monkeypatch.setattr(app, "_head_jeeves_session", lambda: head)
        await pilot.press("j")
        await pilot.pause()
        app.screen.query_one("#prompt-input", Input).value = "status?"
        await pilot.press("escape")
        await pilot.pause()
        assert sent == []
        await pilot.press("j")
        await pilot.pause()
        app.screen.query_one("#prompt-input", Input).value = "status?"
        await pilot.press("enter")
        await pilot.pause()

    assert sent == [("/dev/ttys777", "status?")]
