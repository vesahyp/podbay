from podbay.iterm import busy_from_title, strip_title
from pathlib import Path

REPOS = str(Path.home() / "Repositories")


def test_strip_title_removes_idle_glyph_and_python_suffix():
    assert strip_title("✳ jeeves-83 (python)") == "jeeves-83"


def test_strip_title_removes_spinner_glyph():
    assert strip_title("◐ jeeves-83") == "jeeves-83"


def test_strip_title_no_glyph_passthrough():
    assert strip_title("jeeves-83") == "jeeves-83"
    assert strip_title("✳ Sora graphics research (claude)") == "Sora graphics research"


def test_busy_from_title_idle_glyph_is_not_busy():
    assert busy_from_title("✳ jeeves-83") is False


def test_busy_from_title_spinner_glyphs_are_busy():
    for glyph in "◐◓◑◒◴◷◶◵⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏":
        assert busy_from_title(f"{glyph} jeeves-83") is True


def test_busy_from_title_unknown_glyph_is_none():
    assert busy_from_title("? jeeves-83") is None


def test_busy_from_title_alnum_leading_char_is_none():
    assert busy_from_title("jeeves-83") is None


def test_busy_from_title_empty_string_is_none():
    assert busy_from_title("") is None
    assert busy_from_title("   ") is None


def test_parse_listing_marks_current_tab_selected():
    from podbay.iterm import parse_listing

    out = (
        "CURRENT | /dev/ttys002\n"
        "W | w1 | 0 | 0 | 800 | 600 | 2\n"
        "S | /dev/ttys001 | A | w1 | 1 | /Users/me/jeeves | ✳ one (python)\n"
        "S | /dev/ttys002 | B | w1 | 2 | /Users/me/jeeves | ✳ two (python)\n"
    )
    tabs = parse_listing(out)
    assert tabs["/dev/ttys001"].selected is False
    assert tabs["/dev/ttys002"].selected is True
    assert tabs["/dev/ttys002"].title == "two"


def test_parse_listing_without_current_line():
    from podbay.iterm import parse_listing

    tabs = parse_listing("W | w1 | 0 | 0 | 800 | 600 | 1\nS | /dev/ttys001 | A | w1 | 1 | /Users/me/jeeves | ✳ one (python)\n")
    assert list(tabs) == ["/dev/ttys001"]
    assert tabs["/dev/ttys001"].selected is False


def test_parse_listing_carries_window_id_and_tab_index():
    from podbay.iterm import parse_listing

    out = "W | w1 | 0 | 0 | 800 | 600 | 1\nS | /dev/ttys001 | A | w1 | 3 | /Users/me/jeeves | ✳ one (python)\n"
    tabs = parse_listing(out)
    assert tabs["/dev/ttys001"].window_id == "w1"
    assert tabs["/dev/ttys001"].tab_index == 3


def test_parse_windows_multi_window_multi_tab():
    from podbay.iterm import parse_windows

    out = (
        "W | w1 | 0 | 0 | 800 | 600 | 2\n"
        "S | /dev/ttys001 | A | w1 | 1 | /Users/me/jeeves | ✳ one (python)\n"
        "S | /dev/ttys002 | B | w1 | 2 | /Users/me/jeeves | ✳ two (python)\n"
        "W | w2 | 100 | 100 | 900 | 700 | 1\n"
        "S | /dev/ttys003 | C | w2 | 1 | /Users/me/jeeves | ✳ three (python)\n"
    )
    windows = parse_windows(out)
    assert set(windows) == {"w1", "w2"}
    assert windows["w1"].bounds == (0, 0, 800, 600)
    assert windows["w1"].tab_count == 2
    assert windows["w2"].bounds == (100, 100, 900, 700)
    assert windows["w2"].tab_count == 1


def test_parse_windows_negative_coordinates():
    from podbay.iterm import parse_windows

    out = "W | w1 | -810 | -1410 | 908 | 0 | 1\n"
    windows = parse_windows(out)
    assert windows["w1"].bounds == (-810, -1410, 908, 0)


def test_parse_all_skips_malformed_lines():
    from podbay.iterm import parse_listing, parse_windows

    out = (
        "W | w1 | 0 | 0 | 800 | 600 | 1\n"
        "S | /dev/ttys001 | A | w1 | 1 | /Users/me/jeeves | ✳ one (python)\n"
        "garbage line with no delimiter\n"
        "W | broken | not-a-number | 0 | 800 | 600 | 1\n"
        "S | /dev/ttys002 | onlythree\n"
        "\n"
    )
    tabs = parse_listing(out)
    windows = parse_windows(out)
    assert list(tabs) == ["/dev/ttys001"]
    assert list(windows) == ["w1"]


def test_tabinfo_backward_compatible_constructor():
    from podbay.iterm import TabInfo

    t = TabInfo(tty="/dev/ttys001", tab_id="A", title="one")
    assert t.window_id is None
    assert t.tab_index is None


