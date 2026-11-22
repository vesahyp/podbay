import json
from datetime import datetime

from podbay import board

NOW = datetime(2026, 10, 3, 18, 5)


def _session(**over):
    base = {
        "name": "jeeves-64", "title": "Sora graphics research", "tab": "6", "repo": "sora", "state": "working",
        "age_seconds": 30, "parked_until": None, "last_text": "Slices 1 and 2 are pushed.", "waiting_on": None,
        "has_transcript": True, "head_jeeves": False, "context_pct": 18.0, "work_repo": "sora",
        "session_id": "s-0", "short_id": "s-0",
    }
    base.update(over)
    return base


def _by_id(found):
    return {kind: {i["id"]: i for i in items} for kind, items in found.items()}


def test_lines_are_sorted_into_sections_by_what_they_need():
    payload = {"sessions": [
        _session(session_id="s-sora", state="needs_you", age_seconds=600,
                 last_text="**Done, six commits on sora**, all live at https://vesahyp.github.io/sora: - **The race on"),
        _session(session_id="s-ux", name="a", title="ecarbrowser ux", tab="5", repo="jeeves", work_repo="ecarbrowser",
                 state="needs_you", age_seconds=600, waiting_on={"kind": "ask_user_question", "detail": "Which **layout**?"}),
        _session(session_id="s-ins", name="b", title="Tile cache sizing", tab="9", work_repo="ecarbrowser", state="working",
                 last_text="Comparing the three cache sizes."),
        _session(session_id="s-ug", name="c", title="Greenhouse follow-up", tab="3", repo="greenhouse", work_repo="greenhouse",
                 state="parked", parked_until="2026-10-12T09:00:00", age_seconds=86400 * 9),
        _session(session_id="s-fix", name="podbay-fix-close", title="podbay-fix-close", tab="14", work_repo="podbay", state="working"),
        _session(session_id="s-new", name="d", title="Claude Code", tab="7", repo="", work_repo="", state="empty",
                 last_text=None, has_transcript=False),
        _session(session_id="s-hj", name="head-jeeves", title="head-jeeves", tab="8", state="needs_you", head_jeeves=True),
    ]}
    found = _by_id(board.collect(payload, NOW))
    # only the question is a decision; the finished sora session is not
    assert list(found["decision"]) == ["s-ux"]
    ux = found["decision"]["s-ux"]
    assert ux["target"] == "#5 ecarbrowser" and ux["text"] == "Which layout?" and ux["project"] == "ecarbrowser"
    # ready to test is Head Jeeves' call; without his line a finished session is only finished
    assert found["shipped"] == {}
    done = found["finished"]["s-sora"]
    assert done["text"] == "Done, six commits on sora, all live at https://vesahyp.github.io/sora"
    assert done["link"] == "https://vesahyp.github.io/sora"
    assert set(found["progress"]) == {"s-ins", "s-ug"}
    assert found["progress"]["s-ug"]["when"] == "parked until Mon 12 Oct"
    # podbay's own fix sessions, empty tabs and Head Jeeves are not the user's work
    assert not any(k in kind for kind in found.values() for k in ("s-fix", "s-new", "s-hj"))


def test_head_jeeves_headlines_win_and_stale_questions_drop():
    payload = {"sessions": [
        _session(session_id="s-sora", state="needs_you", last_text="Everything is committed."),
        _session(session_id="s-ux", name="a", tab="5", work_repo="ecarbrowser", state="working", last_text="Building the layout."),
        _session(session_id="s-96", name="jeeves-96", tab="4", title="Free browser games", work_repo="jeeves", repo="jeeves",
                 state="needs_you", last_text="Goal: publish."),
    ]}
    headlines = {
        "s-sora": {"kind": "shipped", "text": "Six improvements live", "link": "https://vesahyp.github.io/sora",
                   "steps": ["Open the page on the phone", "Start a race and watch the lap counter"]},
        "s-ux": {"kind": "decision", "text": "Gallery or list?"},  # answered already: the session works again
        "s-96": {"kind": "decision", "text": "Publish Räkkä on itch.io now?", "repo": "hoyry", "options": ["Yes", "Wait"]},
        "gone": {"kind": "shipped", "text": "Stats board live", "link": "https://podbay.tienoo.com/stats/",
                 "repo": "podbay", "at": "2026-10-03T12:00"},
        "old": {"kind": "shipped", "text": "Yesterday's work", "repo": "keitos", "at": "2026-09-20"},
    }
    found = _by_id(board.collect(payload, NOW, headlines))
    assert found["decision"]["s-96"]["project"] == "hoyry" and found["decision"]["s-96"]["options"] == ["Yes", "Wait"]
    assert found["progress"]["s-ux"]["text"] == "Building the layout."
    sora = found["shipped"]["s-sora"]
    assert sora["link"] == "https://vesahyp.github.io/sora" and sora["steps"][1] == "Start a race and watch the lap counter"
    gone = found["shipped"]["shipped:gone"]
    assert gone["target"] == "podbay (session ended)" and gone["text"] == "Stats board live"
    assert "shipped:old" not in found["shipped"]


