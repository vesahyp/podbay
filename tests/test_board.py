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
        "old": {"kind": "shipped", "text": "Yesterday's work", "repo": "keitos", "at": "2026-10-02"},
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