def test_windowinfo_fields():
    from podbay.iterm import WindowInfo

    w = WindowInfo(window_id="w1", bounds=(-810, -1410, 908, 0), tab_count=3)
    assert w.window_id == "w1"
    assert w.bounds == (-810, -1410, 908, 0)
    assert w.tab_count == 3


class _FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_open_window_returns_new_window_id(monkeypatch):
    import podbay.iterm as iterm

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(stdout="98765\n")

    monkeypatch.setattr(iterm.subprocess, "run", fake_run)
    assert iterm.open_window() == "98765"
    # argv-based: command/profile travel as trailing argv items, never
    # interpolated into the script text.
    assert calls[0][-2:] == ["", ""]


def test_open_window_passes_command_and_profile_as_argv(monkeypatch):
    import podbay.iterm as iterm

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(stdout="1\n")

    monkeypatch.setattr(iterm.subprocess, "run", fake_run)
    iterm.open_window(command="echo 'hi \" there'", profile="Podbay")
    assert calls[0][-2:] == ["Podbay", "echo 'hi \" there'"]
    script = "\n".join(cmd for flag, cmd in zip(calls[0], calls[0][1:]) if flag == "-e")
    assert "create window with profile targetProfile" in script
    # never passed through create window's own `command` param -- that
    # replaces the login shell, so the window would close when it exits
    assert "with command" not in script


def test_open_window_with_tty_splits_on_a_real_tab(monkeypatch):
    import podbay.iterm as iterm

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(stdout="17067\t/dev/ttys013\n")

    monkeypatch.setattr(iterm.subprocess, "run", fake_run)
    assert iterm.open_window_with_tty() == ("17067", "/dev/ttys013")
    # a bare `tab` inside the iTerm2 tell block is its tab class, not a
    # tab character: the output read "17067tab/dev/ttys013" and every
    # podbay open into a new window failed
    script = "\n".join(cmd for flag, cmd in zip(calls[0], calls[0][1:]) if flag == "-e")
    assert "& tab &" not in script
    assert "character id 9" in script


def test_open_window_returns_none_on_nonzero_exit(monkeypatch):
    import podbay.iterm as iterm

    monkeypatch.setattr(iterm.subprocess, "run", lambda cmd, **kw: _FakeCompletedProcess(stdout="", returncode=1))
    assert iterm.open_window() is None


def test_open_window_returns_none_when_iterm_unreachable(monkeypatch):
    import podbay.iterm as iterm
    import subprocess as real_subprocess

    def boom(cmd, **kwargs):
        raise real_subprocess.SubprocessError("no iTerm2")

    monkeypatch.setattr(iterm.subprocess, "run", boom)
    assert iterm.open_window() is None


def test_read_session_text_trims_to_last_n_lines(monkeypatch):
    import podbay.iterm as iterm

    calls = []
    lines = [f"line {i}" for i in range(10)]

    def fake_run_applescript(script, timeout=3.0):
        calls.append((script, timeout))
        return "\n".join(lines) + "\n"

    monkeypatch.setattr(iterm, "_run_applescript", fake_run_applescript)
    result = iterm.read_session_text("/dev/ttys005", max_lines=3)
    assert result == "\n".join(lines[-3:])
    assert '"/dev/ttys005"' in calls[0][0]


def test_read_session_text_returns_none_on_no_match(monkeypatch):
    import podbay.iterm as iterm

    monkeypatch.setattr(iterm, "_run_applescript", lambda script, timeout=3.0: "")
    assert iterm.read_session_text("/dev/ttysXYZ") is None


def test_read_session_text_returns_none_when_iterm_unreachable(monkeypatch):
    import podbay.iterm as iterm
    import subprocess as real_subprocess

    def boom(script, timeout=3.0):
        raise real_subprocess.SubprocessError("no iTerm2")

    monkeypatch.setattr(iterm, "_run_applescript", boom)
    assert iterm.read_session_text("/dev/ttys005") is None


def test_itermlister_shares_one_applescript_call_for_tabs_and_windows(monkeypatch):
    import podbay.iterm as iterm

    calls = []

    def fake_run_applescript(script, timeout=3.0):
        calls.append(script)
        return "W | w1 | 0 | 0 | 800 | 600 | 1\nS | /dev/ttys001 | A | w1 | 1 | /Users/me/jeeves | ✳ one (python)\n"

    monkeypatch.setattr(iterm, "_run_applescript", fake_run_applescript)
    lister = iterm.ItermLister()
    tabs = lister.tabs()
    windows = lister.windows()
    assert len(calls) == 1
    assert "/dev/ttys001" in tabs
    assert "w1" in windows


def test_parse_all_keeps_the_session_path():
    from podbay.iterm import parse_listing

    tabs = parse_listing(
        "W | w1 | 0 | 0 | 800 | 600 | 1\n"
        f"S | /dev/ttys001 | A | w1 | 1 | {REPOS}/kafka-infra | ✳ one (python)\n"
        "S | /dev/ttys002 | B | w1 | 2 |  | ✳ two (python)\n"
    )
    assert tabs["/dev/ttys001"].path == f"{REPOS}/kafka-infra"
    assert tabs["/dev/ttys002"].path is None


