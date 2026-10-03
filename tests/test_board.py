from datetime import datetime

from podbay import board

NOW = datetime(2026, 10, 3, 18, 5)


def _session(**over):
    base = {
        "name": "jeeves-64", "title": "Sora graphics research", "tab": "6", "repo": "sora", "state": "working",
        "age_seconds": 30, "parked_until": None, "last_text": "Slices 1 and 2 are pushed.", "waiting_on": None,
        "has_transcript": True, "head_jeeves": False, "context_pct": 18.0, "work_repo": "sora",
    }
    base.update(over)
    return base


def test_board_groups_cards_by_who_acts_next_and_addresses_them():
    payload = {"sessions": [
        _session(),
        _session(name="a", title="ecarbrowser ux", tab="5", repo="jeeves", work_repo="ecarbrowser", state="needs_you", age_seconds=600,
                 context_pct=73, waiting_on={"kind": "ask_user_question", "detail": "Which layout?"}),
        _session(name="b", title="Urbangreen follow-up", tab="3", repo="urbangreen", work_repo="urbangreen", state="parked", parked_until="2026-10-12T09:00:00", age_seconds=86400 * 9),
        _session(name="c", title="Claude Code", tab="7", repo="", work_repo="", state="empty", last_text=None, has_transcript=False, context_pct=None),
        _session(name="head-jeeves", title="head-jeeves", tab="8", repo="jeeves", work_repo="jeeves", state="needs_you", head_jeeves=True, context_pct=7.4),
    ]}
    limits = {"claude": {"five_pct": 36, "five_resets_at": None, "week_pct": 17, "week_resets_at": None},
              "personal": {"five_pct": 11, "five_resets_at": None, "week_pct": None, "week_resets_at": None}}
    html = board.render(payload, NOW, limits)

    assert html.startswith("<title>Session Board</title>")
    assert "3 Oct, 18:05 · 4 sessions · 1 needs you" in html
    assert html.index("<h2>Needs you</h2>") < html.index("<h2>Working</h2>") < html.index("<h2>Parked</h2>")
    assert 'data-target="#5 ecarbrowser"' in html and 'data-target="#6 sora"' in html and 'data-target="#7"' in html
    assert "head-jeeves" not in html
    assert '<p class="ask">asks: Which layout?</p>' in html
    assert "idle 10m" in html and "active now" in html and "until 10-12 09:00" in html
    assert "never used" in html
    assert "sendToClaude" in html and 'target + ": " + text' in html
    # context per card, Head Jeeves' own in the header, the accounts' windows under it
    assert '<span class="ctx">ctx 18%</span>' in html and '<span class="ctx hot">ctx 73%</span>' in html
    assert "Head Jeeves <span class=\"ctx\">ctx 7%</span>" in html
    assert "<b>claude</b> 5H 36%  7D 17%" in html and "<b>personal</b> 5H 11%" in html


def test_board_escapes_and_trims_text_and_handles_no_sessions():
    payload = {"sessions": [_session(title="<b>bold</b>", last_text="x " * 400)]}
    html = board.render(payload, NOW)
    assert "&lt;b&gt;bold&lt;/b&gt;" in html and "<b>bold</b>" not in html
    assert "…</p>" in html
    assert "No live sessions." in board.render({"sessions": []}, NOW)


def test_write_is_atomic_and_cmd_board_prints_the_path(tmp_path, monkeypatch, capsys):
    from podbay import app as app_mod

    out = tmp_path / "boards" / "board.html"
    assert board.write("<title>x</title>", out) == out and out.read_text() == "<title>x</title>"
    assert not list((tmp_path / "boards").glob(".board-*"))

    monkeypatch.setattr(app_mod.sources, "gather_sessions", lambda *_a, **_k: [])
    app_mod.cmd_board(out)
    assert capsys.readouterr().out.strip() == str(out)
    assert "No live sessions." in out.read_text()


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
