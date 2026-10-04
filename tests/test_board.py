from datetime import datetime

from podbay import board

NOW = datetime(2026, 10, 3, 18, 5)


def _session(**over):
    base = {
        "name": "jeeves-64", "title": "Sora graphics research", "tab": "6", "repo": "sora", "state": "working",
        "age_seconds": 30, "last_text": "Slices 1 and 2 are pushed.", "waiting_on": None,
        "has_transcript": True, "head_jeeves": False, "context_pct": 18.0, "work_repo": "sora",
        "session_id": "s-0", "short_id": "s-0",
    }
    base.update(over)
    return base


def test_board_leads_with_outcomes_grouped_by_project():
    payload = {"sessions": [
        _session(session_id="s-sora", state="needs_you", age_seconds=600,
                 last_text="**Done, six commits on sora**, all live at https://vesahyp.github.io/sora: - **The race on"),
        _session(session_id="s-ux", name="a", title="ecarbrowser ux", tab="5", repo="jeeves", work_repo="ecarbrowser",
                 state="needs_you", age_seconds=600, context_pct=73,
                 waiting_on={"kind": "ask_user_question", "detail": "Which **layout**?"}),
        _session(session_id="s-ins", name="b", title="Tile cache sizing", tab="9", work_repo="ecarbrowser", state="working",
                 last_text="Comparing the three cache sizes."),
        _session(session_id="s-fix", name="podbay-fix-close", title="podbay-fix-close", tab="14", work_repo="podbay", state="working"),
        _session(session_id="s-new", name="d", title="Claude Code", tab="7", repo="", work_repo="", state="empty",
                 last_text=None, has_transcript=False, context_pct=None),
        _session(session_id="s-hj", name="head-jeeves", title="head-jeeves", tab="8", state="needs_you", head_jeeves=True, context_pct=7.4),
    ]}
    limits = {"claude": {"five_pct": 36, "five_resets_at": None, "week_pct": 17, "week_resets_at": None}}
    html = board.render(payload, NOW, limits)

    assert html.startswith("<title>Status Board</title>")
    assert "1 decision for you · 1 finished today · 1 in progress" in html
    order = [html.index(h) for h in ("Decisions for you", "Ready for you to test", "Finished today", "In progress", "Machine room")]
    assert order == sorted(order)
    decisions = html[html.index("Decisions for you"):html.index("Ready for you to test")]
    # only the question is a decision; the finished sora session is not
    assert 'data-target="#5 ecarbrowser"' in decisions and "Which layout?" in decisions and "sora" not in decisions
    # ready to test is Head Jeeves' call; without his line a finished session is only finished
    assert "Nothing new to test today." in html[html.index("Ready for you to test"):html.index("Finished today")]
    done = html[html.index("Finished today"):html.index("In progress")]
    assert "Done, six commits on sora, all live at https://vesahyp.github.io/sora</span>" in done
    assert 'href="https://vesahyp.github.io/sora"' in done and "*" not in done and "race" not in done
    progress = html[html.index("In progress"):html.index("Machine room")]
    assert progress.count('class="proj">ecarbrowser<') == 1
    assert "podbay-fix" not in progress and "Claude Code" not in progress
    room = html[html.index("Machine room"):]
    assert "podbay-fix-close" in room and "<b>claude</b> 5H 36%  7D 17%" in room and "never used" in room
    assert '<span class="ctx hot">ctx 73%</span>' in room and "Head Jeeves <span class=\"ctx\">ctx 7%</span>" in room
    assert "head-jeeves</span>" not in html and "#?" not in html
    assert "sendToClaude" in html and 'target + ": " + text' in html