def test_parse_windows_uses_iterms_own_number_and_ranks_only_as_fallback():
    from podbay.iterm import parse_windows

    # iTerm reused number 2 for a newer window after the old window 2 closed.
    out = (
        "W | 249 | 0 | 0 | 800 | 600 | 1 | 1\n"
        "W | 300 | 0 | 0 | 800 | 600 | 1 | 2\n"
        "W | 280 | 0 | 0 | 800 | 600 | 1 | 3\n"
        "W | 290 | 0 | 0 | 800 | 600 | 1 | \n"
    )
    windows = parse_windows(out)
    assert {w: info.number for w, info in windows.items()} == {"249": 1, "300": 2, "280": 3, "290": 4}


def test_run_applescript_raises_when_the_script_fails(monkeypatch):
    import subprocess as real_subprocess

    import podbay.iterm as iterm

    monkeypatch.setattr(
        iterm.subprocess, "run",
        lambda cmd, **kw: _FakeCompletedProcess(stdout="", returncode=1),
    )
    try:
        iterm._run_applescript(iterm.LIST_SCRIPT)
    except real_subprocess.CalledProcessError:
        return
    raise AssertionError("a failed script must not read as an empty listing")


def test_itermlister_keeps_the_last_listing_when_a_refresh_fails(monkeypatch):
    import subprocess as real_subprocess

    import podbay.iterm as iterm

    answers = [
        "W | w1 | 0 | 0 | 800 | 600 | 1 | 6\nS | /dev/ttys001 | A | w1 | 1 | /Users/me/sora | ✳ Sora graphics research\n",
        None,
    ]

    def fake_run_applescript(script, timeout=3.0):
        out = answers.pop(0)
        if out is None:
            raise real_subprocess.CalledProcessError(1, "osascript", "", "Can't get missing value. (-1728)")
        return out

    monkeypatch.setattr(iterm, "_run_applescript", fake_run_applescript)
    lister = iterm.ItermLister()
    assert lister.tabs()["/dev/ttys001"].title == "Sora graphics research"
    tabs = lister.tabs(force=True)
    assert tabs["/dev/ttys001"].title == "Sora graphics research"
    assert lister.windows()["w1"].number == 6


def test_list_script_skips_a_window_it_cannot_read():
    # The whole listing failed on one window left with no tabs. Each window
    # now builds its lines inside its own try and adds them only at the end.
    from podbay.iterm import LIST_SCRIPT

    body = LIST_SCRIPT[LIST_SCRIPT.index("repeat with w in windows"):]
    assert body.split("\n", 2)[1].strip() == "try"
    assert "set out to out & winOut" in body
    assert "count of tabList" not in body


def _send_argv(cmd):
    """The argv items after the script: the tty, then the text pieces."""
    i = max(k for k, flag in enumerate(cmd) if flag == "-e") + 2
    return cmd[i], cmd[i + 1:]


def test_a_5000_byte_message_arrives_whole_as_one_paste(monkeypatch):
    """Typed terminal input is cut at 1024 bytes (TTYHOG = MAX_CANON): a
    1132-byte report once reached a session as its last 1024 bytes. The
    text now goes in pieces under that, as one bracketed paste."""
    import podbay.iterm as iterm

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(stdout="true\n")

    monkeypatch.setattr(iterm.subprocess, "run", fake_run)
    line = "Part %d: \"quoted\", 'ticks', $HOME and ä ö å.\n"
    message = "".join(line % n for n in range(60))
    message += "x" * (5000 - len(message.encode()))
    assert len(message.encode()) == 5000

    assert iterm.send_text("/dev/ttys005", message) is True
    tty, pieces = _send_argv(calls[0])
    assert tty == "/dev/ttys005"
    assert len(pieces) > 1
    assert all(len(p.encode()) <= iterm.PASTE_CHUNK_BYTES for p in pieces)
    joined = "".join(pieces)
    assert joined == iterm.PASTE_START + message + iterm.PASTE_END
    assert joined.count(iterm.PASTE_START) == 1 and joined.count(iterm.PASTE_END) == 1
    # Enter is a separate write after the last piece, never part of one.
    script = "\n".join(c for flag, c in zip(calls[0], calls[0][1:]) if flag == "-e")
    assert "repeat with i from 2 to (count of argv)" in script
    assert 'write text ""' in script
    assert not any("\n" in p[-1] for p in pieces)


def test_pieces_split_between_characters_not_inside_one():
    import podbay.iterm as iterm

    pieces = iterm.paste_chunks("ä" * 3000, limit=64)
    assert "".join(pieces) == iterm.PASTE_START + "ä" * 3000 + iterm.PASTE_END
    assert all(len(p.encode()) <= 64 for p in pieces)


def test_a_short_single_line_is_typed_plain_and_a_multiline_one_is_pasted():
    import podbay.iterm as iterm

    assert iterm.paste_chunks("stop") == ["stop"]
    assert iterm.paste_chunks("cd /tmp && claude") == ["cd /tmp && claude"]
    assert iterm.paste_chunks("two\nlines") == [iterm.PASTE_START + "two\nlines" + iterm.PASTE_END]
