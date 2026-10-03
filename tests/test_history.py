import json
import json
import os
import time
from dataclasses import dataclass

from podbay import history
from podbay.history import PastSession, list_past_sessions, search_sessions


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _set_mtime(path, days_ago):
    ts = time.time() - days_ago * 86400
    os.utime(path, (ts, ts))


def _user_record(text, cwd=None):
    record = {"type": "user", "message": {"role": "user", "content": text}}
    if cwd is not None:
        record["cwd"] = cwd
    return record


def _away_summary(content, cwd=None):
    record = {"type": "system", "subtype": "away_summary", "content": content}
    if cwd is not None:
        record["cwd"] = cwd
    return record


def test_list_past_sessions_orders_newest_first(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    a = proj / "aaa.jsonl"
    b = proj / "bbb.jsonl"
    _write_jsonl(a, [_user_record("first one", cwd="/Users/vesa/repo")])
    _write_jsonl(b, [_user_record("second one", cwd="/Users/vesa/repo")])
    _set_mtime(a, days_ago=5)
    _set_mtime(b, days_ago=1)

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert [s.session_id for s in sessions] == ["bbb", "aaa"]


def test_list_past_sessions_excludes_live_ids(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    live = proj / "live.jsonl"
    dead = proj / "dead.jsonl"
    _write_jsonl(live, [_user_record("still running", cwd="/Users/vesa/repo")])
    _write_jsonl(dead, [_user_record("finished", cwd="/Users/vesa/repo")])

    sessions = list_past_sessions(projects_dir=tmp_path, exclude_ids={"live"}, since_days=None)
    assert [s.session_id for s in sessions] == ["dead"]


def test_list_past_sessions_respects_limit(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    for i in range(5):
        p = proj / f"s{i}.jsonl"
        _write_jsonl(p, [_user_record(f"prompt {i}", cwd="/Users/vesa/repo")])
        _set_mtime(p, days_ago=i)

    sessions = list_past_sessions(projects_dir=tmp_path, limit=2, since_days=None)
    assert len(sessions) == 2
    assert [s.session_id for s in sessions] == ["s0", "s1"]


def test_list_past_sessions_drops_older_than_cutoff(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    recent = proj / "recent.jsonl"
    old = proj / "old.jsonl"
    _write_jsonl(recent, [_user_record("recent one", cwd="/Users/vesa/repo")])
    _write_jsonl(old, [_user_record("old one", cwd="/Users/vesa/repo")])
    _set_mtime(recent, days_ago=1)
    _set_mtime(old, days_ago=60)

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=30)
    assert [s.session_id for s in sessions] == ["recent"]


def test_list_past_sessions_skips_empty_transcript(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    empty = proj / "empty.jsonl"
    good = proj / "good.jsonl"
    proj.mkdir(parents=True, exist_ok=True)
    empty.write_text("")
    _write_jsonl(good, [_user_record("hello", cwd="/Users/vesa/repo")])

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert [s.session_id for s in sessions] == ["good"]


def test_list_past_sessions_skips_malformed_line_but_keeps_file(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / "malformed.jsonl"
    path.write_text("not valid json at all\n" + json.dumps(_user_record("hi there", cwd="/Users/vesa/repo")) + "\n")

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert [s.session_id for s in sessions] == ["malformed"]
    assert sessions[0].title == "hi there"


def test_list_past_sessions_drops_file_with_only_malformed_lines(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "malformed.jsonl").write_text("not valid json at all\n")

    assert list_past_sessions(projects_dir=tmp_path, since_days=None) == []


def test_title_prefers_away_summary_over_first_prompt(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(
        path,
        [
            _user_record("what should I do about the flaky test", cwd="/Users/vesa/repo"),
            _away_summary("Goal: fix the flaky test in CI."),
        ],
    )

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert sessions[0].title == "Goal: fix the flaky test in CI."


def test_title_falls_back_to_first_user_prompt_trimmed(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    long_prompt = "x" * 200
    _write_jsonl(path, [_user_record(long_prompt, cwd="/Users/vesa/repo")])

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert len(sessions[0].title) <= 80
    assert sessions[0].title.endswith("…")


def test_session_without_prompt_or_summary_is_dropped(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    # a record with a cwd but no usable user-prompt text and no summary
    _write_jsonl(path, [{"type": "system", "subtype": "other", "cwd": "/Users/vesa/repo"}])

    assert list_past_sessions(projects_dir=tmp_path, since_days=None) == []


def test_slash_command_only_session_is_dropped(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    command = "<command-name>/exit</command-name> <command-message>exit</command-message>"
    _write_jsonl(path, [_user_record(command, cwd="/Users/vesa/repo")])

    assert list_past_sessions(projects_dir=tmp_path, since_days=None) == []


def test_cwd_comes_from_transcript_record_not_slug(tmp_path):
    # slug decodes (lossily) to /Users/vesa/my-repo, but the real cwd (with
    # a literal hyphen in the last segment) is only recoverable from the
    # record itself.
    proj = tmp_path / "-Users-vesa-my-repo"
    path = proj / "s.jsonl"
    _write_jsonl(path, [_user_record("hi", cwd="/Users/vesa/my-repo-actual")])

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert sessions[0].cwd == "/Users/vesa/my-repo-actual"


def test_cwd_falls_back_to_decoded_slug_when_no_record_has_one(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(path, [{"type": "user", "message": {"role": "user", "content": "hi"}}])

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert sessions[0].cwd == "/Users/vesa/repo"


def test_project_dir_and_size_bytes(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(path, [_user_record("hi", cwd="/Users/vesa/repo")])

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert sessions[0].project_dir == "-Users-vesa-repo"
    assert sessions[0].size_bytes == path.stat().st_size


def test_ignores_subagent_transcripts_nested_deeper(tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    main = proj / "main.jsonl"
    _write_jsonl(main, [_user_record("hi", cwd="/Users/vesa/repo")])
    sub = proj / "main" / "subagents" / "sub1.jsonl"
    _write_jsonl(sub, [_user_record("subagent prompt", cwd="/Users/vesa/repo")])

    sessions = list_past_sessions(projects_dir=tmp_path, since_days=None)
    assert [s.session_id for s in sessions] == ["main"]


def test_pastsession_is_dataclass_with_expected_fields():
    import dataclasses

    fields = {f.name for f in dataclasses.fields(PastSession)}
    assert fields == {"session_id", "cwd", "project_dir", "ended_at", "title", "size_bytes", "account"}


@dataclass
class _FakeCompleted:
    stdout: str
    returncode: int = 0


def _rg_stub(*paths):
    stdout = "".join(f"{p}\0" for p in paths)
    return lambda *args, **kwargs: _FakeCompleted(stdout=stdout)


def test_search_sessions_title_match_ranks_above_conversation_match(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    title_hit = proj / "title-hit.jsonl"
    conv_hit = proj / "conv-hit.jsonl"
    _write_jsonl(title_hit, [_user_record("need help with pandas indexing", cwd="/Users/vesa/repo")])
    _write_jsonl(
        conv_hit,
        [
            _user_record("hello there", cwd="/Users/vesa/repo"),
            _user_record("let's talk about pandas dataframes next", cwd="/Users/vesa/repo"),
        ],
    )
    _set_mtime(title_hit, days_ago=5)
    _set_mtime(conv_hit, days_ago=1)  # newer, but a conversation-only hit still ranks below title

    monkeypatch.setattr(history.subprocess, "run", _rg_stub(title_hit, conv_hit))

    matches = search_sessions("pandas", projects_dir=tmp_path)
    assert [m.session.session_id for m in matches] == ["title-hit", "conv-hit"]
    assert matches[0].where == "title"
    assert matches[1].where == "conversation"


def test_search_sessions_snippet_comes_from_message_text_not_raw_json(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(
        path,
        [
            _user_record("hello there", cwd="/Users/vesa/repo"),
            _user_record("the quokka population is thriving this year", cwd="/Users/vesa/repo"),
        ],
    )
    monkeypatch.setattr(history.subprocess, "run", _rg_stub(path))

    matches = search_sessions("quokka", projects_dir=tmp_path)
    assert len(matches) == 1
    assert matches[0].where == "conversation"
    assert "quokka" in matches[0].snippet
    assert '"type"' not in matches[0].snippet
    assert '"role"' not in matches[0].snippet


def test_search_sessions_honours_exclude_ids(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(path, [_user_record("talking about mangoes", cwd="/Users/vesa/repo")])
    monkeypatch.setattr(history.subprocess, "run", _rg_stub(path))

    assert search_sessions("mangoes", projects_dir=tmp_path, exclude_ids={"s"}) == []


def test_search_sessions_honours_include_ids(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    keep = proj / "keep.jsonl"
    drop = proj / "drop.jsonl"
    _write_jsonl(keep, [_user_record("talking about mangoes", cwd="/Users/vesa/repo")])
    _write_jsonl(drop, [_user_record("talking about mangoes too", cwd="/Users/vesa/repo")])
    monkeypatch.setattr(history.subprocess, "run", _rg_stub(keep, drop))

    matches = search_sessions("mangoes", projects_dir=tmp_path, include_ids={"keep"})
    assert [m.session.session_id for m in matches] == ["keep"]


def test_search_sessions_exclude_ids_wins_over_include_ids(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(path, [_user_record("talking about mangoes", cwd="/Users/vesa/repo")])
    monkeypatch.setattr(history.subprocess, "run", _rg_stub(path))

    matches = search_sessions("mangoes", projects_dir=tmp_path, include_ids={"s"}, exclude_ids={"s"})
    assert matches == []


def test_search_sessions_falls_back_when_rg_is_missing(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(
        path,
        [
            _user_record("hello there", cwd="/Users/vesa/repo"),
            _user_record("let's talk about durians next", cwd="/Users/vesa/repo"),
        ],
    )

    def boom(*args, **kwargs):
        raise FileNotFoundError("rg not found")

    monkeypatch.setattr(history.subprocess, "run", boom)

    matches = search_sessions("durians", projects_dir=tmp_path)
    assert [m.session.session_id for m in matches] == ["s"]
    assert matches[0].where == "conversation"
    assert "durians" in matches[0].snippet


def test_search_sessions_skips_a_file_that_vanishes_after_rg_reports_it(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    proj.mkdir(parents=True, exist_ok=True)
    ghost = proj / "ghost.jsonl"  # rg claims a match but the file is gone by the time we look

    monkeypatch.setattr(history.subprocess, "run", _rg_stub(ghost))

    assert search_sessions("anything", projects_dir=tmp_path) == []


def test_search_sessions_treats_query_as_a_fixed_string(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    path = proj / "s.jsonl"
    _write_jsonl(path, [_user_record("check config.py(main) for details", cwd="/Users/vesa/repo")])

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout=f"{path}\0")

    monkeypatch.setattr(history.subprocess, "run", fake_run)

    matches = search_sessions("config.py(main)", projects_dir=tmp_path)
    assert "--fixed-strings" in captured["cmd"]
    assert "config.py(main)" in captured["cmd"]  # passed as a literal argument, not shell/regex-interpreted
    assert [m.session.session_id for m in matches] == ["s"]


def test_search_sessions_ignores_nested_subagent_transcripts(monkeypatch, tmp_path):
    proj = tmp_path / "-Users-vesa-repo"
    main = proj / "main.jsonl"
    _write_jsonl(main, [_user_record("hi", cwd="/Users/vesa/repo")])
    sub = proj / "main" / "subagents" / "sub1.jsonl"
    _write_jsonl(sub, [_user_record("kumquat marmalade recipe", cwd="/Users/vesa/repo")])

    # simulate rg reporting the subagent file too, as it would without the glob exclusion
    monkeypatch.setattr(history.subprocess, "run", _rg_stub(sub))

    assert search_sessions("kumquat", projects_dir=tmp_path) == []