def test_clean_line_takes_the_first_sentence_and_never_cuts_a_word():
    assert board.clean_line("**Done.** The rest [here](http://x).") == "Done."
    assert board.clean_line("Shipped at vesahyp.github.io/sora. Next one.") == "Shipped at vesahyp.github.io/sora."
    assert board.clean_line("## Summary\n- `one` thing\n- two") == "Summary"
    long = board.clean_line("word " * 100)
    assert long.endswith("word…") and len(long) <= board.LINE_CHARS + 1


def test_load_headlines_tolerates_a_missing_or_broken_file(tmp_path):
    assert board.load_headlines(tmp_path / "none.json") == ({}, None)
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    lines, problem = board.load_headlines(bad)
    assert lines == {} and "bad.json" in problem
    good = tmp_path / "good.json"
    good.write_text('{"abc": {"kind": "progress", "text": "Half done"}, "x": {"kind": "shipped"}}')
    assert board.load_headlines(good) == ({"abc": {"kind": "progress", "text": "Half done"}}, None)


def test_a_session_with_no_terminal_number_is_addressed_by_name():
    assert board._target(_session(tab=None, name="jeeves-64", work_repo="sora")) == "jeeves-64 sora"
    assert board._target(_session(tab="6", work_repo="sora")) == "#6 sora"


def test_no_sessions_no_lines():
    assert board.collect({"sessions": []}, NOW) == {"decision": [], "shipped": [], "progress": [], "finished": []}


def test_mac_name_drops_the_owner(monkeypatch):
    from podbay import machine
    for full, short in (("Someone’s MacBook Air", "MacBook Air"), ("studio", "studio"), ("", "")):
        monkeypatch.setattr(machine, "_run", lambda cmd, out=full: out + "\n")
        assert machine.mac_name() == short


def test_shipped_line_stays_until_tested_or_seven_days():
    headlines = {
        "a": {"kind": "shipped", "text": "Yesterday", "repo": "sora", "at": "2026-10-02T12:00"},
        "b": {"kind": "shipped", "text": "Tested already", "repo": "sora", "at": "2026-10-03T12:00", "tested": "2026-10-03T13:00"},
        "c": {"kind": "shipped", "text": "Too old", "repo": "sora", "at": "2026-09-20T12:00"},
    }
    found = _by_id(board.collect({"sessions": []}, NOW, headlines))
    assert list(found["shipped"]) == ["shipped:a"]


def test_decision_without_a_live_session_stays_with_options_for_head_jeeves():
    headlines = {
        "decision:databricks": {"kind": "decision", "text": "Sign in to Databricks?", "options": ["Now", "Later"]},
        "ended-session-id": {"kind": "decision", "text": "Approve the push?", "repo": "sora"},
    }
    found = _by_id(board.collect({"sessions": []}, NOW, headlines))
    item = found["decision"]["decision:databricks"]
    assert item["options"] == ["Now", "Later"] and item["target"] == "Head Jeeves"
    assert found["decision"]["decision:ended-session-id"]["project"] == "sora"
    assert board.collect({"sessions": []}, NOW, {})["decision"] == []


