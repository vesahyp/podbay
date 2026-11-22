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
    for kept in ("decision", "not tested", "session", "quiet hours", "skill and memory"):
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


def test_a_round_starts_with_the_precompact_when_idle():
    assert app_mod.compact_step(_head(), NOW, 60, None, None, None) == "precompact"
    assert app_mod.HEAD_JEEVES_PRECOMPACT == "/head-jeeves precompact"


def test_no_precompact_while_busy_or_below_the_threshold():
    busy = _head(status="busy", last_turn="in_progress", last_turn_ts=NOW - timedelta(seconds=10))
    assert app_mod.compact_step(busy, NOW, 60, None, None, None) is None
    assert app_mod.compact_step(_head(subagents_running=1), NOW, 60, None, None, None) is None
    assert app_mod.compact_step(_head(context_pct=40), NOW, 60, None, None, None) is None
    assert app_mod.compact_step(_head(), NOW, 60, NOW - timedelta(minutes=10), None, None) is None


def test_compact_waits_until_he_has_answered_the_precompact():
    sent = NOW - timedelta(minutes=5)
    still_on_it = _head(status="busy", last_turn="in_progress", last_turn_ts=NOW - timedelta(seconds=10))
    assert app_mod.compact_step(still_on_it, NOW, 60, None, sent, sent) is None
    # idle, but the turn is older than the precompact: not answered yet
    assert app_mod.compact_step(_head(last_turn_ts=sent - timedelta(minutes=1)), NOW, 60, None, sent, sent) is None
    answered = _head(last_turn_ts=sent + timedelta(minutes=3))
    assert app_mod.compact_step(answered, NOW, 60, None, sent, sent) == "compact"
    # an event typed in after the precompact must be answered too
    event_at = sent + timedelta(minutes=4)
    assert app_mod.compact_step(answered, NOW, 60, None, event_at, sent) is None


def test_the_compact_goes_whatever_the_context_use_after_the_precompact():
    sent = NOW - timedelta(minutes=5)
    answered = _head(context_pct=30, last_turn_ts=sent + timedelta(minutes=3))
    assert app_mod.compact_step(answered, NOW, 60, None, sent, sent) == "compact"


def test_the_round_is_dropped_past_the_time_limit():
    sent = NOW - app_mod.HEAD_JEEVES_PRECOMPACT_LIMIT
    still_on_it = _head(status="busy", last_turn="in_progress", last_turn_ts=NOW - timedelta(seconds=10))
    assert app_mod.compact_step(still_on_it, NOW, 60, None, sent, sent) == "give-up"
    just_inside = NOW - app_mod.HEAD_JEEVES_PRECOMPACT_LIMIT + timedelta(seconds=1)
    assert app_mod.compact_step(still_on_it, NOW, 60, None, just_inside, just_inside) is None
    # answered at the limit: compact, not give up
    answered = _head(last_turn_ts=sent + timedelta(minutes=19))
    assert app_mod.compact_step(answered, NOW, 60, None, sent, sent) == "compact"


def test_the_loop_runs_precompact_then_compact(monkeypatch, tmp_path):
    """Over the threshold and idle: the precompact; nothing while he works
    on it; the /compact once he has answered; then nothing for 30 minutes."""
    import asyncio

    now = datetime.now()
    kw = dict(updated_at=now, started_at=now - timedelta(hours=3))
    idle = _head(status_updated_at=now - timedelta(minutes=5), last_turn_ts=now - timedelta(minutes=2), **kw)
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
            assert sent == [(HEAD_JEEVES_NAME, app_mod.HEAD_JEEVES_PRECOMPACT)]
            t = datetime.now()
            busy = _head(status="busy", status_updated_at=t, last_turn="in_progress", last_turn_ts=t, **kw)
            application._apply_refresh([busy], t + timedelta(minutes=2))
            application._apply_refresh([idle], t + timedelta(minutes=3))  # his turn from before
            await pilot.pause()
            assert len(sent) == 1
            done = _head(status_updated_at=t, last_turn_ts=t + timedelta(minutes=4), **kw)
            application._apply_refresh([done], t + timedelta(minutes=5))
            await pilot.pause()
            assert sent[1:] == [(HEAD_JEEVES_NAME, app_mod.HEAD_JEEVES_COMPACT)]
            later = _head(status_updated_at=t, last_turn_ts=t + timedelta(minutes=6), **kw)
            application._apply_refresh([later], t + timedelta(minutes=20))
            await pilot.pause()
            assert len(sent) == 2

    asyncio.run(run())


def test_the_loop_drops_a_precompact_he_does_not_answer(monkeypatch, tmp_path):
    import asyncio

    now = datetime.now()
    kw = dict(updated_at=now, started_at=now - timedelta(hours=3))
    idle = _head(status_updated_at=now - timedelta(minutes=5), last_turn_ts=now - timedelta(minutes=2), **kw)
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [idle])
    monkeypatch.setattr(app_mod.sources, "read_status_snapshots", lambda *_a, **_k: {}, raising=False)
    sent: list[str] = []

    async def run():
        application = app_mod.PodbayApp(
            state_store=StateStore(tmp_path / "s.json"), no_splash=True, head_jeeves=True, head_jeeves_compact_at=60,
        )
        monkeypatch.setattr(application, "_send_to_session", lambda s, text: sent.append(text) or True)
        async with application.run_test(size=(140, 40)) as pilot:
            await application.workers.wait_for_complete()
            await pilot.pause()
            assert sent == [app_mod.HEAD_JEEVES_PRECOMPACT]
            t = datetime.now()
            busy = _head(status="busy", status_updated_at=t, last_turn="in_progress", last_turn_ts=t, **kw)
            application._apply_refresh([busy], t + timedelta(minutes=21))
            await pilot.pause()
            assert sent == [app_mod.HEAD_JEEVES_PRECOMPACT]  # dropped, no /compact behind his turn
            assert application._head_jeeves_precompact_at is None
            done = _head(status_updated_at=t, last_turn_ts=t + timedelta(minutes=22), **kw)
            application._apply_refresh([done], t + timedelta(minutes=23))
            await pilot.pause()
            assert sent == [app_mod.HEAD_JEEVES_PRECOMPACT]  # the next round waits 30 minutes
            application._apply_refresh([done], t + timedelta(minutes=52))
            await pilot.pause()
            assert sent == [app_mod.HEAD_JEEVES_PRECOMPACT] * 2

    asyncio.run(run())


def test_excerpt_since_compact_reads_only_what_came_after_it(tmp_path):
    """The precompact's reviewer reads Head Jeeves' transcript since his last compact."""
    import json

    from podbay import reviewer

    def user(ts, text, **over):
        return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}, **over}

    def agent(ts, text):
        return {"type": "assistant", "timestamp": ts,
                "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}

    path = tmp_path / "hj.jsonl"
    records = [
        user("2026-11-22T08:00:00.000Z", "old order"), agent("2026-11-22T08:01:00.000Z", "old answer"),
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-22T09:00:00.000Z"},
        user("2026-11-22T09:00:00.000Z", "summary of the old part", isCompactSummary=True),
        user("2026-11-22T10:00:00.000Z", "new order"), agent("2026-11-22T10:01:00.000Z", "new answer"),
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    since = reviewer.excerpt(path, since_compact=True)
    assert "new order" in since and "new answer" in since
    assert "old" not in since and "summary" not in since
    assert "old order" in reviewer.excerpt(path)  # the plain excerpt is unchanged
