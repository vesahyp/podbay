import json
import os
import time
from datetime import datetime, timedelta

from podbay.iterm import TabInfo
from podbay.sources import (
    _cwds_for_pids,
    compute_waiting_on,
    count_running_subagents,
    gather_sessions,
    model_name_from_id,
    newest_limits,
    read_status_snapshots,
    subagent_activity,
    tail_read_transcript,
    transcript_path_for,
)
from podbay.state import StateStore
from pathlib import Path

REPOS = str(Path.home() / "Repositories")


def _line(record: dict) -> str:
    return json.dumps(record)


def _expected_local(ts: str) -> datetime:
    """Mirrors tail_read_transcript's own UTC -> naive local conversion, so
    tests assert the right record was picked without hardcoding a timezone."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone().replace(tzinfo=None)


def test_tail_read_picks_newest_recap_and_prompt(tmp_path):
    records = [
        {"type": "last-prompt", "lastPrompt": "first prompt", "gitBranch": "main"},
        {"type": "system", "subtype": "away_summary", "content": "old recap", "timestamp": "2026-09-09T09:00:00Z"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "an assistant reply"}]}},
        {"type": "last-prompt", "lastPrompt": "second prompt", "gitBranch": "feature/x"},
        {"type": "system", "subtype": "away_summary", "content": "new recap", "timestamp": "2026-09-09T10:00:00Z"},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path, tail_bytes=1024 * 1024)

    assert result["recap"] == "new recap"
    assert result["recap_ts"] == "2026-09-09T10:00:00Z"
    assert result["last_prompt"] == "second prompt"
    assert result["git_branch"] == "feature/x"


def test_tail_read_falls_back_to_last_assistant_text_when_no_recap(tmp_path):
    records = [
        {"type": "last-prompt", "lastPrompt": "hello"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first answer"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "final answer"}]}},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["recap"] == "final answer"
    assert result["last_assistant_text"] == "final answer"


def test_tail_read_drops_partial_first_line_when_truncated(tmp_path):
    good = {"type": "last-prompt", "lastPrompt": "kept"}
    records = [good] * 50
    path = tmp_path / "session.jsonl"
    # Prepend a deliberately huge junk line that will get split by the seek.
    junk_prefix = "x" * 200
    content = junk_prefix + "\n" + "\n".join(_line(r) for r in records) + "\n"
    path.write_text(content)

    # tail_bytes small enough to land inside the junk line, forcing a partial
    # first line that must be dropped rather than fail to parse.
    result = tail_read_transcript(path, tail_bytes=len(content) - 50)

    assert result["last_prompt"] == "kept"


def test_transcript_path_for_direct_hit(tmp_path):
    projects_dir = tmp_path / "projects"
    slug_dir = projects_dir / "-Users-me-jeeves"
    slug_dir.mkdir(parents=True)
    session_file = slug_dir / "abc-123.jsonl"
    session_file.write_text("{}\n")

    found = transcript_path_for(f"{REPOS}/jeeves", "abc-123", projects_dir)

    assert found == session_file


def test_transcript_path_for_falls_back_to_glob(tmp_path):
    projects_dir = tmp_path / "projects"
    other_slug_dir = projects_dir / "-some-other-cwd"
    other_slug_dir.mkdir(parents=True)
    session_file = other_slug_dir / "xyz-789.jsonl"
    session_file.write_text("{}\n")

    # cwd doesn't match where the file actually lives; glob fallback should
    # still find it by session id.
    found = transcript_path_for(f"{REPOS}/jeeves", "xyz-789", projects_dir)

    assert found == session_file


def test_transcript_path_for_no_match_returns_none(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    found = transcript_path_for(f"{REPOS}/jeeves", "nope", projects_dir)

    assert found is None


def test_tail_read_last_turn_end_turn(tmp_path):
    records = [
        {"type": "user", "timestamp": "2026-09-09T09:00:00Z", "message": {"content": "hi"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:05:00Z",
            "message": {
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            },
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["last_turn"] == "end_turn"
    assert result["last_turn_ts"] == _expected_local("2026-09-09T09:05:00Z")


def test_tail_read_last_turn_in_progress_on_tool_use(tmp_path):
    records = [
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:10:00Z",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {}}],
                "stop_reason": "tool_use",
            },
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["last_turn"] == "in_progress"
    assert result["last_turn_ts"] == _expected_local("2026-09-09T09:10:00Z")


def test_tail_read_last_turn_in_progress_on_trailing_user_record(tmp_path):
    # A user record after a completed assistant turn means a prompt (or a
    # tool result) is sitting there awaiting the model.
    records = [
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:00:00Z",
            "message": {"content": [{"type": "text", "text": "answer"}], "stop_reason": "end_turn"},
        },
        {"type": "user", "timestamp": "2026-09-09T09:15:00Z", "message": {"content": "next question"}},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["last_turn"] == "in_progress"
    assert result["last_turn_ts"] == _expected_local("2026-09-09T09:15:00Z")


def test_tail_read_last_turn_none_when_only_sidechain_records(tmp_path):
    records = [
        {
            "type": "assistant",
            "isSidechain": True,
            "timestamp": "2026-09-09T09:00:00Z",
            "message": {"content": [{"type": "text", "text": "sidechain"}], "stop_reason": "end_turn"},
        },
        {
            "type": "user",
            "isSidechain": True,
            "timestamp": "2026-09-09T09:01:00Z",
            "message": {"content": "sidechain question"},
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["last_turn"] is None
    assert result["last_turn_ts"] is None


def test_tail_read_last_turn_ignores_later_sidechain_record(tmp_path):
    records = [
        {
            "type": "assistant",
            "timestamp": "2026-09-09T09:00:00Z",
            "message": {"content": [{"type": "text", "text": "real answer"}], "stop_reason": "end_turn"},
        },
        {
            "type": "assistant",
            "isSidechain": True,
            "timestamp": "2026-09-09T09:30:00Z",
            "message": {"content": [{"type": "text", "text": "sidechain noise"}], "stop_reason": "end_turn"},
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["last_turn"] == "end_turn"
    assert result["last_turn_ts"] == _expected_local("2026-09-09T09:00:00Z")


def test_tail_read_last_turn_none_when_no_user_or_assistant_records(tmp_path):
    records = [
        {"type": "last-prompt", "lastPrompt": "hello"},
        {"type": "system", "subtype": "away_summary", "content": "recap", "timestamp": "2026-09-09T09:00:00Z"},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["last_turn"] is None
    assert result["last_turn_ts"] is None


def _write_snapshot(path, session_id, ctx_pct=None, five=None, week=None):
    data = {"session_id": session_id}
    if ctx_pct is not None:
        data["context_window"] = {"used_percentage": ctx_pct}
    rate_limits = {}
    if five is not None:
        rate_limits["five_hour"] = five
    if week is not None:
        rate_limits["seven_day"] = week
    if rate_limits:
        data["rate_limits"] = rate_limits
    path.write_text(json.dumps(data))


def test_read_status_snapshots_two_sessions(tmp_path):
    _write_snapshot(
        tmp_path / "sess-a.json",
        "sess-a",
        ctx_pct=47,
        five={"used_percentage": 32, "resets_at": 1234567890},
        week={"used_percentage": 61, "resets_at": 1234999999},
    )
    _write_snapshot(tmp_path / "sess-b.json", "sess-b", ctx_pct=12)

    result = read_status_snapshots(tmp_path)

    assert set(result) == {"sess-a", "sess-b"}
    assert result["sess-a"]["context_pct"] == 47
    assert result["sess-a"]["five_pct"] == 32
    assert result["sess-a"]["five_resets_at"] == 1234567890
    assert result["sess-a"]["week_pct"] == 61
    assert result["sess-a"]["week_resets_at"] == 1234999999
    assert "mtime" in result["sess-a"]

    assert result["sess-b"]["context_pct"] == 12
    assert result["sess-b"]["five_pct"] is None
    assert result["sess-b"]["week_pct"] is None


def test_read_status_snapshots_missing_dir_returns_empty(tmp_path):
    assert read_status_snapshots(tmp_path / "nope") == {}


def test_read_status_snapshots_prunes_old_files(tmp_path):
    old_path = tmp_path / "sess-old.json"
    _write_snapshot(old_path, "sess-old", ctx_pct=50)
    fresh_path = tmp_path / "sess-fresh.json"
    _write_snapshot(fresh_path, "sess-fresh", ctx_pct=10)

    old_time = time.time() - 15 * 86400  # older than SNAPSHOT_PRUNE_AFTER_DAYS (14)
    os.utime(old_path, (old_time, old_time))

    result = read_status_snapshots(tmp_path)

    assert "sess-old" not in result
    assert "sess-fresh" in result
    assert not old_path.exists()  # pruned from disk, not just skipped
    assert fresh_path.exists()


def _snap(model=None, five_pct=None, five_resets_at=None, week_pct=None, week_resets_at=None, mtime=0.0):
    return {
        "model": model,
        "five_pct": five_pct,
        "five_resets_at": five_resets_at,
        "week_pct": week_pct,
        "week_resets_at": week_resets_at,
        "mtime": mtime,
    }


def test_newest_limits_five_hour_uses_newest_snapshot():
    snapshots = {
        "a": _snap(model="Opus 5", five_pct=10, five_resets_at=100, mtime=1),
        "b": _snap(model="Fable 5.1", five_pct=53, five_resets_at=200, mtime=2),
    }
    limits = newest_limits(snapshots)
    assert limits["five_pct"] == 53
    assert limits["five_resets_at"] == 200


def test_newest_limits_week_uses_newest_snapshot():
    snapshots = {
        "a": _snap(model="Opus 5", week_pct=8, week_resets_at=100, mtime=1),
        "b": _snap(model="Fable 5.1", week_pct=34, week_resets_at=200, mtime=2),
    }
    limits = newest_limits(snapshots)
    assert limits["week_pct"] == 34
    assert limits["week_resets_at"] == 200


def test_newest_limits_snapshot_with_only_one_figure_does_not_clobber_the_other():
    # A snapshot often carries only one of the two figures; a newer snapshot
    # with only a five-hour reading must not blank out an older week reading.
    snapshots = {
        "older": _snap(model="Opus 5", week_pct=34, week_resets_at=200, mtime=1),
        "newer": _snap(model="Opus 5", five_pct=53, five_resets_at=100, mtime=2),
    }
    limits = newest_limits(snapshots)
    assert limits["five_pct"] == 53
    assert limits["five_resets_at"] == 100
    assert limits["week_pct"] == 34
    assert limits["week_resets_at"] == 200


def test_newest_limits_blank_when_nothing_known():
    assert newest_limits({}) == {
        "five_pct": None,
        "five_resets_at": None,
        "week_pct": None,
        "week_resets_at": None,
    }


# -- subagent activity: a background agent keeps the session working ----------


def _agent_records(last_stop_reason: str):
    return [
        {"type": "user", "isSidechain": True, "timestamp": "2026-09-10T07:00:00Z", "message": {"content": "task"}},
        {
            "type": "assistant",
            "isSidechain": True,
            "timestamp": "2026-09-10T07:10:00Z",
            "message": {"content": [{"type": "tool_use", "name": "Bash"}], "stop_reason": last_stop_reason},
        },
    ]


def _write_session_with_agent(tmp_path, agent_stop_reason, agent_mtime=None):
    main = tmp_path / "s1.jsonl"
    main.write_text(_line({"type": "assistant", "timestamp": "2026-09-10T07:00:30Z",
                           "message": {"content": [{"type": "text", "text": "running"}], "stop_reason": "end_turn"}}) + "\n")
    sub_dir = tmp_path / "s1" / "subagents"
    sub_dir.mkdir(parents=True)
    agent = sub_dir / "agent-abc.jsonl"
    agent.write_text("\n".join(_line(r) for r in _agent_records(agent_stop_reason)) + "\n")
    if agent_mtime is not None:
        os.utime(agent, (agent_mtime, agent_mtime))
    return main


def test_tail_read_include_sidechain_reads_subagent_records(tmp_path):
    path = tmp_path / "agent.jsonl"
    path.write_text("\n".join(_line(r) for r in _agent_records("tool_use")) + "\n")
    assert tail_read_transcript(path)["last_turn"] is None
    assert tail_read_transcript(path, include_sidechain=True)["last_turn"] == "in_progress"


def test_subagent_activity_in_progress_agent(tmp_path):
    main = _write_session_with_agent(tmp_path, "tool_use")
    main_ts = _expected_local("2026-09-10T07:00:30Z")
    assert subagent_activity(main, "s1", main_ts) == ("in_progress", _expected_local("2026-09-10T07:10:00Z"))


def test_subagent_activity_finished_agent_is_none(tmp_path):
    main = _write_session_with_agent(tmp_path, "end_turn")
    assert subagent_activity(main, "s1", _expected_local("2026-09-10T07:00:30Z")) is None


def test_subagent_activity_skips_file_older_than_main_turn(tmp_path):
    main = _write_session_with_agent(tmp_path, "tool_use", agent_mtime=time.time() - 3600)
    assert subagent_activity(main, "s1", datetime.now()) is None


def test_subagent_activity_no_subagents_dir(tmp_path):
    main = tmp_path / "s1.jsonl"
    main.write_text("")
    assert subagent_activity(main, "s1", None) is None


# -- seen_at: the current iTerm tab marks a finished answer as seen ----------


def test_mark_seen_if_unread_writes_once(tmp_path):
    from podbay.sources import _mark_seen_if_unread

    store = StateStore(tmp_path / "state.json")
    transcript = {"last_turn": "end_turn", "last_turn_ts": datetime(2026, 9, 1, 9, 0)}
    saved = _mark_seen_if_unread(store, "s1", transcript)
    assert saved.seen_at is not None and saved.seen_at > transcript["last_turn_ts"]
    first = saved.seen_at
    assert _mark_seen_if_unread(store, "s1", transcript).seen_at == first
    assert StateStore(tmp_path / "state.json").get("s1").seen_at == first


def test_mark_seen_if_unread_ignores_in_progress(tmp_path):
    from podbay.sources import _mark_seen_if_unread

    store = StateStore(tmp_path / "state.json")
    transcript = {"last_turn": "in_progress", "last_turn_ts": datetime(2026, 9, 11, 9, 0)}
    assert _mark_seen_if_unread(store, "s1", transcript).seen_at is None


# -- model name ---------------------------------------------------------------


def test_model_name_from_id_variants():
    assert model_name_from_id("claude-fable-5-1") == "Fable 5.1"
    assert model_name_from_id("claude-sonnet-5") == "Sonnet 5"
    assert model_name_from_id("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert model_name_from_id(None) is None


def test_tail_read_picks_model_id_from_assistant_record(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(_line({"type": "assistant", "timestamp": "2026-09-10T07:00:00Z",
                           "message": {"model": "claude-fable-5-1", "content": [], "stop_reason": "end_turn"}}) + "\n")
    assert tail_read_transcript(path)["model_id"] == "claude-fable-5-1"


def test_read_status_snapshots_model_and_effort(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"session_id": "s", "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
                                "effort": {"level": "high"}}))
    result = read_status_snapshots(tmp_path)
    assert result["s"]["model"] == "Fable 5.1"
    assert result["s"]["effort"] == "high"


# -- gather_sessions: left join with every iTerm2 pane ------------------------


class _FakeLister:
    """Stands in for iterm.ItermLister without shelling out to osascript:
    same public surface (tabs, windows, tabs_for_pids) gather_sessions calls."""

    def __init__(self, tabs_by_tty: dict, tabs_by_pid: dict | None = None, windows: dict | None = None):
        self._tabs_by_tty = tabs_by_tty
        self._tabs_by_pid = tabs_by_pid or {}
        self._windows = windows or {}

    def tabs(self, force: bool = False) -> dict:
        return self._tabs_by_tty

    def windows(self, force: bool = False) -> dict:
        return self._windows

    def tabs_for_pids(self, pids: list[int]) -> dict:
        return {pid: self._tabs_by_pid.get(pid) for pid in pids}


def _write_registry_entry(sessions_dir, session_id, pid, cwd="/tmp", status="idle"):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{pid}.json").write_text(json.dumps({
        "sessionId": session_id, "pid": pid, "cwd": cwd, "name": session_id,
        "nameSource": "derived", "status": status,
    }))


def test_gather_sessions_unmatched_pane_becomes_shell_row(tmp_path):
    sessions_dir = tmp_path / "sessions"  # left empty: no registry entries
    projects_dir = tmp_path / "projects"
    lister = _FakeLister({"/dev/ttys001": TabInfo(tty="/dev/ttys001", tab_id="t1", title="zsh")})

    sessions = gather_sessions(
        StateStore(tmp_path / "state.json"),
        lister,
        sessions_dir=sessions_dir,
        projects_dir=projects_dir,
        status_snapshots={},
        pid_by_tty=lambda: {"/dev/ttys001": 4242},
        cwd_by_pids=lambda pids: {4242: "/Users/me/project"} if 4242 in pids else {},
    )

    assert len(sessions) == 1
    s = sessions[0]
    assert s.is_shell is True
    assert s.session_id == "tty:/dev/ttys001"
    assert s.pid == 4242
    assert s.cwd == "/Users/me/project"
    assert s.tty == "/dev/ttys001"
    assert s.has_transcript is False
    assert s.name == "zsh"
    assert s.window_id is None  # TabInfo without window_id yet: getattr falls back


def test_gather_sessions_pane_matching_live_session_not_duplicated(tmp_path):
    sessions_dir = tmp_path / "sessions"
    projects_dir = tmp_path / "projects"
    pid = os.getpid()  # a pid _pid_alive will consider live
    _write_registry_entry(sessions_dir, "s1", pid)
    tab = TabInfo(tty="/dev/ttys002", tab_id="t2", title="claude")
    lister = _FakeLister({"/dev/ttys002": tab}, tabs_by_pid={pid: tab})

    sessions = gather_sessions(
        StateStore(tmp_path / "state.json"),
        lister,
        sessions_dir=sessions_dir,
        projects_dir=projects_dir,
        status_snapshots={},
        pid_by_tty=lambda: {"/dev/ttys002": pid},
        cwd_by_pids=lambda pids: {},
    )

    assert len(sessions) == 1
    assert sessions[0].session_id == "s1"
    assert sessions[0].is_shell is False


def test_gather_sessions_cwd_lookup_failure_is_graceful(tmp_path):
    sessions_dir = tmp_path / "sessions"
    projects_dir = tmp_path / "projects"
    lister = _FakeLister({"/dev/ttys003": TabInfo(tty="/dev/ttys003", tab_id="t3", title="bash")})

    sessions = gather_sessions(
        StateStore(tmp_path / "state.json"),
        lister,
        sessions_dir=sessions_dir,
        projects_dir=projects_dir,
        status_snapshots={},
        pid_by_tty=lambda: {"/dev/ttys003": 9999},
        cwd_by_pids=lambda pids: {},  # simulates lsof failing/finding nothing
    )

    assert len(sessions) == 1
    assert sessions[0].pid == 9999
    assert sessions[0].cwd == ""


def test_cwds_for_pids_subprocess_failure_returns_empty(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no lsof")

    monkeypatch.setattr("podbay.sources.subprocess.run", boom)
    assert _cwds_for_pids([123]) == {}


def test_busy_ttys_flags_foreground_commands_only(monkeypatch):
    from podbay import sources

    output = "\n".join([
        "ttys000 Ss+ -zsh",          # idle shell: free
        "ttys001 S+ claude",          # foreground command: busy
        "ttys002 S node",             # background, not the foreground group: free
        "??      Ss  launchd",        # no tty at all
        "garbage",
    ])

    class _Result:
        stdout = output

    monkeypatch.setattr(sources.subprocess, "run", lambda *_a, **_k: _Result())
    assert sources.busy_ttys() == {"/dev/ttys001"}


def test_shell_session_carries_the_window_number(monkeypatch, tmp_path):
    from podbay import iterm as iterm_mod
    from podbay import sources
    from podbay.state import StateStore

    tab = iterm_mod.TabInfo(
        tty="/dev/ttys004", tab_id="A", title="(-zsh)", window_id="281", tab_index=1, path=f"{REPOS}/jeeves"
    )
    windows = {"281": iterm_mod.WindowInfo(window_id="281", bounds=(0, 0, 100, 100), tab_count=1, number=12)}
    lister = _FakeLister({"/dev/ttys004": tab}, windows=windows)

    sessions = sources.gather_sessions(
        StateStore(tmp_path / "s.json"),
        lister,
        sessions_dir=tmp_path / "none",
        projects_dir=tmp_path / "proj",
        status_snapshots={},
        pid_by_tty=lambda: {"/dev/ttys004": 4242},
        cwd_by_pids=lambda _pids: {},
    )

    assert [s.window_number for s in sessions] == [12]
    assert sessions[0].cwd == f"{REPOS}/jeeves"


# -- repos touched/edited and waiting_on (ported from the coordinator inventory) --


def test_tail_read_transcript_collects_repos_and_waiting_on(tmp_path):
    records = [
        {
            "type": "assistant",
            "timestamp": "2026-09-18T09:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use", "id": "tu1", "name": "Bash",
                        "input": {"command": f"ls {REPOS}/data-platform/x"},
                    },
                    {
                        "type": "tool_use", "id": "tu2", "name": "Edit",
                        "input": {"file_path": f"{REPOS}/jeeves/TODO.md", "old_string": "a", "new_string": "b"},
                    },
                    {
                        "type": "tool_use", "id": "tu3", "name": "AskUserQuestion",
                        "input": {"questions": [{"question": "Which repo?"}]},
                    },
                ],
                "stop_reason": "tool_use",
            },
        },
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    result = tail_read_transcript(path)

    assert result["repos_touched"] == {"data-platform", "jeeves"}
    assert result["repos_edited"] == {"jeeves"}
    assert result["resolved_tool_use_ids"] == set()
    assert result["newest_assistant"]["stop_reason"] == "tool_use"
    assert {b["name"] for b in result["newest_assistant"]["tool_uses"]} == {"Bash", "Edit", "AskUserQuestion"}

    waiting = compute_waiting_on(result, "idle", 5.0, 0)
    assert waiting == {"kind": "ask_user_question", "detail": "Which repo?"}


def test_compute_waiting_on_permission_when_idle_past_threshold():
    transcript = {
        "newest_assistant": {
            "stop_reason": "tool_use",
            "text": "",
            "tool_uses": [{"id": "tu1", "name": "Bash", "input": {"command": "ls /tmp"}}],
        },
        "resolved_tool_use_ids": set(),
    }
    assert compute_waiting_on(transcript, "idle", 5.0, 0) == {"kind": "permission", "detail": "Bash: ls /tmp"}
    assert compute_waiting_on(transcript, "busy", 5.0, 0) is None
    assert compute_waiting_on(transcript, "idle", 0.5, 0) is None


def test_compute_waiting_on_question_text_when_turn_ended_with_trailing_question():
    transcript = {
        "newest_assistant": {"stop_reason": "end_turn", "text": "Done. Should I proceed?", "tool_uses": []},
        "resolved_tool_use_ids": set(),
    }
    assert compute_waiting_on(transcript, "idle", None, 0) == {"kind": "question_text", "detail": "Should I proceed?"}


def test_compute_waiting_on_subagents_running_fallback():
    transcript = {"newest_assistant": None, "resolved_tool_use_ids": set()}
    assert compute_waiting_on(transcript, "idle", None, 2) == {"kind": "subagents_running", "detail": "2"}
    assert compute_waiting_on(transcript, "idle", None, 0) is None


def test_count_running_subagents_counts_files_newer_than_cutoff(tmp_path):
    slug_dir = tmp_path
    sub_dir = slug_dir / "s1" / "subagents"
    sub_dir.mkdir(parents=True)
    old = sub_dir / "old.jsonl"
    new = sub_dir / "new.jsonl"
    old.write_text("{}\n")
    new.write_text("{}\n")
    cutoff = old.stat().st_mtime + 10
    os.utime(new, (cutoff + 5, cutoff + 5))

    count = count_running_subagents(slug_dir, "s1", datetime.fromtimestamp(cutoff))

    assert count == 1


def _resume_records(agent_id: str, notified: bool = False) -> list[dict]:
    records = [
        {"type": "assistant", "timestamp": "2026-09-10T07:00:00Z",
         "message": {"content": [{"type": "tool_use", "id": "tu9", "name": "SendMessage",
                                  "input": {"to": agent_id, "message": "one more thing"}}],
                     "stop_reason": "tool_use"}},
        {"type": "user", "timestamp": "2026-09-10T07:00:01Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "tu9", "content": "ok"}]},
         "toolUseResult": {"success": True, "message": f"Resuming agent {agent_id[:7]}", "resumedAgentId": agent_id}},
        {"type": "assistant", "timestamp": "2026-09-10T07:00:05Z",
         "message": {"content": [{"type": "text", "text": "Sent."}], "stop_reason": "end_turn"}},
    ]
    if notified:
        records.append({"type": "user", "timestamp": "2026-09-10T07:05:00Z",
                        "message": {"content": f"<task-notification>\n<task-id>{agent_id}</task-id>\n"
                                               "<status>completed</status></task-notification>"}})
    return records


def test_tail_read_notes_an_agent_resumed_by_send_message(tmp_path):
    path = tmp_path / "s1.jsonl"
    path.write_text("\n".join(_line(r) for r in _resume_records("a1b2c3d4e5")) + "\n")
    assert set(tail_read_transcript(path)["resumed_agents"]) == {"a1b2c3d4e5"}


def test_tail_read_forgets_a_resumed_agent_once_it_reports(tmp_path):
    path = tmp_path / "s1.jsonl"
    path.write_text("\n".join(_line(r) for r in _resume_records("a1b2c3d4e5", notified=True)) + "\n")
    assert tail_read_transcript(path)["resumed_agents"] == {}


def test_count_running_subagents_counts_a_resumed_agent_not_yet_writing(tmp_path):
    sub_dir = tmp_path / "s1" / "subagents"
    sub_dir.mkdir(parents=True)
    agent = sub_dir / "agent-a1.jsonl"
    agent.write_text("{}\n")
    resumed_at = datetime.now()
    os.utime(agent, (resumed_at.timestamp() - 600, resumed_at.timestamp() - 600))
    main_turn_ended = resumed_at + timedelta(seconds=5)

    assert count_running_subagents(tmp_path, "s1", main_turn_ended, {"a1": resumed_at}) == 1
    # Long past the grace, a resume that never started is not work.
    later = resumed_at + timedelta(hours=1)
    assert count_running_subagents(tmp_path, "s1", main_turn_ended, {"a1": resumed_at}, now=later) == 0


def test_count_running_subagents_counts_a_resumed_agent_once(tmp_path):
    sub_dir = tmp_path / "s1" / "subagents"
    sub_dir.mkdir(parents=True)
    agent = sub_dir / "agent-a1.jsonl"
    agent.write_text("{}\n")
    resumed_at = datetime.now() - timedelta(seconds=30)
    main_turn_ended = resumed_at + timedelta(seconds=5)

    assert count_running_subagents(tmp_path, "s1", main_turn_ended, {"a1": resumed_at}) == 1


def test_gather_sessions_sets_repos_and_waiting_on(tmp_path):
    sessions_dir = tmp_path / "sessions"
    projects_dir = tmp_path / "projects"
    pid = os.getpid()
    _write_registry_entry(sessions_dir, "s1", pid, cwd=f"{REPOS}/jeeves")

    slug_dir = projects_dir / "-Users-me-jeeves"
    slug_dir.mkdir(parents=True)
    records = [
        {
            "type": "assistant",
            "timestamp": "2026-09-18T09:00:00Z",
            "cwd": f"{REPOS}/jeeves",
            "message": {
                "content": [
                    {
                        "type": "tool_use", "id": "tu1", "name": "AskUserQuestion",
                        "input": {"questions": [{"question": "Which one?"}]},
                    },
                ],
                "stop_reason": "tool_use",
            },
        },
    ]
    (slug_dir / "s1.jsonl").write_text("\n".join(_line(r) for r in records) + "\n")

    lister = _FakeLister({})
    sessions = gather_sessions(
        StateStore(tmp_path / "state.json"),
        lister,
        sessions_dir=sessions_dir,
        projects_dir=projects_dir,
        status_snapshots={},
        pid_by_tty=lambda: {},
        cwd_by_pids=lambda pids: {},
    )

    assert len(sessions) == 1
    s = sessions[0]
    assert s.repos_touched == ["jeeves"]
    assert s.repos_edited == []
    assert s.waiting_on == {"kind": "ask_user_question", "detail": "Which one?"}
    assert s.turn_ended is False


def test_compute_waiting_on_prompt_from_registry_waiting_status():
    # An open AskUserQuestion or permission dialog is not in the transcript
    # until answered: the transcript ends at a resolved tool result.
    transcript = {
        "newest_assistant": {
            "stop_reason": "tool_use",
            "text": "",
            "tool_uses": [{"id": "tu1", "name": "Bash", "input": {"command": "ls"}}],
        },
        "resolved_tool_use_ids": {"tu1"},
    }
    assert compute_waiting_on(transcript, "waiting", 0.5, 1) == {
        "kind": "prompt",
        "detail": "a question or permission dialog is open (not in the transcript yet)",
    }
    assert compute_waiting_on(transcript, "idle", 0.5, 1) == {"kind": "subagents_running", "detail": "1"}


def test_interrupted_turn_counts_as_ended(tmp_path):
    path = tmp_path / "s.jsonl"
    records = [
        {"type": "user", "timestamp": "2026-09-18T09:14:40.000Z", "message": {"role": "user", "content": "do it"}},
        {"type": "assistant", "timestamp": "2026-09-18T09:14:52.000Z",
         "message": {"role": "assistant", "stop_reason": None,
                     "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "timestamp": "2026-09-18T09:14:54.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    t = tail_read_transcript(path)
    assert t["last_turn"] == "end_turn"
    assert t["interrupted"] is True
    assert compute_waiting_on(t, "idle", 5.0, 0) == {
        "kind": "interrupted", "detail": "turn interrupted by user, waiting for a new prompt"}


def test_tail_read_tracks_background_task_starts_and_ends(tmp_path):
    def result(ts, tool_use_result, content=None):
        return {"type": "user", "timestamp": ts, "toolUseResult": tool_use_result,
                "message": {"content": content or [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]}}

    records = [
        result("2026-09-23T09:00:00Z", {"stdout": "", "backgroundTaskId": "bdone"}),
        result("2026-09-23T09:01:00Z", {"stdout": "", "backgroundTaskId": "brun"}),
        result("2026-09-23T09:02:00Z", {"taskId": "bmon", "timeoutMs": 540000, "persistent": False}),
        result("2026-09-23T09:03:00Z", {"stdout": "", "backgroundTaskId": "bstop"}),
        {"type": "user", "timestamp": "2026-09-23T09:04:00Z",
         "message": {"content": "<task-notification>\n<task-id>bdone</task-id>\n<status>completed</status>\n</task-notification>"}},
        result("2026-09-23T09:05:00Z", {"message": "Successfully stopped task: bstop", "task_id": "bstop"}),
        result("2026-09-23T09:06:00Z", {"stdout": "", "backgroundTaskId": "bqueued"}),
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-09-23T09:07:00Z",
         "content": "<task-notification>\n<task-id>bqueued</task-id>\n<status>completed</status>\n</task-notification>"},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(_line(r) for r in records) + "\n")

    tasks = tail_read_transcript(path, tail_bytes=1024 * 1024)["background_tasks"]

    assert {k: v["ended"] for k, v in tasks.items()} == {"bdone": True, "brun": False, "bmon": False, "bstop": True, "bqueued": True}
    assert tasks["bmon"]["timeout_ms"] == 540000
    assert tasks["brun"]["ts"] == _expected_local("2026-09-23T09:01:00Z")
