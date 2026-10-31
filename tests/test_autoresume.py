"""A session stopped on a usage limit gets one message after the reset, only
when the window really reset and it still sits there (autoresume.py). The
transcript records keep the shapes Claude Code wrote on 2026-10-31."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from podbay import autoresume as ar
from podbay import sources, voice

from .test_inventory import _session

STOP = datetime(2026, 10, 31, 11, 56, 51)
RESET = datetime(2026, 10, 31, 13, 20)
AFTER = RESET + timedelta(minutes=2)
MESSAGE = "You've hit your 5-hour limit · resets 1:20pm (Europe/Helsinki)"
TZ = "Europe/Helsinki"


def _iso(local: datetime) -> str:
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def assistant(at, text, stop_reason="end_turn", model="claude-opus-5-5", error=None, sidechain=False):
    record = {"type": "assistant", "timestamp": _iso(at), "isSidechain": sidechain, "message": {
        "model": model, "stop_reason": stop_reason, "id": f"m{at.timestamp()}", "content": [{"type": "text", "text": text}]}}
    if error:
        record.update(isApiErrorMessage=True, error=error)
    return record


def limit(at, text=MESSAGE, sidechain=False):
    return assistant(at, text, stop_reason="stop_sequence", model="<synthetic>", error="rate_limit", sidechain=sidechain)


def user(at, content, **extra):
    return {"type": "user", "timestamp": _iso(at), "isSidechain": False, "message": {"role": "user", "content": content}, **extra}


def wrap_note(at):
    return user(at, "[Usage limit reached; a short grace allowance remains...]", isMeta=True, usageLimitNote="wrap_up")


def notification(at, status="completed", summary="Agent finished"):
    return user(at, f"<task-notification>\n<task-id>abc</task-id>\n<status>{status}</status>\n<summary>{summary}</summary>\n</task-notification>")


def transcript(tmp_path, records, name="s1"):
    path = tmp_path / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def stop_of(tmp_path, records):
    return sources.read_limit_stop(transcript(tmp_path, records), "s1", now=AFTER)


@pytest.mark.parametrize("text,label,reset", [
    (MESSAGE, "5-hour", "1:20pm (Europe/Helsinki)"),
    ("You've hit your session limit · resets 9pm (Europe/Helsinki)", "session", "9pm (Europe/Helsinki)"),
    ("You've hit your weekly limit · resets Nov 3 at 1pm (Europe/Helsinki)", "weekly", "Nov 3 at 1pm (Europe/Helsinki)"),
    ("You've hit your Opus limit · resets Nov 3, 1:20pm (Europe/Helsinki)", "Opus", "Nov 3, 1:20pm (Europe/Helsinki)"),
    ("You've hit your 5-hour limit · resets 1:20pm (Europe/Helsinki) · progress saved\n/upgrade", "5-hour", "1:20pm (Europe/Helsinki)"),
    ("You've hit your weekly limit", "weekly", None),
])
def test_the_limit_message_variants(tmp_path, text, label, reset):
    stop = stop_of(tmp_path, [limit(STOP, text)])
    assert (stop["kind"], stop["label"], stop["reset_text"]) == ("message", label, reset)


@pytest.mark.parametrize("text", [
    "You've hit your monthly spend limit · raise it at x",
    "You've hit your fast limit",
    "You've hit your team's shared budget. /model to switch models.",
    "Login expired · Please run /login",
])
def test_limits_that_no_window_reset_ends_are_no_stop(tmp_path, text):
    assert stop_of(tmp_path, [limit(STOP, text)]) is None


def test_the_model_quoting_the_message_is_no_stop(tmp_path):
    assert stop_of(tmp_path, [assistant(STOP, MESSAGE)]) is None


def test_a_notification_after_the_limit_message_leaves_the_stop(tmp_path):
    stop = stop_of(tmp_path, [limit(STOP), notification(STOP + timedelta(seconds=1))])
    assert stop["kind"] == "message"


def test_a_reminder_does_not_count_as_typed(tmp_path):
    records = [limit(STOP), user(STOP + timedelta(seconds=1), "<system-reminder>x</system-reminder>")]
    assert stop_of(tmp_path, records)["kind"] == "message"


@pytest.mark.parametrize("after", [
    user(STOP + timedelta(minutes=1), "go on"),
    user(STOP + timedelta(minutes=1), [{"type": "text", "text": "[Request interrupted by user]"}]),
    assistant(STOP + timedelta(minutes=1), "Still at work.", stop_reason="tool_use"),
    assistant(STOP + timedelta(minutes=1), "All done."),
])
def test_anything_new_ends_the_stop(tmp_path, after):
    assert stop_of(tmp_path, [limit(STOP), after]) is None


def test_a_grace_turn_that_reports_the_limit_is_a_stop(tmp_path):
    records = [wrap_note(STOP), assistant(STOP + timedelta(minutes=1), "My usage limit was reached, so I stopped here.")]
    stop = stop_of(tmp_path, records)
    assert (stop["kind"], stop["at"], stop["reset_text"]) == ("wrap_up", STOP + timedelta(minutes=1), None)


def test_a_grace_turn_still_running_is_no_stop(tmp_path):
    assert stop_of(tmp_path, [wrap_note(STOP)]) is None
    assert stop_of(tmp_path, [wrap_note(STOP), assistant(STOP, "Next step.", stop_reason="tool_use")]) is None


def test_a_grace_turn_that_finished_without_a_word_of_the_limit_is_no_stop(tmp_path):
    assert stop_of(tmp_path, [wrap_note(STOP), assistant(STOP, "All tests pass. Done.")]) is None


def test_a_typed_prompt_after_a_grace_stop_ends_it(tmp_path):
    records = [wrap_note(STOP), assistant(STOP, "My usage limit was reached."), user(STOP + timedelta(minutes=1), "go on")]
    assert stop_of(tmp_path, records) is None


def _agent_file(tmp_path, records):
    agents = tmp_path / "s1" / "subagents"
    agents.mkdir(parents=True)
    (agents / "agent-a1.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_an_agent_on_the_limit_is_a_stop_of_its_session(tmp_path):
    _agent_file(tmp_path, [limit(STOP, sidechain=True)])
    path = transcript(tmp_path, [assistant(STOP - timedelta(minutes=5), "Started an agent.")])
    stop = sources.read_limit_stop(path, "s1", now=STOP + timedelta(minutes=1))
    assert (stop["kind"], stop["reset_text"]) == ("agent", "1:20pm (Europe/Helsinki)")


def test_an_agent_stop_from_before_a_typed_prompt_is_over(tmp_path):
    _agent_file(tmp_path, [limit(STOP, sidechain=True)])
    path = transcript(tmp_path, [user(STOP + timedelta(minutes=5), "go on")])
    assert sources.read_limit_stop(path, "s1", now=STOP + timedelta(minutes=6)) is None


def test_an_agent_that_got_past_the_limit_is_no_stop(tmp_path):
    _agent_file(tmp_path, [limit(STOP, sidechain=True), assistant(STOP + timedelta(minutes=1), "Done.", sidechain=True)])
    path = transcript(tmp_path, [assistant(STOP - timedelta(minutes=5), "Started an agent.")])
    assert sources.read_limit_stop(path, "s1", now=STOP + timedelta(minutes=2)) is None


FAILED = "Agent failed: Agent terminated early due to an API error: You've hit your session limit · resets 12:30am (Europe/Helsinki) (error type rate_limit, HTTP 429)"


def test_a_failed_notification_on_the_limit_is_a_stop(tmp_path):
    stop = stop_of(tmp_path, [notification(STOP, "failed", FAILED)])
    assert (stop["kind"], stop["label"], stop["reset_text"]) == ("agent", "session", "12:30am (Europe/Helsinki)")


def test_the_session_reporting_the_agents_limit_stays_on_it(tmp_path):
    records = [notification(STOP, "failed", FAILED), assistant(STOP + timedelta(seconds=9), "The agent hit the usage limit. I wait for the reset.")]
    assert stop_of(tmp_path, records)["kind"] == "agent"


def test_the_session_finishing_the_work_itself_ends_the_agents_stop(tmp_path):
    records = [notification(STOP, "failed", FAILED), assistant(STOP + timedelta(seconds=9), "I did the job myself. All done.")]
    assert stop_of(tmp_path, records) is None


def test_a_notification_of_a_failure_that_is_no_limit_is_no_stop(tmp_path):
    assert stop_of(tmp_path, [notification(STOP, "failed", "Agent failed: a tool error")]) is None


@pytest.mark.parametrize("text,stopped,expected", [
    ("1:20pm (Europe/Helsinki)", STOP, RESET),
    ("9pm (Europe/Helsinki)", STOP, datetime(2026, 10, 31, 21, 0)),
    ("12:30am (Europe/Helsinki)", datetime(2026, 10, 31, 23, 0), datetime(2026, 11, 1, 0, 30)),
    ("11:20am (Europe/Helsinki)", STOP, datetime(2026, 11, 1, 11, 20)),
    ("Nov 3 at 1pm (Europe/Helsinki)", STOP, datetime(2026, 11, 3, 13, 0)),
    ("Nov 3, 1:20pm (Europe/Helsinki)", STOP, datetime(2026, 11, 3, 13, 20)),
    ("Jan 2 at 9am (Europe/Helsinki)", STOP, datetime(2027, 1, 2, 9, 0)),
    ("soon", STOP, None),
    ("1pm (Not/AZone)", STOP, None),
    (None, STOP, None),
])
def test_reset_time(text, stopped, expected, monkeypatch):
    monkeypatch.setenv("TZ", TZ)
    assert ar.reset_time(text, stopped) == expected


def test_window_kind():
    assert ar.window_kind("5-hour") == ar.window_kind("session") == ar.FIVE_HOUR
    assert ar.window_kind("weekly") == ar.SEVEN_DAY
    assert ar.window_kind("Opus") == "model:opus"
    assert ar.window_kind(None) is None


def window(pct, resets=None):
    return {"used_pct": pct, "left_pct": 100 - pct, "resets_at": resets.astimezone(timezone.utc).isoformat() if resets else None}


def entry(five=None, week=None, models=()):
    return {"account": "claude", ar.FIVE_HOUR: five, ar.SEVEN_DAY: week, "models": list(models)}


@pytest.mark.parametrize("five,expected", [
    (window(0.0), True),  # quota.window: the reading's reset time passed
    (None, True),  # no reading: the clock decides
    (window(3.0, RESET + timedelta(hours=5)), True),  # a new window has started
    (window(100.0, RESET + timedelta(hours=5)), False),  # full again already
    (window(40.0, RESET + timedelta(minutes=1)), False),  # the old window has not gone
])
def test_window_reset(five, expected):
    assert ar.window_reset(entry(five), ar.FIVE_HOUR, RESET) is expected


def test_a_models_window_is_checked_on_a_model_limit():
    opus = {"alias": "opus", "seven_day": window(99.0, RESET + timedelta(days=2))}
    assert ar.window_reset(entry(window(1.0), window(1.0), [opus]), "model:opus", RESET) is False


def test_the_input_box():
    assert ar.input_box_empty("output\n───\n❯  \n───\n  ⚠ Usage limit reached")
    assert ar.input_box_empty("❯ an old prompt\nanswer\n❯ \n")
    assert not ar.input_box_empty("───\n❯ half a line\n───")
    assert not ar.input_box_empty("no box here")
    assert not ar.input_box_empty(None)


class Bay:
    """One check's world: a session, its quota, and what was sent."""

    def __init__(self, tmp_path, stop=None, **session):
        self.path = tmp_path / "autoresume.json"
        self.sessions = {}
        self.entries = {"claude": entry(window(0.0))}
        self.paused = set()
        self.screen = "❯ \n"
        self.sits = True
        self.answer = "sent"
        self.sent = []
        self.usage_since = []
        self.add("mono", stop if stop is not None else {"kind": "message", "at": STOP, "label": "5-hour", "reset_text": "1:20pm (Europe/Helsinki)"}, **session)

    def add(self, name, stop, **fields):
        s = _session(name)
        s.account = "claude"
        s.limit_stop = stop
        for k, v in fields.items():
            setattr(s, k, v)
        self.sessions[name] = s
        return s

    def check(self, now=AFTER):
        def entries_for(since):
            self.usage_since.append(since)
            return self.entries

        def send(s, text):
            self.sent.append((s.name, text))
            return self.answer

        return ar.check(
            list(self.sessions.values()), entries_for, self.paused, lambda _s, _k: self.sits,
            lambda _s: self.screen, lambda text: "enter to confirm" in (text or ""), send, now, self.path,
        )

    def stops(self):
        return list(ar.read_state(self.path)["stops"].values())


