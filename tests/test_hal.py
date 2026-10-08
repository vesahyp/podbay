from datetime import datetime, timedelta

from podbay import hal
from podbay.model import Session

NOW = datetime(2026, 10, 3, 12, 0)


def _session(sid, *, last_turn="end_turn", minutes_ago=1, seen=False, waiting_on=None, parked_until=None, shell=False, prompt="do it",
             status="idle", subagents=0, main_turn_ts=None):
    ts = NOW - timedelta(minutes=minutes_ago)
    return Session(
        session_id=sid, pid=1, cwd="/x", name=sid, name_source="derived", status=status,
        status_updated_at=ts, updated_at=ts, started_at=NOW - timedelta(hours=2),
        last_turn=last_turn, last_turn_ts=ts, seen_at=NOW if seen else None,
        waiting_on=waiting_on, parked_until=parked_until, is_shell=shell, iterm_title=sid.upper(), last_prompt=prompt,
        subagents_running=subagents, turn_ended=main_turn_ts is not None or last_turn == "end_turn",
        main_turn_ts=main_turn_ts or ts,
    )


def _delegating(sid, report_minutes_ago, agent_minutes_ago, **kw):
    """An orchestrator whose main turn ended `report_minutes_ago` with a
    background agent still running: sources.gather_sessions reports the
    agent's own in-progress turn as last_turn and keeps the main turn's end
    in main_turn_ts."""
    return _session(sid, last_turn="in_progress", minutes_ago=agent_minutes_ago, subagents=1,
                    main_turn_ts=NOW - timedelta(minutes=report_minutes_ago),
                    waiting_on={"kind": "subagents_running", "detail": "1"}, **kw)


LATER = NOW + hal.FINISH_HOLD


def test_first_refresh_only_sets_the_baseline():
    memory = hal.Memory()
    assert hal.remarks(memory, [_session("a")], {}, NOW) == []
    assert memory.primed and memory.statuses == {"a": "needs_you"}


def test_a_session_that_finishes_is_announced_once_and_only_if_unread():
    memory = hal.Memory()
    working = _session("a", last_turn="in_progress")
    hal.remarks(memory, [working], {}, NOW)

    done = _session("a")
    assert hal.remarks(memory, [done], {}, NOW) == []  # not yet: the finish must hold
    lines = hal.remarks(memory, [done], {}, LATER)
    assert lines[0].startswith("A has finished")
    assert hal.remarks(memory, [done], {}, LATER) == []  # same state, nothing new

    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    seen = _session("a", seen=True)  # the tab was focused as it finished
    assert hal.remarks(memory, [seen], {}, NOW) == []
    assert hal.remarks(memory, [seen], {}, LATER) == []


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
    assert lines == ["The claude account's five-hour window is at 90 percent, Frank. I would not take on anything heavy."] or "90 percent" in lines[0]
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



def test_a_session_turning_hot_is_announced_once_and_listed_for_a_checkup():
    memory = hal.Memory()
    calm = _session("a", last_turn="in_progress")
    hal.remarks(memory, [calm], {}, NOW)
    hot = _session("a", last_turn="in_progress")
    hot.recent_prompts = ["how the fuck is this still broken"]
    lines = hal.remarks(memory, [hot], {}, NOW)
    assert any("frustration in A" in l for l in lines)
    assert memory.newly_heated == ["a"]
    assert hal.remarks(memory, [hot], {}, NOW) == [] and memory.newly_heated == []


def test_session_events_are_listed_for_head_jeeves():
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    hal.remarks(memory, [_session("a")], {}, NOW)
    hal.remarks(memory, [_session("a")], {}, LATER)
    assert memory.events == [("a", "A has finished, Frank. It is waiting for you.")]
    hal.remarks(memory, [_session("a")], {}, LATER)
    assert memory.events == []


def test_a_session_seen_for_the_first_time_is_not_an_event():
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    lines = hal.remarks(memory, [_session("a", last_turn="in_progress"), _session("b")], {}, NOW)
    assert lines == [] and memory.events == []


def test_a_turn_that_ended_with_agents_still_running_is_not_finished():
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    delegating = _session("a", waiting_on={"kind": "subagents_running", "detail": "2"})
    assert hal.remarks(memory, [delegating], {}, NOW) == []
    assert memory.events == []
    # once the agents are done and the turn is really over, it counts
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    hal.remarks(memory, [_session("a")], {}, NOW)
    assert memory.events == [] and hal.remarks(memory, [_session("a")], {}, LATER)