def test_mark_tested_writes_the_field_and_takes_the_line_off(tmp_path):
    path = tmp_path / "headlines.json"
    path.write_text('{"abcdef123": {"kind": "shipped", "text": "Live", "repo": "sora", "at": "2026-10-03T12:00"},'
                    ' "q": {"kind": "decision", "text": "Q?"}}')
    assert board.mark_tested("shipped:abcdef123", path, datetime(2026, 10, 3, 14, 0))
    assert json.loads(path.read_text())["abcdef123"]["tested"] == "2026-10-03T14:00:00"
    assert not board.mark_tested("shipped:nothing", path)
    assert not board.mark_tested("q", path)
    headlines, _ = board.load_headlines(path)
    assert board.collect({"sessions": []}, NOW, headlines)["shipped"] == []


def test_full_text_keeps_every_sentence_and_drops_markdown():
    text = "**Done.** The rest [here](http://x). " + "More. " * 5
    full = board.full_text(text)
    assert full.startswith("Done. The rest here. More.") and "**" not in full
    assert board.clean_line(text) == "Done."
    assert board.full_text("a " * 2000).endswith("…")


def test_a_line_over_a_session_that_stopped_is_stale_and_listed_for_head_jeeves():
    # 2026-11-21: a progress line said "today" over a session idle for hours.
    payload = {"sessions": [
        _session(session_id="s-idle", name="idle-1", title="Access control", state="needs_you", age_seconds=2 * 3600),
        _session(session_id="s-fresh", name="fresh-1", state="needs_you", age_seconds=10 * 60),
        _session(session_id="s-work", name="work-1", state="working", age_seconds=5 * 3600),
        _session(session_id="s-lim", name="lim-1", state="needs_you", age_seconds=60,
                 limit_stop={"kind": "message", "at": "2026-10-03T17:00:00"}),
        _session(session_id="s-ask", name="ask-1", state="needs_you", age_seconds=5 * 3600),
    ]}
    headlines = {
        "s-idle": {"kind": "progress", "text": "Column hiding, done today"},
        "s-fresh": {"kind": "progress", "text": "Just finished a step"},
        "s-work": {"kind": "progress", "text": "Long build running"},
        "s-lim": {"kind": "progress", "text": "Halfway through the migration"},
        "s-ask": {"kind": "decision", "text": "Sign in to Databricks in your browser?"},  # waits idle by design
        "s-gone": {"kind": "progress", "text": "Rewriting the rules", "repo": "jeeves"},
        "s-gone-ask": {"kind": "decision", "text": "Approve the push?"},
        "decision:own": {"kind": "decision", "text": "Head Jeeves' own question"},
    }
    found = board.collect(payload, NOW, headlines)
    by = _by_id(found)
    assert by["progress"]["s-idle"]["stale"] == "idle"
    assert by["progress"]["s-idle"]["when"] == "stale: the session has been idle for 2h"
    assert by["progress"]["s-lim"]["when"] == "stale: the session is stopped on a usage limit"
    assert not by["progress"]["s-fresh"].get("stale") and not by["progress"]["s-work"].get("stale")
    assert not by["decision"]["s-ask"].get("stale") and not by["decision"]["decision:own"].get("stale")
    assert by["decision"]["decision:s-gone-ask"]["stale"] == "ended"

    stale = {line["key"]: line for line in board.stale_lines(found, payload, headlines)}
    assert {k: v["why"] for k, v in stale.items()} == {
        "s-idle": "idle", "s-lim": "limit", "s-gone-ask": "ended", "s-gone": "ended"}
    assert stale["s-idle"]["name"] == "idle-1" and stale["s-idle"]["text"] == "Column hiding, done today"
    assert stale["s-gone"]["title"] == "jeeves" and stale["s-gone"]["kind"] == "progress"


def test_each_stale_line_is_told_once_per_reason(tmp_path):
    idle = {"key": "a", "why": "idle"}
    new, told = board.stale_to_tell([idle], {})
    assert new == [idle]
    told = {"a": "idle"}
    assert board.stale_to_tell([idle], told) == ([], {"a": "idle"})
    ended = {"key": "a", "why": "ended"}
    assert board.stale_to_tell([ended], told)[0] == [ended]  # a new reason is news
    assert board.stale_to_tell([], told) == ([], {})  # fixed: forgotten, so a relapse is told again
    path = tmp_path / "told.json"
    board.save_told({"a": "idle"}, path)
    assert board.load_told(path) == {"a": "idle"}
    assert board.load_told(tmp_path / "missing.json") == {}
