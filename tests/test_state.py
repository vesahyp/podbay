from datetime import datetime, timedelta

from podbay.state import StateStore


NOW = datetime(2026, 9, 17, 12, 0)


def _entry(sid="a", **overrides):
    entry = {
        "session_id": sid,
        "cwd": "/x/y",
        "title": f"session {sid}",
        "window_id": "w1",
        "bounds": [0, 0, 100, 100],
        "seen_at": NOW.isoformat(),
    }
    entry.update(overrides)
    return entry


def test_get_snapshot_empty_by_default(tmp_path):
    store = StateStore(tmp_path / "s.json")
    assert store.get_snapshot() == []


def test_set_snapshot_round_trips_in_memory(tmp_path):
    store = StateStore(tmp_path / "s.json")
    entries = [_entry("a"), _entry("b")]
    store.set_snapshot(entries, NOW)
    assert store.get_snapshot() == entries


def test_set_snapshot_replaces_wholesale(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.set_snapshot([_entry("a"), _entry("b")], NOW)
    store.set_snapshot([_entry("c")], NOW + timedelta(minutes=5))
    assert [e["session_id"] for e in store.get_snapshot()] == ["c"]


def test_snapshot_persists_across_reload(tmp_path):
    path = tmp_path / "s.json"
    store = StateStore(path)
    store.set_snapshot([_entry("a"), _entry("b")], NOW)

    reloaded = StateStore(path)
    assert [e["session_id"] for e in reloaded.get_snapshot()] == ["a", "b"]


def test_prune_does_not_remove_snapshot_entries(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.set_snapshot([_entry("a"), _entry("b")], NOW)
    # Neither "a" nor "b" is live, and both are old enough that a park/note
    # entry with the same id would be pruned -- the snapshot must survive.
    store.set_note("a", "stale note")
    store._sessions["a"].updated_at = NOW - timedelta(days=30)
    removed = store.prune(set(), NOW)
    assert removed == 1  # the stale note entry for "a", not the snapshot
    assert [e["session_id"] for e in store.get_snapshot()] == ["a", "b"]


def test_prune_leaves_snapshot_even_with_no_live_sessions(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.set_snapshot([_entry("a")], NOW)
    store.prune(set(), NOW + timedelta(days=100))
    assert [e["session_id"] for e in store.get_snapshot()] == ["a"]


def test_old_state_file_without_snapshot_key_loads_cleanly(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"version": 1, "sessions": {}}')
    store = StateStore(path)
    assert store.get_snapshot() == []


def test_empty_live_set_keeps_the_previous_snapshot(tmp_path):
    """The first refresh after a reboot sees no live sessions; overwriting
    then would destroy exactly what Resume needs."""
    import asyncio
    from datetime import datetime
    from podbay import app as app_mod
    from podbay.state import StateStore

    store = StateStore(tmp_path / "s.json")
    now = datetime.now()
    store.set_snapshot([{"session_id": "old", "cwd": "/Users/vesa/jeeves", "title": "before reboot"}], now)

    application = app_mod.PodbayApp(state_store=store, no_splash=True)
    application.rows = []
    application._write_snapshot(now)

    assert [e["session_id"] for e in store.get_snapshot()] == ["old"]
