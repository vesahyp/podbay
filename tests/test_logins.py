"""When a login ends, and the pushes about it: a warning 24 hours and 1 hour
before, held at night, and one line the moment a session answers "Login
expired" (2026-10-24: a personal session died mid task with no warning)."""

import json
from datetime import datetime, timedelta

from podbay import accounts, board, logins, quota
from podbay.accounts import Account
from podbay.inventory import inventory_payload, render_status
from podbay.sources import tail_read_transcript

from .test_inventory import _session

NOON = datetime(2026, 10, 14, 12, 0)  # no clock change in the days after it
HOUR = 3600


def _credential(refresh_end: float | None, access_end: float, access: str = "a" * 108, refresh: str = "r" * 108) -> dict:
    oauth = {"accessToken": access, "refreshToken": refresh, "expiresAt": access_end * 1000}
    if refresh_end is not None:
        oauth["refreshTokenExpiresAt"] = refresh_end * 1000
    return {"claudeAiOauth": oauth}


def test_the_deadline_is_the_refresh_tokens_end_then_the_access_tokens():
    now = NOON.timestamp()
    assert accounts.login_deadline(_credential(now + 3 * 86400, now + 8 * HOUR), now) == now + 3 * 86400
    # Past the refresh token's end, the last access token is all that is left.
    assert accounts.login_deadline(_credential(now - HOUR, now + 2 * HOUR), now) == now + 2 * HOUR
    assert accounts.login_deadline(_credential(now - HOUR, now - 60), now) is None
    # An item that does not say, and a logged-out one, have no deadline.
    assert accounts.login_deadline(_credential(None, now + HOUR), now) is None
    assert accounts.login_deadline(_credential(now + 86400, 0, access="", refresh=""), now) is None
    assert accounts.login_deadline(None, now) is None


def test_login_deadlines_by_label(tmp_path):
    now = NOON.timestamp()
    default, personal = Account("claude", tmp_path / ".claude"), Account("personal", tmp_path / ".claude-personal")
    items = {
        default.keychain_service: json.dumps(_credential(now + 86400, 0, access="", refresh="")),
        personal.keychain_service: json.dumps(_credential(now + 86400, now + HOUR)),
    }
    assert accounts.login_deadlines([default, personal], items.get, now) == {"personal": now + 86400}


def _run(deadlines, expired=None, at=NOON, state=None):
    return logins.step(state or {}, deadlines, expired or {}, at)


def test_a_warning_24_hours_and_1_hour_before_and_no_more():
    deadline = (NOON + timedelta(hours=30)).timestamp()
    lines, state = _run({"personal": deadline})
    assert lines == []
    lines, state = _run({"personal": deadline}, at=NOON + timedelta(hours=7), state=state)
    assert len(lines) == 1 and "personal login ends in 23 h 0 min" in lines[0]
    assert "Run /login in a session on personal" in lines[0]
    lines, state = _run({"personal": deadline}, at=NOON + timedelta(hours=8), state=state)
    assert lines == []
    lines, state = _run({"personal": deadline}, at=NOON + timedelta(hours=29, minutes=1), state=state)
    assert len(lines) == 1 and "ends in 59 min" in lines[0]
    lines, state = _run({"personal": deadline}, at=NOON + timedelta(hours=29, minutes=30), state=state)
    assert lines == []


def test_no_warning_at_night_unless_under_2_hours_left():
    deadline = (datetime(2026, 10, 25, 20, 0)).timestamp()
    night = datetime(2026, 10, 24, 23, 30)
    lines, state = _run({"personal": deadline}, at=night)
    assert lines == []
    lines, state = _run({"personal": deadline}, at=datetime(2026, 10, 25, 7, 0), state=state)
    assert len(lines) == 1 and "13 h 0 min" in lines[0]
    # A login that ends at 03:00: the early warning at 01:30 is not held.
    deadline = datetime(2026, 10, 25, 3, 0).timestamp()
    lines, _ = _run({"personal": deadline}, at=datetime(2026, 10, 25, 1, 30))
    assert len(lines) == 1 and "1 h 30 min" in lines[0]
    lines, _ = _run({"personal": deadline}, at=datetime(2026, 10, 25, 0, 30))
    assert lines == []


def test_only_the_late_warning_when_first_seen_under_an_hour():
    deadline = (NOON + timedelta(minutes=40)).timestamp()
    lines, state = _run({"personal": deadline})
    assert len(lines) == 1 and "40 min" in lines[0]
    assert state["warned"]["personal"]["sent"] == ["early", "late"]


