from datetime import datetime, timedelta

from podbay import board, hal, inventory, overload, paused
from podbay.model import Session

NOW = datetime(2026, 10, 6, 7, 5)
T = NOW.timestamp()
HEALTH = {"mem_free_mb": 41, "swap_used_mb": 6374}


def _points(loads, cpu=80.0, free=900):
    """One sample a minute, the newest at NOW."""
    return [{"at": T - 60 * (len(loads) - 1 - i), "load": v, "cpu": cpu, "free": free, "swap": 6000} for i, v in enumerate(loads)]


def _session(sid, *, working=True, started_hours=1, recap="building the board"):
    ts = NOW - timedelta(minutes=1)
    return Session(
        session_id=sid, pid=1, cwd="/x/podbay", name=sid, name_source="derived", status="busy",
        status_updated_at=ts, updated_at=ts, started_at=NOW - timedelta(hours=started_hours),
        last_turn="in_progress" if working else "end_turn", last_turn_ts=ts, iterm_title=sid.upper(), recap=recap,
    )


def test_sustained_cpu_load_is_an_overload_and_a_spike_is_not():
    verdict, reason = overload.assess(_points([43, 44, 46, 58, 46, 30]), 8, T)
    assert verdict == overload.HIGH and reason == "load average 44 on 8 cores for 5 min, cpu 80%"
    assert overload.assess(_points([37, 24, 21, 14, 10, 17]), 8, T) == (None, None)  # last night, 06:15 on
    assert overload.assess(_points([10, 9, 12, 15, 11, 8]), 8, T) == (overload.NORMAL, None)


def test_low_free_memory_and_swap_never_trigger_it():
    assert overload.assess(_points([5, 6, 4, 6, 5, 3], cpu=40.0, free=20), 8, T) == (overload.NORMAL, None)


def test_high_load_with_idle_cpu_is_not_an_overload():
    assert overload.assess(_points([30, 30, 30, 30, 30, 30], cpu=20.0), 8, T) == (None, None)


def test_too_few_samples_give_no_verdict():
    assert overload.assess(_points([60, 60]), 8, T) == (None, None)


def test_one_event_per_episode_only_with_two_working_and_a_clear_after_it():
    busy = [_session("a"), _session("b", started_hours=2)]
    command, state = overload.step({}, overload.HIGH, "load average 45 on 8 cores for 5 min", busy[:1], {}, HEALTH, NOW)
    assert command is None and state["episode"]  # one session working: no message
    command, state = overload.step(state, overload.HIGH, "load average 45 on 8 cores for 5 min", busy, {}, HEALTH, NOW)
    assert command.startswith("/head-jeeves overload load average 45 on 8 cores for 5 min (free 41 MB, swap 6374 MB). Working (2): ")
    assert 'a "A" in podbay, 1 h 0 min, now: building the board' in command
    assert "b \"B\" in podbay, 2 h 0 min" in command
    assert "\n" not in command
    again, state = overload.step(state, overload.HIGH, "x", busy, {}, HEALTH, NOW)
    assert again is None
    still, state = overload.step(state, None, None, busy, {}, HEALTH, NOW)
    assert still is None and state["episode"]
    pauses = {"a": {"name": "a", "title": "A"}}
    cleared, state = overload.step(state, overload.NORMAL, None, busy, pauses, HEALTH, NOW)
    assert cleared == "/head-jeeves overload-cleared The load has been normal for 5 min. Paused: a."
    assert not state["episode"]
    assert overload.step(state, overload.NORMAL, None, busy, pauses, HEALTH, NOW)[0] is None


def test_an_episode_without_an_event_sends_no_clear():
    _, state = overload.step({}, overload.HIGH, "x", [_session("a")], {}, HEALTH, NOW)
    assert overload.step(state, overload.NORMAL, None, [], {}, HEALTH, NOW)[0] is None