def test_a_stop_is_recorded_with_session_account_and_reset_when_first_seen(tmp_path):
    bay = Bay(tmp_path)
    bay.check(STOP + timedelta(minutes=1))
    e = bay.stops()[0]
    assert (e["session_id"], e["account"], e["reset_at"], e["window"]) == ("mono", "claude", RESET.isoformat(), ar.FIVE_HOUR)
    assert bay.sent == []


def test_nothing_goes_before_the_reset_or_inside_the_delay(tmp_path):
    bay = Bay(tmp_path)
    assert bay.check(RESET - timedelta(minutes=30)).resumed == []
    assert bay.check(RESET + timedelta(seconds=89)).resumed == []
    assert bay.sent == []


def test_one_message_after_the_reset_and_never_a_second(tmp_path):
    bay = Bay(tmp_path)
    outcome = bay.check(RESET + timedelta(seconds=90))
    assert outcome.resumed == ["mono"]
    assert bay.sent == [("mono", voice.LIMIT_RESET_MESSAGE)]
    assert bay.stops()[0]["resumed_at"] == (RESET + timedelta(seconds=90)).isoformat()
    bay.check(RESET + timedelta(minutes=3))
    assert len(bay.sent) == 1


def test_a_restart_does_not_send_twice(tmp_path):
    Bay(tmp_path).check()
    again = Bay(tmp_path)
    again.check(AFTER + timedelta(minutes=1))
    assert again.sent == []


