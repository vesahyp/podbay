import pytest
from datetime import datetime, timedelta

from podbay.model import (
    DUE,
    EMPTY,
    NEEDS_YOU,
    PARKED,
    SHELL,
    STALLED,
    WORKING,
    Session,
    sort_key,
)

NOW = datetime(2026, 9, 9, 12, 0)


def _make_session(
    *,
    name="s",
    status="idle",
    status_updated_at=None,
    parked_until=None,
    last_turn=None,
    last_turn_ts=None,
    tab_busy=None,
    has_transcript=True,
    is_shell=False,
) -> Session:
    return Session(
        session_id=name,
        pid=1,
        cwd="/tmp",
        name=name,
        name_source="derived",
        status=status,
        status_updated_at=status_updated_at or NOW,
        updated_at=NOW,
        started_at=NOW,
        parked_until=parked_until,
        last_turn=last_turn,
        last_turn_ts=last_turn_ts,
        tab_busy=tab_busy,
        has_transcript=has_transcript,
        is_shell=is_shell,
    )


# -- last_turn == "in_progress" ------------------------------------------------


def test_in_progress_recent_is_working(    has_transcript=True,
):
    s = _make_session(last_turn="in_progress", last_turn_ts=NOW - timedelta(minutes=2))
    assert s.derive_status(NOW) == WORKING


def test_in_progress_at_exactly_ten_minutes_is_still_working():
    s = _make_session(last_turn="in_progress", last_turn_ts=NOW - timedelta(minutes=10))
    assert s.derive_status(NOW) == WORKING


def test_in_progress_over_ten_minutes_is_stalled():
    s = _make_session(last_turn="in_progress", last_turn_ts=NOW - timedelta(minutes=10, seconds=1))
    assert s.derive_status(NOW) == STALLED


def test_in_progress_with_no_timestamp_falls_back_to_working():
    s = _make_session(last_turn="in_progress", last_turn_ts=None)
    assert s.derive_status(NOW) == WORKING


# -- last_turn == "end_turn" falls through to parked/due/needs_you -----------


def test_end_turn_with_no_park_is_needs_you():
    s = _make_session(last_turn="end_turn")
    assert s.derive_status(NOW) == NEEDS_YOU


def test_end_turn_with_past_park_is_due():
    s = _make_session(last_turn="end_turn", parked_until=NOW - timedelta(minutes=1))
    assert s.derive_status(NOW) == DUE


def test_end_turn_with_park_exactly_now_is_due():
    s = _make_session(last_turn="end_turn", parked_until=NOW)
    assert s.derive_status(NOW) == DUE


def test_end_turn_with_future_park_is_parked():
    s = _make_session(last_turn="end_turn", parked_until=NOW + timedelta(hours=1))
    assert s.derive_status(NOW) == PARKED


# -- last_turn is None: tab_busy fallback ------------------------------------


def test_none_last_turn_tab_busy_true_is_working():
    s = _make_session(last_turn=None, tab_busy=True, status="idle")
    assert s.derive_status(NOW) == WORKING


def test_none_last_turn_tab_busy_false_is_needs_you_even_if_registry_busy():
    s = _make_session(last_turn=None, tab_busy=False, status="busy")
    assert s.derive_status(NOW) == NEEDS_YOU


def test_none_last_turn_tab_busy_false_respects_park():
    s = _make_session(last_turn=None, tab_busy=False, parked_until=NOW + timedelta(hours=1))
    assert s.derive_status(NOW) == PARKED


# -- last_turn is None, tab_busy is None: registry fallback ------------------


def test_none_last_turn_no_tab_busy_registry_busy_is_working():
    s = _make_session(last_turn=None, tab_busy=None, status="busy")
    assert s.derive_status(NOW) == WORKING


def test_none_last_turn_no_tab_busy_registry_shell_is_working():
    s = _make_session(last_turn=None, tab_busy=None, status="shell")
    assert s.derive_status(NOW) == WORKING


def test_none_last_turn_no_tab_busy_registry_idle_is_needs_you():
    s = _make_session(last_turn=None, tab_busy=None, status="idle")
    assert s.derive_status(NOW) == NEEDS_YOU


def test_none_last_turn_no_tab_busy_registry_idle_respects_due():
    s = _make_session(last_turn=None, tab_busy=None, status="idle", parked_until=NOW - timedelta(minutes=1))
    assert s.derive_status(NOW) == DUE


# -- no transcript file: empty session --------------------------------------


def test_no_transcript_idle_is_empty():
    s = _make_session(last_turn=None, tab_busy=False, status="idle", has_transcript=False)
    assert s.derive_status(NOW) == EMPTY


def test_no_transcript_but_tab_busy_is_working():
    s = _make_session(last_turn=None, tab_busy=True, has_transcript=False)
    assert s.derive_status(NOW) == WORKING


