from datetime import datetime, timedelta

from podbay.state import PRUNE_AFTER_DAYS, StateStore


NOW = datetime(2026, 9, 17, 12, 0)


def test_seen_at_round_trips_through_the_file(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.set_seen("a", NOW)
    assert StateStore(tmp_path / "state.json").get("a").seen_at == NOW
    assert store.get("missing").seen_at is None


def test_prune_drops_only_stale_entries_of_dead_sessions(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.set_seen("live-old", NOW - timedelta(days=PRUNE_AFTER_DAYS + 1))
    store.set_seen("dead-old", NOW - timedelta(days=PRUNE_AFTER_DAYS + 1))
    store.set_seen("dead-fresh", NOW - timedelta(days=1))
    assert store.prune({"live-old"}, NOW) == 1
    reloaded = StateStore(tmp_path / "state.json")
    assert reloaded.get("live-old").seen_at is not None
    assert reloaded.get("dead-fresh").seen_at is not None
    assert reloaded.get("dead-old").seen_at is None