def test_the_usage_reading_must_be_from_after_the_reset(tmp_path):
    bay = Bay(tmp_path)
    bay.check()
    assert {"claude": RESET} in bay.usage_since


def test_held_while_the_window_is_still_open_then_sent_when_it_resets(tmp_path):
    bay = Bay(tmp_path)
    bay.entries = {"claude": entry(window(100.0, RESET + timedelta(minutes=30)))}
    assert bay.check().resumed == []
    assert bay.stops()[0]["held_because"] == "the quota still shows the window open"
    bay.entries = {"claude": entry(window(0.0))}
    assert bay.check(AFTER + timedelta(minutes=1)).resumed == ["mono"]


def test_a_new_stop_after_the_message_gets_its_own_message(tmp_path):
    bay = Bay(tmp_path)
    bay.check()
    ar.mark_announced(bay.path)
    later = {"kind": "message", "at": AFTER + timedelta(hours=1), "label": "5-hour", "reset_text": "6:20pm (Europe/Helsinki)"}
    bay.sessions["mono"].limit_stop = later
    assert bay.check(AFTER + timedelta(hours=1)).resumed == []
    assert bay.check(datetime(2026, 10, 31, 18, 22)).resumed == ["mono"]
    assert len(bay.sent) == 2


def test_a_session_that_moved_on_gets_nothing(tmp_path):
    bay = Bay(tmp_path)
    bay.check(STOP + timedelta(minutes=1))
    bay.sessions["mono"].limit_stop = None
    assert bay.check().resumed == []
    assert bay.sent == []
    assert bay.stops()[0]["outcome"] == ar.MOVED_ON