def test_no_transcript_parked_stays_parked():
    s = _make_session(last_turn=None, tab_busy=False, has_transcript=False, parked_until=NOW + timedelta(hours=1))
    assert s.derive_status(NOW) == PARKED


# -- is_shell: plain terminal, wins over every other signal ------------------


def test_is_shell_wins_over_in_progress():
    s = _make_session(is_shell=True, last_turn="in_progress", last_turn_ts=NOW)
    assert s.derive_status(NOW) == SHELL


def test_is_shell_wins_over_park():
    s = _make_session(is_shell=True, last_turn="end_turn", parked_until=NOW - timedelta(minutes=1))
    assert s.derive_status(NOW) == SHELL


def test_is_shell_with_no_other_signal():
    s = _make_session(is_shell=True, has_transcript=False)
    assert s.derive_status(NOW) == SHELL


# -- sort order ---------------------------------------------------------------


def test_sort_order_groups_and_ages():
    due = _make_session(name="due", last_turn="end_turn", parked_until=NOW - timedelta(minutes=5))
    needs_old = _make_session(name="needs_old", last_turn="end_turn", status_updated_at=NOW - timedelta(hours=2))
    stalled = _make_session(
        name="stalled",
        last_turn="in_progress",
        last_turn_ts=NOW - timedelta(minutes=15),
        status_updated_at=NOW - timedelta(minutes=30),
    )
    needs_new = _make_session(name="needs_new", last_turn="end_turn", status_updated_at=NOW - timedelta(minutes=10))
    working = _make_session(name="working", last_turn="in_progress", last_turn_ts=NOW - timedelta(minutes=1))
    parked_soon = _make_session(name="parked_soon", last_turn="end_turn", parked_until=NOW + timedelta(hours=1))
    parked_later = _make_session(name="parked_later", last_turn="end_turn", parked_until=NOW + timedelta(hours=5))
    empty = _make_session(name="empty", last_turn=None, tab_busy=False, has_transcript=False)
    shell = _make_session(name="shell", is_shell=True)

    ordered = sorted(
        [working, parked_later, empty, needs_new, due, stalled, needs_old, parked_soon, shell],
        key=lambda s: sort_key(s, NOW),
    )
    names = [s.name for s in ordered]

    assert names[0] == "due"
    # needs_you and stalled share one group, sorted together oldest-first
    assert names[1:4] == ["needs_old", "stalled", "needs_new"]
    assert names[4] == "working"
    assert names[5] == "empty"
    assert names[6] == "shell"
    assert names[7:9] == ["parked_soon", "parked_later"]


# -- unread: a finished answer not yet looked at -----------------------------


def test_unread_when_never_seen():
    s = _make_session(last_turn="end_turn", last_turn_ts=NOW - timedelta(minutes=1))
    assert s.unread is True


def test_not_unread_when_seen_after_answer():
    s = _make_session(last_turn="end_turn", last_turn_ts=NOW - timedelta(minutes=5))
    s.seen_at = NOW - timedelta(minutes=1)
    assert s.unread is False


def test_unread_when_answer_after_seen():
    s = _make_session(last_turn="end_turn", last_turn_ts=NOW)
    s.seen_at = NOW - timedelta(minutes=1)
    assert s.unread is True


def test_not_unread_while_in_progress():
    s = _make_session(last_turn="in_progress", last_turn_ts=NOW)
    assert s.unread is False


# -- registry status "waiting": an open dialog the transcript cannot see -----


def test_waiting_registry_status_is_needs_you_even_if_turn_in_progress():
    s = _make_session(
        last_turn="in_progress", last_turn_ts=NOW - timedelta(minutes=12),
        status="waiting", status_updated_at=NOW - timedelta(minutes=11),
    )
    assert s.derive_status(NOW) == NEEDS_YOU


def test_waiting_registry_status_older_than_last_turn_is_ignored():
    s = _make_session(
        last_turn="in_progress", last_turn_ts=NOW - timedelta(minutes=1),
        status="waiting", status_updated_at=NOW - timedelta(minutes=5),
    )
    assert s.derive_status(NOW) == WORKING


def test_waiting_registry_status_beats_park():
    s = _make_session(
        last_turn="end_turn", last_turn_ts=NOW - timedelta(minutes=2),
        status="waiting", status_updated_at=NOW - timedelta(minutes=1),
        parked_until=NOW + timedelta(hours=1),
    )
    assert s.derive_status(NOW) == NEEDS_YOU


# -- background tasks -> WATCHING -------------------------------------------------

from podbay.model import WATCHING  # noqa: E402


def _watching_session(tasks, **kwargs):
    s = _make_session(last_turn="end_turn", last_turn_ts=NOW - timedelta(minutes=5), **kwargs)
    s.started_at = NOW - timedelta(hours=1)
    s.background_tasks = tasks
    return s


