from datetime import datetime, timedelta

from podbay import sweep
from podbay.model import Session

NOW = datetime(2026, 10, 14, 7, 30)
URL = "https://claude.ai/artifact/abc"


def _head(turn_ago=10, turn="end_turn"):
    ts = NOW - timedelta(minutes=turn_ago)
    return Session(
        session_id="h", pid=1, cwd="/x/jeeves", name="head-jeeves", name_source="derived", status="idle",
        status_updated_at=ts, updated_at=ts, started_at=NOW - timedelta(hours=3),
        last_turn=turn, last_turn_ts=ts,
    )


def test_command_is_a_plain_prompt_that_checks_the_watch_before_reading():
    c = sweep.command(URL)
    assert URL in c and not c.startswith("/")
    assert "watch with no url" in c
    assert c.index("watch with no url") < c.index("read the threads")


def test_due_when_idle_answered_and_five_minutes_since_the_last():
    assert sweep.due(_head(), NOW, URL, None, None)
    assert sweep.due(_head(), NOW, URL, NOW - timedelta(minutes=5), NOW - timedelta(minutes=11))


def test_not_due_without_a_board_or_inside_the_interval():
    assert not sweep.due(_head(), NOW, None, None, None)
    assert not sweep.due(_head(), NOW, URL, NOW - timedelta(minutes=4), None)


def test_not_due_while_he_works_or_has_not_answered_the_last_command():
    assert not sweep.due(_head(turn="in_progress"), NOW, URL, None, None)
    assert not sweep.due(_head(turn_ago=10), NOW, URL, None, NOW - timedelta(minutes=2))