def test_no_new_event_while_the_paused_set_is_unchanged():
    busy = [_session("b"), _session("c")]
    pauses = {"a": {"name": "a"}}
    state = {"episode": False, "sent": False, "last_paused": ["a"]}
    command, state = overload.step(state, overload.HIGH, "x", busy, pauses, HEALTH, NOW)
    assert command is None
    command, state = overload.step(state, overload.HIGH, "x", busy, {}, HEALTH, NOW)  # a resumed
    assert command is not None and state["last_paused"] == []


def test_paused_sessions_and_head_jeeves_are_not_working():
    head = _session("hj")
    head.name = "head-jeeves"
    sessions = [_session("a"), _session("b"), _session("idle", working=False), head]
    assert [s.session_id for s in overload.working(sessions, {"b"}, NOW)] == ["a"]


def test_state_survives_a_restart(tmp_path):
    overload.write_state({"episode": True, "sent": True})
    assert overload.read_state() == {"episode": True, "sent": True}


def test_pause_records_and_resume_forgets():
    paused.record("a", "a", "A", "head-jeeves", NOW)
    paused.record("a", "a", "A", "head-jeeves", NOW)
    assert paused.ids() == {"a"} and len(paused.read()) == 1
    assert paused.forget("a") and not paused.forget("a")
    paused.record("old", "old", "Old", None, NOW - timedelta(days=8))
    paused.record("new", "new", "New", None, NOW)
    assert paused.ids() == {"new"}


def test_a_paused_session_that_goes_idle_raises_no_finish_or_stall():
    memory = hal.Memory()
    hal.remarks(memory, [_session("a"), _session("s")], {}, NOW)
    paused.record("a", "a", "A", None, NOW)
    paused.record("s", "s", "S", None, NOW)
    later = NOW + timedelta(minutes=15)
    stalled = _session("s")
    stalled.last_turn_ts = NOW
    lines = hal.remarks(memory, [_session("a", working=False), stalled], {}, later)
    assert lines == [] and memory.events == []


def test_inventory_and_board_show_paused():
    paused.record("a", "a", "A", "head-jeeves", NOW)
    payload = inventory.inventory_payload([_session("a"), _session("b")], set())
    rows = {s["name"]: s for s in payload["sessions"]}
    assert rows["a"]["paused"]["by"] == "head-jeeves" and rows["b"]["paused"] is None
    assert "paused: a" in inventory.render_status(payload)
    html = board.render(payload, NOW)
    assert "paused · " in html


def test_the_screen_sends_the_event_once_and_retries_when_it_did_not_arrive(tmp_path, monkeypatch):
    from podbay import app as app_mod
    from podbay.state import StateStore

    app = app_mod.PodbayApp(state_store=StateStore(tmp_path / "s.json"), no_splash=True, head_jeeves=True)
    app._live_sessions = {s.session_id: s for s in (_session("a"), _session("b"))}
    monkeypatch.setattr(overload, "working", lambda sessions, ids, now: sorted(sessions, key=lambda s: s.name))
    sent, arrives = [], [False]

    def send(command):
        sent.append(command)
        return arrives[0]

    monkeypatch.setattr(app, "_send_to_head_jeeves", send)
    now = datetime.now().timestamp()
    health = {**HEALTH, "cores": 8, "history": [{"at": now - 60 * i, "load": 50, "cpu": 90} for i in range(5, -1, -1)]}
    app._check_overload(health)
    assert len(sent) == 1 and overload.read_state() == {}  # not delivered: nothing recorded
    arrives[0] = True
    app._check_overload(health)
    app._check_overload(health)
    assert len(sent) == 2 and overload.read_state()["sent"]
    health["history"] = [{"at": now - 60 * i, "load": 3, "cpu": 10} for i in range(5, -1, -1)]
    app._check_overload(health)
    assert sent[-1].startswith("/head-jeeves overload-cleared") and not overload.read_state()["episode"]