def test_a_new_login_warns_again():
    first = (NOON + timedelta(hours=10)).timestamp()
    _, state = _run({"personal": first})
    second = (NOON + timedelta(days=5)).timestamp()
    lines, state = _run({"personal": second}, at=NOON + timedelta(days=4, hours=1), state=state)
    assert len(lines) == 1


def test_login_expired_pushes_once_per_login():
    at = NOON - timedelta(minutes=1)
    lines, state = _run({}, {"personal": at})
    assert lines == ["The personal login has expired and its sessions have stopped. Run /login in a session on personal."]
    # A second session dying later on the same login: no second push.
    lines, state = _run({}, {"personal": NOON + timedelta(minutes=5)}, at=NOON + timedelta(minutes=6), state=state)
    assert lines == []
    # After /login the account has a new deadline; the next expiry pushes.
    deadline = (NOON + timedelta(days=29)).timestamp()
    lines, state = _run({"personal": deadline}, {"personal": NOON + timedelta(hours=2)}, at=NOON + timedelta(hours=2), state=state)
    assert len(lines) == 1


def test_an_old_login_expired_answer_is_not_news():
    lines, state = _run({}, {"claude": NOON - timedelta(hours=3)})
    assert lines == [] and "claude" in state["expired"]


def test_check_records_only_what_went_out(tmp_path):
    path = tmp_path / "logins.json"
    dead = _session("vinkki-1")
    dead.account, dead.login_expired_at = "personal", datetime.now()
    pushed = []
    assert logins.check({}, [dead], datetime.now(), lambda t: (False, "exited 1"), path) == []
    assert not path.exists()
    sent = logins.check({}, [dead], datetime.now(), lambda t: (pushed.append(t), (True, ""))[1], path)
    assert sent == pushed and len(sent) == 1
    assert logins.check({}, [dead], datetime.now(), lambda t: (pushed.append(t), (True, ""))[1], path) == []
    assert "Token" not in path.read_text() and "a" * 20 not in path.read_text()


def test_the_transcript_marks_a_login_expired_answer(tmp_path):
    def record(model, text, ts):
        return json.dumps({"type": "assistant", "timestamp": ts, "message": {
            "model": model, "role": "assistant", "stop_reason": "stop_sequence",
            "content": [{"type": "text", "text": text}]}})

    path = tmp_path / "t.jsonl"
    path.write_text(record("claude-opus-5-5", "Working on it.", "2026-10-24T19:30:00.000Z") + "\n"
                    + record("<synthetic>", "Login expired · Please run /login", "2026-10-24T19:34:19.076Z") + "\n")
    assert tail_read_transcript(path)["login_expired_at"] is not None
    # The model quoting the words is not the answer.
    path.write_text(record("claude-opus-5-5", "Login expired · Please run /login", "2026-10-24T19:34:19.076Z") + "\n")
    assert tail_read_transcript(path)["login_expired_at"] is None
    # A real answer after /login clears it.
    path.write_text(record("<synthetic>", "Login expired · Please run /login", "2026-10-24T19:34:19.076Z") + "\n"
                    + record("claude-opus-5-5", "Back.", "2026-10-24T19:36:00.000Z") + "\n")
    assert tail_read_transcript(path)["login_expired_at"] is None


def test_inventory_and_accounts_show_the_time_left(tmp_path):
    now = datetime.now().timestamp()
    ends = {"personal": now + 3 * 86400 + 5 * HOUR + 60}
    dead = _session("vinkki-1")
    dead.account, dead.login_expired_at = "personal", datetime.now()
    payload = inventory_payload([dead], set(), login_ends=ends)
    assert payload["login_ends"]["personal"].endswith("+00:00")
    assert payload["login_expired"] == ["vinkki-1"]
    assert "logins: personal ends in 3 d 5 h" in render_status(payload)

    personal = Account("personal", tmp_path / ".claude-personal")
    entry = quota.account_entry(personal, {}, None, now, logged_in=True, login_ends_at=ends["personal"])
    assert "login ends in 3 d 5 h" in quota.render(quota.payload([entry]))


def test_board_machine_room_shows_the_time_left():
    now = datetime.now()
    limits = {"personal": {"five_pct": 21.0, "five_resets_at": None, "week_pct": 20.0, "week_resets_at": None},
              "work": {"five_pct": 1.0, "five_resets_at": None, "week_pct": 2.0, "week_resets_at": None}}
    payload = inventory_payload([], set(), login_ends={
        "personal": now.timestamp() + 2 * 86400 + 60, "work": now.timestamp() + 5 * HOUR + 30})
    room = board.render(payload, limits=limits)
    room = room[room.index("Machine room"):]
    assert '<span class="ends">login ends in 2 d 0 h</span>' in room
    assert '<span class="out">login ends in 5 h 0 min</span>' in room