def test_a_session_found_moved_on_at_the_last_read_gets_nothing(tmp_path):
    bay = Bay(tmp_path)
    bay.sits = False
    assert bay.check().resumed == []
    assert bay.sent == []
    assert bay.stops()[0]["outcome"] == ar.MOVED_ON


def test_a_paused_session_gets_nothing(tmp_path):
    bay = Bay(tmp_path)
    bay.paused = {"mono"}
    assert bay.check().resumed == []
    assert bay.check(AFTER + timedelta(hours=1)).gave_up == []
    assert bay.sent == []


def test_a_finished_session_has_no_stop_and_gets_nothing(tmp_path):
    bay = Bay(tmp_path, stop={"kind": "message", "at": STOP, "label": "5-hour", "reset_text": "1:20pm (Europe/Helsinki)"})
    del bay.sessions["mono"]
    bay.add("done", None)
    assert bay.check().resumed == []
    assert bay.sent == [] and bay.stops() == []


def test_a_session_that_left_gets_nothing_and_expires(tmp_path):
    bay = Bay(tmp_path)
    bay.check(STOP + timedelta(minutes=1))
    del bay.sessions["mono"]
    assert bay.check().resumed == []
    assert bay.stops()[0]["outcome"] is None
    bay.check(RESET + ar.MAX_LATE + timedelta(minutes=1))
    assert bay.stops()[0]["outcome"] == ar.TOO_LATE and bay.sent == []


def test_an_old_stop_is_only_recorded(tmp_path):
    bay = Bay(tmp_path)
    outcome = bay.check(RESET + ar.MAX_LATE + timedelta(minutes=1))
    assert (outcome.resumed, bay.sent, bay.stops()[0]["outcome"]) == ([], [], ar.TOO_LATE)


@pytest.mark.parametrize("change,why", [
    ({"status": "busy"}, "its status is busy"),
    ({"status": "waiting"}, "its status is waiting"),
    ({"subagent_written_at": AFTER - timedelta(seconds=30)}, "an agent of it is still writing"),
])
def test_held_while_at_work(tmp_path, change, why):
    bay = Bay(tmp_path, **change)
    assert bay.check().resumed == []
    assert bay.sent == [] and bay.stops()[0]["held_because"] == why