def test_head_jeeves_headlines_win_and_stale_questions_drop():
    payload = {"sessions": [
        _session(session_id="s-sora", state="needs_you", last_text="Everything is committed."),
        _session(session_id="s-ux", name="a", tab="5", work_repo="ecarbrowser", state="working", last_text="Building the layout."),
        _session(session_id="s-96", name="jeeves-96", tab="4", title="Free browser games", work_repo="jeeves", repo="jeeves",
                 state="needs_you", last_text="Goal: publish."),
    ]}
    headlines = {
        "s-sora": {"kind": "shipped", "text": "Six improvements live", "link": "vesahyp.github.io/sora",
                   "steps": ["Open the page on the phone", "Start a race and watch the **lap** counter"]},
        "s-ux": {"kind": "decision", "text": "Gallery or list?"},  # answered already: the session works again
        "s-96": {"kind": "decision", "text": "Publish Räkkä on itch.io now?", "repo": "hoyry"},
        "gone": {"kind": "shipped", "text": "Stats board live", "link": "https://podbay.tienoo.com/stats/",
                 "repo": "podbay", "at": "2026-10-03T12:00"},
        "old": {"kind": "shipped", "text": "Yesterday's work", "repo": "keitos", "at": "2026-10-02"},
    }
    html = board.render(payload, NOW, headlines=headlines)
    decisions = html[html.index("Decisions for you"):html.index("Ready for you to test")]
    assert 'class="proj">hoyry<' in decisions and "Publish Räkkä on itch.io now?" in decisions
    assert "Gallery or list?" not in html and "Building the layout." in html
    ready = html[html.index("Ready for you to test"):html.index("In progress")]
    assert 'href="https://vesahyp.github.io/sora"' in ready and ">https://vesahyp.github.io/sora</a>" in ready
    assert "<li>Start a race and watch the lap counter</li>" in ready
    assert "Stats board live" in ready and "Yesterday" not in html


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


def test_board_handles_no_sessions():
    html = board.render({"sessions": []}, NOW)
    assert "Nothing waits on you." in html and "No live sessions." in html
    payload = {"sessions": [_session(title="<b>bold</b>", state="needs_you", last_text="<i>x</i> done")]}
    html = board.render(payload, NOW)
    assert "&lt;b&gt;bold&lt;/b&gt;" in html and "<b>bold</b>" not in html and "<i>x</i>" not in html


def test_write_is_atomic_and_cmd_board_prints_the_path(tmp_path, monkeypatch, capsys):
    from podbay import app as app_mod

    out = tmp_path / "boards" / "board.html"
    assert board.write("<title>x</title>", out) == out and out.read_text() == "<title>x</title>"
    assert not list((tmp_path / "boards").glob(".board-*"))

    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    app_mod.cmd_board(out)
    assert capsys.readouterr().out.strip() == str(out)
    assert "No live sessions." in out.read_text()


def test_the_published_url_is_recorded_once_and_printed_after_that(tmp_path, monkeypatch, capsys):
    """The link lives in a file, not only in Head Jeeves' context, so it
    survives a compact: `--url` records it, every later call prints it."""
    from podbay import app as app_mod

    out = tmp_path / "board.html"
    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    assert board.load_url() is None
    app_mod.cmd_board(out, "https://claude.ai/artifact/abc123 ")
    assert capsys.readouterr().out == f"{out}\npublished at https://claude.ai/artifact/abc123\n"
    app_mod.cmd_board(out)
    assert capsys.readouterr().out == f"{out}\npublished at https://claude.ai/artifact/abc123\n"
    assert board.URL_PATH.read_text() == "https://claude.ai/artifact/abc123\n"


def test_a_card_age_never_contradicts_its_state():
    """sora read "working · idle 2h": the age said idle while the state said
    working. Each state words its own age."""
    sub = {"kind": "subagents_running", "detail": "1"}
    assert board._age(_session(state="working", age_seconds=20, waiting_on=sub), NOW) == "active now · subagent running"
    assert board._age(_session(state="working", age_seconds=300), NOW) == "active 5m ago"
    assert board._age(_session(state="watching", age_seconds=600), NOW) == "active 10m ago"
    assert board._age(_session(state="stalled", age_seconds=1800), NOW) == "silent 30m"
    assert board._age(_session(state="needs_you", age_seconds=7200), NOW) == "idle 2h"
    assert board._age(_session(state="needs_you", age_seconds=10), NOW) == "just now"
    assert board._age(_session(state="shell_pane", age_seconds=10), NOW) == ""
    for state in ("working", "watching", "stalled"):
        assert "idle" not in board._age(_session(state=state, age_seconds=7200), NOW)
