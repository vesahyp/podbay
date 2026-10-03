from datetime import datetime, timedelta

from podbay import hal
from podbay.model import Session

NOW = datetime(2026, 10, 3, 12, 0)


def _session(sid, *, last_turn="end_turn", minutes_ago=1, seen=False, waiting_on=None, parked_until=None, shell=False):
    ts = NOW - timedelta(minutes=minutes_ago)
    return Session(
        session_id=sid, pid=1, cwd="/x", name=sid, name_source="derived", status="idle",
        status_updated_at=ts, updated_at=ts, started_at=NOW - timedelta(hours=2),
        last_turn=last_turn, last_turn_ts=ts, seen_at=NOW if seen else None,
        waiting_on=waiting_on, parked_until=parked_until, is_shell=shell, iterm_title=sid.upper(),
    )


def test_first_refresh_only_sets_the_baseline():
    memory = hal.Memory()
    assert hal.remarks(memory, [_session("a")], {}, NOW) == []
    assert memory.primed and memory.statuses == {"a": "needs_you"}


def test_a_session_that_finishes_is_announced_once_and_only_if_unread():
    memory = hal.Memory()
    working = _session("a", last_turn="in_progress")
    hal.remarks(memory, [working], {}, NOW)

    done = _session("a")
    lines = hal.remarks(memory, [done], {}, NOW)
    assert lines == ["A has finished, Vesa. It is waiting for you."] or lines[0].startswith("A has finished")
    assert hal.remarks(memory, [done], {}, NOW) == []  # same state, nothing new

    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    seen = _session("a", seen=True)  # the tab was focused as it finished
    assert hal.remarks(memory, [seen], {}, NOW) == []


def test_question_stall_and_due_have_their_own_lines():
    memory = hal.Memory()
    hal.remarks(memory, [_session("q", last_turn="in_progress"), _session("s", last_turn="in_progress"), _session("d", last_turn="in_progress")], {}, NOW)
    later = NOW + timedelta(minutes=15)
    lines = hal.remarks(memory, [
        _session("q", waiting_on={"kind": "ask_user_question", "detail": "Which?"}),
        _session("s", last_turn="in_progress", minutes_ago=0),  # in progress since NOW: 15 min at `later`
        _session("d", parked_until=later - timedelta(minutes=1)),
    ], {}, later)
    assert any("Q has a question" in l for l in lines)
    assert any("S has been silent for 15 minutes" in l for l in lines)
    assert any("D is due" in l for l in lines)


def test_hot_quota_warns_once_per_window_and_again_after_a_reset():
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {"claude": {"five_pct": 10}}, NOW)
    hot = {"claude": {"five_pct": 90}, "personal": {"five_pct": 20}}
    lines = hal.remarks(memory, [_session("a", last_turn="in_progress")], hot, NOW)
    assert lines == ["The claude account's five-hour window is at 90 percent, Vesa. I would not take on anything heavy."] or "90 percent" in lines[0]
    assert hal.remarks(memory, [_session("a", last_turn="in_progress")], hot, NOW) == []
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {"claude": {"five_pct": 3}, "personal": {"five_pct": 20}}, NOW)
    assert "90 percent" in hal.remarks(memory, [_session("a", last_turn="in_progress")], hot, NOW)[0]


def _working_at(when):
    """A session mid-turn as of `when`, so it is working, not stalled."""
    s = _session("a", last_turn="in_progress")
    s.last_turn_ts = when
    return [s]


def test_quiet_ship_remark_at_most_every_two_hours_and_only_when_nothing_needs_you():
    memory = hal.Memory()
    hal.remarks(memory, _working_at(NOW), {}, NOW)
    t1 = NOW + timedelta(hours=1)
    assert hal.remarks(memory, _working_at(t1), {}, t1) == []
    t2 = NOW + timedelta(hours=2)
    lines = hal.remarks(memory, _working_at(t2), {}, t2)
    assert lines and "functioning normally" in lines[0]
    t3 = NOW + timedelta(hours=3)
    assert hal.remarks(memory, _working_at(t3), {}, t3) == []
    # something needing attention keeps him quiet and restarts the clock
    waiting = [_session("a", seen=True)]  # seen, so no "finished" line, but still needs you
    assert hal.remarks(memory, waiting, {}, NOW + timedelta(hours=4, minutes=1)) == []
    assert hal.remarks(memory, waiting, {}, NOW + timedelta(hours=7)) == []


def test_shell_rows_say_nothing():
    memory = hal.Memory()
    hal.remarks(memory, [_session("sh", shell=True)], {}, NOW)
    assert hal.remarks(memory, [_session("sh", shell=True)], {}, NOW + timedelta(hours=3)) == []

