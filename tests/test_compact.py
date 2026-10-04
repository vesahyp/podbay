"""Head Jeeves is the session the phone is bridged to, so a full context is
compacted in place from the background loop, never replaced."""

from datetime import datetime, timedelta

from podbay import app as app_mod
from podbay import config
from podbay.model import HEAD_JEEVES_NAME, Session
from podbay.state import StateStore

NOW = datetime(2026, 10, 4, 12, 0, 0)


def _head(**over) -> Session:
    base = dict(
        session_id="hj", pid=1, cwd="/x", name=HEAD_JEEVES_NAME, name_source="user",
        status="idle", status_updated_at=NOW - timedelta(minutes=5),
        updated_at=NOW, started_at=NOW - timedelta(hours=3),
        last_turn="end_turn", last_turn_ts=NOW - timedelta(minutes=2), context_pct=72.0,
    )
    base.update(over)
    return Session(**base)


def test_compact_is_due_at_the_threshold_when_idle():
    assert app_mod.compact_due(_head(), NOW, 60, None, None)
    assert app_mod.compact_due(_head(context_pct=60), NOW, 60, None, None)
    assert not app_mod.compact_due(_head(context_pct=59.9), NOW, 60, None, None)
    assert not app_mod.compact_due(_head(context_pct=None), NOW, 60, None, None)
    assert not app_mod.compact_due(_head(context_pct=99), NOW, 0, None, None)  # off


def test_never_while_busy_or_with_something_open():
    assert not app_mod.compact_due(_head(last_turn="in_progress"), NOW, 60, None, None)
    assert not app_mod.compact_due(_head(last_turn=None), NOW, 60, None, None)
    assert not app_mod.compact_due(_head(subagents_running=1), NOW, 60, None, None)
    waiting = _head(status="waiting", status_updated_at=NOW - timedelta(minutes=1))
    assert waiting.awaiting_prompt and not app_mod.compact_due(waiting, NOW, 60, None, None)
    task = _head(background_tasks=[{"id": "t", "ts": NOW - timedelta(minutes=1), "ended": False}])
    assert not app_mod.compact_due(task, NOW, 60, None, None)


def test_never_while_a_command_podbay_sent_is_unanswered():
    sent_after_his_turn = NOW - timedelta(minutes=1)
    assert not app_mod.compact_due(_head(), NOW, 60, None, sent_after_his_turn)
    sent_before_his_turn = NOW - timedelta(minutes=10)
    assert app_mod.compact_due(_head(), NOW, 60, None, sent_before_his_turn)


def test_at_most_once_per_thirty_minutes():
    assert not app_mod.compact_due(_head(), NOW, 60, NOW - timedelta(minutes=29), None)
    assert app_mod.compact_due(_head(), NOW, 60, NOW - timedelta(minutes=30), None)


def test_the_compact_command_is_one_line_that_fits_a_tty_write():
    assert app_mod.HEAD_JEEVES_COMPACT.startswith("/compact ")
    assert "\n" not in app_mod.HEAD_JEEVES_COMPACT
    assert len(app_mod.HEAD_JEEVES_COMPACT.encode()) < 1000
    for kept in ("board artifact URL", "decision", "not tested", "session", "quiet hours", "skill and memory"):
        assert kept in app_mod.HEAD_JEEVES_COMPACT


def test_config_threshold_default_off_and_clamped(tmp_path):
    path = tmp_path / "config.json"
    assert config.head_jeeves_compact_at(path) == 60
    config.set_value("head-jeeves-compact-at", "75", path)
    assert config.head_jeeves_compact_at(path) == 75
    config.set_value("head-jeeves-compact-at", "off", path)
    assert config.head_jeeves_compact_at(path) == 0
    config.set_value("head-jeeves-compact-at", "140", path)
    assert config.head_jeeves_compact_at(path) == 100
    config.set_value("head-jeeves-compact-at", "lots", path)
    assert config.head_jeeves_compact_at(path) == 60


def test_the_loop_compacts_an_idle_full_head_jeeves_once(monkeypatch, tmp_path):
    """One refresh over the threshold sends /compact; the next ones inside
    30 minutes, and one where he is busy, send nothing."""
    import asyncio

    now = datetime.now()
    idle = _head(status_updated_at=now - timedelta(minutes=5), updated_at=now, started_at=now - timedelta(hours=3),
                 last_turn_ts=now - timedelta(minutes=2))
    busy = _head(status="busy", status_updated_at=now, updated_at=now, started_at=now - timedelta(hours=3),
                 last_turn="in_progress", last_turn_ts=now)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [idle])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    sent: list[tuple[str, str]] = []

    async def run():
        application = app_mod.PodbayApp(
            state_store=StateStore(tmp_path / "s.json"), no_splash=True, head_jeeves=True, head_jeeves_compact_at=60,
        )
        monkeypatch.setattr(application, "_send_to_session", lambda s, text: sent.append((s.name, text)) or True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert sent == [(HEAD_JEEVES_NAME, app_mod.HEAD_JEEVES_COMPACT)]
            application._apply_refresh([idle], datetime.now() + timedelta(minutes=5))
            application._apply_refresh([busy], datetime.now() + timedelta(minutes=40))
            await pilot.pause()
            assert len(sent) == 1
            later = _head(status_updated_at=now - timedelta(minutes=5), updated_at=now, started_at=now - timedelta(hours=3),
                          last_turn_ts=datetime.now() + timedelta(minutes=41))
            application._apply_refresh([later], datetime.now() + timedelta(minutes=42))
            await pilot.pause()
            assert len(sent) == 2

    asyncio.run(run())