def test_running_background_task_makes_an_idle_session_watching():
    s = _watching_session([{"id": "b", "ts": NOW - timedelta(minutes=10), "timeout_ms": None, "ended": False}])
    assert s.derive_status(NOW) == WATCHING


def test_ended_expired_or_pre_restart_tasks_do_not_count():
    tasks = [
        {"id": "done", "ts": NOW - timedelta(minutes=10), "timeout_ms": None, "ended": True},
        {"id": "mon", "ts": NOW - timedelta(minutes=10), "timeout_ms": 60_000, "ended": False},
        {"id": "old", "ts": NOW - timedelta(hours=7), "timeout_ms": None, "ended": False},
        {"id": "before_restart", "ts": NOW - timedelta(hours=2), "timeout_ms": None, "ended": False},
    ]
    assert _watching_session(tasks).derive_status(NOW) == NEEDS_YOU


def test_watching_loses_to_due_and_in_progress():
    task = [{"id": "b", "ts": NOW - timedelta(minutes=10), "timeout_ms": None, "ended": False}]
    assert _watching_session(task, parked_until=NOW - timedelta(minutes=1)).derive_status(NOW) == DUE
    busy = _watching_session(task)
    busy.last_turn = "in_progress"
    assert busy.derive_status(NOW) == WORKING


def test_find_session_by_what_a_person_says():
    from datetime import datetime

    from podbay.model import Session, find_session

    now = datetime.now()

    def make(sid, name, title, cwd, repos, terminal=None, shell=False):
        s = Session(session_id=sid, pid=int(sid[-1]), cwd=cwd, name=name, name_source="derived", status="idle",
                    status_updated_at=now, updated_at=now, started_at=now, iterm_title=title, repos_touched=repos, is_shell=shell)
        s.window_number = terminal
        return s

    sora = make("aaaa1111-1", "jeeves-2e", "Sora graphics research", "/r/jeeves", ["jeeves", "sora"], terminal=6)
    ux = make("bbbb2222-2", "jeeves-53", "ecarbrowser ux", "/r/jeeves", ["jeeves", "ecarbrowser"], terminal=5)
    other = make("cccc3333-3", "jeeves-99", "ecarbrowser pricing", "/r/ecarbrowser", ["ecarbrowser"], terminal=7)
    shell = make("tty:/dev/ttys001", "sora shell", "sora", "/r/sora", [], shell=True)
    sessions = [sora, ux, other, shell]

    assert find_session(sessions, "jeeves-2e") is sora  # exact name
    assert find_session(sessions, "1") is sora  # pid
    assert find_session(sessions, "aaaa") is sora  # id prefix
    assert find_session(sessions, "#6") is sora and find_session(sessions, "6") is sora  # terminal
    assert find_session(sessions, "sora") is sora  # title fragment, shells never count
    assert find_session(sessions, "GRAPHICS") is sora  # case does not matter
    assert find_session(sessions, "pricing") is other
    assert find_session(sessions, "ecarbrowser") is None  # two sessions fit
    assert find_session(sessions, "ecarbrowser ux") is ux
    assert find_session(sessions, "nothing like it") is None
    assert find_session(sessions, "") is None


def test_running_subagents_keep_a_session_working_after_its_turn_ended():
    from datetime import datetime, timedelta

    from podbay.model import NEEDS_YOU, WORKING, Session

    now = datetime.now()
    s = Session(session_id="a", pid=1, cwd="/x", name="a", name_source="derived", status="idle",
                status_updated_at=now, updated_at=now, started_at=now - timedelta(hours=1),
                last_turn="end_turn", last_turn_ts=now, subagents_running=2)
    assert s.derive_status(now) == WORKING
    s.subagents_running = 0
    assert s.derive_status(now) == NEEDS_YOU


def test_age_runs_from_the_newest_activity_subagents_included():
    """The registry status stops at the main turn's end; a subagent working
    since then makes the session young, not two hours idle."""
    from datetime import datetime, timedelta

    from tests.test_app import _selection_session

    now = datetime.now()
    session = _selection_session(
        "sora", now, status_updated_at=now - timedelta(hours=2), last_turn_ts=now - timedelta(hours=2),
        subagents_running=1, subagent_written_at=now - timedelta(seconds=20),
    )
    assert session.derive_status(now) == "working"
    assert session.age_seconds(now) == pytest.approx(20, abs=1)

    # with nothing newer, the registry's change still counts
    quiet = _selection_session("q", now, status_updated_at=now - timedelta(minutes=5), last_turn_ts=now - timedelta(minutes=9))
    assert quiet.age_seconds(now) == pytest.approx(300, abs=1)