def test_a_finish_that_does_not_hold_is_not_announced():
    # raide-build-3d-2 on 2026-10-07 17:03: the orchestrator resumed a
    # subagent with SendMessage and ended its turn. For the 3 s between the
    # main turn's end and the agent's next write, the session read as
    # finished; Head Jeeves got a false finished event. The next refresh
    # read it as working again.
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    assert hal.remarks(memory, [_session("a")], {}, NOW) == []  # the false finish
    delegating = _session("a", waiting_on={"kind": "subagents_running", "detail": "1"})
    later = NOW + timedelta(seconds=3)
    assert hal.remarks(memory, [delegating], {}, later) == []
    assert memory.events == [] and memory.unconfirmed == {}
    # A second refresh too soon after the first does not confirm it either.
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    hal.remarks(memory, [_session("a")], {}, NOW)
    assert hal.remarks(memory, [_session("a")], {}, NOW + timedelta(seconds=1)) == []
    assert hal.remarks(memory, [_session("a")], {}, LATER)[0].startswith("A has finished")


def test_a_session_whose_claude_exits_is_announced_with_its_last_words(monkeypatch):
    monkeypatch.setattr(hal, "pid_alive", lambda pid: False)
    memory = hal.Memory()
    site = _session("site", last_turn="in_progress")
    site.recap = "Deployed the site.\n\nAll green."
    hal.remarks(memory, [site], {}, NOW)

    shell = _session("shell-7", shell=True)  # its terminal, back at the prompt
    lines = hal.remarks(memory, [shell], {}, NOW)

    assert lines == ["SITE has ended, Frank. Its last words: Deployed the site. All green."]
    assert memory.events == [("site", lines[0])]
    assert hal.remarks(memory, [shell], {}, NOW) == []  # said once


def test_a_session_podbay_close_ended_is_not_announced(monkeypatch):
    from podbay import opened
    monkeypatch.setattr(hal, "pid_alive", lambda pid: False)
    memory = hal.Memory()
    hal.remarks(memory, [_session("closed"), _session("quit")], {}, NOW)

    opened.record_closed("closed", NOW)
    lines = hal.remarks(memory, [], {}, NOW)

    assert [name for name, _ in memory.events] == ["quit"]
    assert len(lines) == 1 and lines[0].startswith("QUIT has ended")


def test_a_session_missing_from_one_scan_but_still_running_is_not_ended(monkeypatch):
    monkeypatch.setattr(hal, "pid_alive", lambda pid: True)
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress")], {}, NOW)
    assert hal.remarks(memory, [], {}, NOW) == []
    assert memory.events == []


def test_a_session_that_never_had_a_prompt_is_not_announced_as_ended(monkeypatch):
    monkeypatch.setattr(hal, "pid_alive", lambda pid: False)
    memory = hal.Memory()
    hal.remarks(memory, [_session("blip", prompt=None)], {}, NOW)
    assert hal.remarks(memory, [], {}, NOW) == []
    assert memory.events == []


def test_a_usage_run_that_slipped_into_the_registry_is_not_announced_as_ended(monkeypatch):
    """jeeves-45 and jeeves-e5: a `claude -p /usage` in the jeeves cwd, no prompt, no recap."""
    monkeypatch.setattr(hal, "pid_alive", lambda pid: False)
    memory = hal.Memory()
    ghost = _session("jeeves-45", prompt=None)
    hal.remarks(memory, [ghost], {}, NOW)
    assert hal.remarks(memory, [], {}, NOW) == []
    assert memory.events == []


PROMPT = {"kind": "prompt", "detail": "a question or permission dialog is open (not in the transcript yet)"}


def test_a_permission_dialog_is_announced_although_no_turn_ended():
    # jeeves-notify-reply, 2026-10-08 12:31: a Skill call opened an install
    # dialog. The registry went "waiting"; the transcript's newest record
    # stayed the tool call, so the turn never ended and `unread` was false.
    # The session sat there for six hours and Head Jeeves got no event.
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress", status="busy")], {}, NOW)
    dialog = _session("a", last_turn="in_progress", status="waiting", waiting_on=PROMPT)
    lines = hal.remarks(memory, [dialog], {}, NOW)
    assert lines == ["A has a question for you, Frank."]
    assert memory.events == [("a", lines[0])]
    assert hal.remarks(memory, [dialog], {}, NOW + timedelta(hours=1)) == []  # said once
    # the same dialog, once it has sat long enough to be read as a permission
    memory = hal.Memory()
    hal.remarks(memory, [_session("a", last_turn="in_progress", status="busy")], {}, NOW)
    permission = _session("a", last_turn="in_progress", status="waiting", minutes_ago=3,
                          waiting_on={"kind": "permission", "detail": "Bash: grep"})
    assert hal.remarks(memory, [permission], {}, NOW) == ["A is waiting for your permission, Frank."]