@pytest.mark.parametrize("screen,why", [
    ("❯ half a line\n", "its input box holds text"),
    ("enter to confirm\n❯ \n", "a dialog is open in it"),
    (None, "its screen cannot be read"),
])
def test_the_terminal_holds_the_send(tmp_path, screen, why):
    bay = Bay(tmp_path)
    bay.screen = screen
    bay.check()
    assert bay.sent == [] and bay.stops()[0]["held_because"] == why


def test_a_held_session_is_reported_once_when_given_up(tmp_path):
    bay = Bay(tmp_path)
    bay.screen = "❯ half a line\n"
    bay.check()
    assert bay.check(AFTER + ar.GIVE_UP - timedelta(seconds=1)).gave_up == []
    outcome = bay.check(AFTER + ar.GIVE_UP)
    assert outcome.gave_up == [("mono", "its input box holds text")]
    ar.mark_announced(bay.path)
    assert bay.check(AFTER + ar.GIVE_UP + timedelta(minutes=1)).gave_up == []
    assert bay.sent == []


def test_a_send_that_typed_nothing_is_tried_again(tmp_path):
    bay = Bay(tmp_path)
    bay.answer = "missing"
    assert bay.check().resumed == []
    bay.answer = "sent"
    assert bay.check(AFTER + timedelta(seconds=30)).resumed == ["mono"]
    assert len(bay.sent) == 2


def test_a_send_that_timed_out_is_never_repeated(tmp_path):
    bay = Bay(tmp_path)
    bay.answer = "timeout"
    assert bay.check().uncertain == ["mono"]
    ar.mark_announced(bay.path)
    assert bay.check(AFTER + timedelta(minutes=1)).uncertain == []
    assert len(bay.sent) == 1


def test_the_event_is_kept_until_head_jeeves_has_it(tmp_path):
    bay = Bay(tmp_path)
    assert bay.check().resumed == ["mono"]
    assert bay.check(AFTER + timedelta(seconds=30)).resumed == ["mono"]  # not delivered yet
    ar.mark_announced(bay.path)
    assert bay.check(AFTER + timedelta(minutes=1)).resumed == []
    assert len(bay.sent) == 1


def test_a_grace_stop_waits_for_the_hot_window(tmp_path):
    wrap = {"kind": "wrap_up", "at": STOP, "label": None, "reset_text": None}
    bay = Bay(tmp_path, stop=wrap)
    bay.entries = {"claude": entry(window(99.0, RESET), window(47.0, RESET + timedelta(days=2)))}
    bay.check(STOP + timedelta(minutes=1))
    e = bay.stops()[0]
    assert (e["reset_at"], e["window"]) == (RESET.isoformat(), ar.FIVE_HOUR)
    assert bay.check(RESET - timedelta(minutes=1)).resumed == []
    bay.entries = {"claude": entry(window(0.0), window(47.0, RESET + timedelta(days=2)))}
    assert bay.check().resumed == ["mono"]


def test_a_grace_stop_waits_for_the_week_when_both_are_hot(tmp_path):
    week = RESET + timedelta(days=2)
    assert ar.hot_window(entry(window(100.0, RESET), window(95.0, week))) == (ar.SEVEN_DAY, week)


def test_a_grace_stop_with_no_hot_window_goes_at_once(tmp_path):
    bay = Bay(tmp_path, stop={"kind": "wrap_up", "at": STOP, "label": None, "reset_text": None})
    bay.entries = {"claude": entry(window(10.0, RESET))}
    assert bay.check(STOP + ar.RESUME_DELAY).resumed == ["mono"]


def test_two_sessions_on_one_limit_both_go_and_one_event_names_both(tmp_path):
    bay = Bay(tmp_path)
    bay.add("ecar", {"kind": "wrap_up", "at": STOP - timedelta(minutes=2), "label": None, "reset_text": None})
    bay.entries = {"claude": entry(window(99.0, RESET))}
    bay.check(STOP + timedelta(minutes=1))
    bay.entries = {"claude": entry(window(0.0))}
    outcome = bay.check()
    assert sorted(outcome.resumed) == ["ecar", "mono"]
    assert sorted(n for n, _t in bay.sent) == ["ecar", "mono"]
    command = voice.limit_resume_command(sorted(outcome.resumed), outcome.uncertain, outcome.gave_up)
    assert command == "/head-jeeves event ecar, mono: The usage limit reset and podbay resumed: ecar, mono."


def test_the_message_and_the_full_event():
    assert voice.LIMIT_RESET_MESSAGE == "The usage limit has reset. Continue where you stopped."
    command = voice.limit_resume_command(["a"], ["b"], [("c", "its input box holds text")])
    assert "resumed: a." in command and "may have arrived, check: b." in command and "c (its input box holds text)" in command