def test_a_report_with_agents_running_and_then_their_permission_dialog_are_both_announced():
    # raide-build-3d-2, 2026-10-08: at 09:46 the orchestrator reported step 3
    # live, launched the step 4 agent and ended its turn; at 10:30 that agent
    # asked for a Bash permission and the session sat at the dialog for eight
    # hours. Neither raised an event: the report because the agents still ran,
    # the dialog because the agent's turn had not ended.
    memory = hal.Memory()
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy")], {}, NOW)
    t1 = NOW + timedelta(minutes=1)
    lines = hal.remarks(memory, [_delegating("r", 0, 0)], {}, t1)
    assert lines == ["R has reported, Frank. Its agents are still at work."]
    assert memory.events == [("r", lines[0])]
    assert hal.remarks(memory, [_delegating("r", 0, 0)], {}, t1 + timedelta(seconds=3)) == []  # once
    # the agent asks for a permission: the registry says waiting, the newest
    # record is the agent's tool call
    t2 = NOW + timedelta(minutes=44)
    dialog = _session("r", last_turn="in_progress", status="waiting", minutes_ago=-44, subagents=1,
                      main_turn_ts=NOW, waiting_on=PROMPT)
    assert dialog.derive_status(t2) == "needs_you"
    lines = hal.remarks(memory, [dialog], {}, t2)
    assert lines == ["R has a question for you, Frank."]
    assert hal.remarks(memory, [dialog], {}, t2 + timedelta(hours=8)) == []


def test_each_report_is_announced_once_and_a_finish_still_waits_for_the_agents():
    memory = hal.Memory()
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy")], {}, NOW)
    assert len(hal.remarks(memory, [_delegating("r", 5, 0)], {}, NOW)) == 1
    # the agent reports back, the orchestrator works a new turn, reports again
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy", minutes_ago=0)], {}, NOW)
    assert hal.remarks(memory, [_delegating("r", 0, 0)], {}, NOW) == ["R has reported, Frank. Its agents are still at work."]
    # a report the user watched being written is not announced
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy", minutes_ago=0)], {}, NOW)
    assert hal.remarks(memory, [_delegating("r", 1, 0, seen=True)], {}, NOW) == []
    # the agents done and the turn over: the finish, held as before
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy", minutes_ago=0)], {}, NOW)
    assert hal.remarks(memory, [_session("r", minutes_ago=-2)], {}, NOW) == []
    assert hal.remarks(memory, [_session("r", minutes_ago=-2)], {}, LATER) == ["R has finished, Frank. It is waiting for you."]


def test_a_report_seen_on_the_first_refresh_is_the_baseline():
    memory = hal.Memory()
    assert hal.remarks(memory, [_delegating("r", 5, 0)], {}, NOW) == []
    assert hal.remarks(memory, [_delegating("r", 5, 0)], {}, NOW) == []
    assert memory.events == []


def test_one_turn_raises_one_event_while_the_agent_count_flickers():
    # raide-build-3d-2, 2026-10-08 18:39: the orchestrator sent its running
    # subagent a SendMessage and ended its turn. The agent count read 1, then
    # 0 for longer than FINISH_HOLD, then 1 again, and Head Jeeves got
    # "finished" and then "reported" for that one turn.
    memory = hal.Memory()
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy")], {}, NOW)
    assert hal.remarks(memory, [_delegating("r", 0, 0)], {}, NOW) == ["R has reported, Frank. Its agents are still at work."]
    looks_done = _session("r", minutes_ago=0)  # same main turn, no agent counted
    assert looks_done.main_turn_ts == NOW
    assert hal.remarks(memory, [looks_done], {}, NOW + timedelta(seconds=3)) == []
    assert hal.remarks(memory, [looks_done], {}, LATER + timedelta(seconds=3)) == []
    assert hal.remarks(memory, [_delegating("r", 0, 0)], {}, LATER + timedelta(seconds=6)) == []
    assert memory.events == []
    # a new turn is a new event
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy", minutes_ago=-1)], {}, NOW)
    assert hal.remarks(memory, [_delegating("r", -1, 0)], {}, NOW) == ["R has reported, Frank. Its agents are still at work."]


def test_a_finished_turn_is_not_reported_again_when_agents_appear():
    memory = hal.Memory()
    hal.remarks(memory, [_session("r", last_turn="in_progress", status="busy")], {}, NOW)
    done = _session("r", minutes_ago=0)
    hal.remarks(memory, [done], {}, NOW)
    assert hal.remarks(memory, [done], {}, LATER) == ["R has finished, Frank. It is waiting for you."]
    assert hal.remarks(memory, [_delegating("r", 0, 0)], {}, LATER) == []
