"""iTerm2 tab mapping, and the AppleScript that types into, opens, reads
and closes tabs.

Maps a session's pid to its controlling tty, and the tty to an iTerm2 tab
via AppleScript. The tab listing is cached and refreshed on a TTL so we
never shell out to osascript once per row.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

LIST_SCRIPT = """
tell application "iTerm2"
	set out to ""
	try
		set out to "CURRENT | " & (tty of current session of current window) & linefeed
	end try
	repeat with w in windows
		try
			set wid to id of w
			set b to bounds of w
			set x1 to item 1 of b
			set y1 to item 2 of b
			set x2 to item 3 of b
			set y2 to item 4 of b
			set tabCount to count of tabs of w
			set wnum to ""
			try
				tell current session of current tab of w to set wnum to (variable named "tab.window.number")
			end try
			set winOut to "W | " & wid & " | " & x1 & " | " & y1 & " | " & x2 & " | " & y2 & " | " & tabCount & " | " & wnum & linefeed
			repeat with tabIndex from 1 to tabCount
				repeat with s in sessions of (tab tabIndex of w)
					try
						tell s to set sessionPath to (variable named "path")
					on error
						set sessionPath to ""
					end try
					set winOut to winOut & "S | " & (tty of s) & " | " & (id of s) & " | " & wid & " | " & tabIndex & " | " & sessionPath & " | " & (name of s) & linefeed
				end repeat
			end repeat
			set out to out & winOut
		end try
	end repeat
	return out
end tell
"""
# One window iTerm2 cannot describe must not take the others with it: a
# window left with no tabs answers "tabs of w" with a missing value, and
# before each window had its own try that one error emptied the whole
# listing, so every session lost its terminal number and title at once.
# A window is added to the output only after all of its lines are built.
# bounds of w is a 4-item list {x1, y1, x2, y2}; concatenating it "as string"
# collapses to an unparseable run of digits (verified), so each coordinate is
# pulled out with "item N of b" and joined with the record's own " | " delimiter.

# Only selects/activates when a matching tty is found, so running this
# against a tty that matches nothing is a safe no-op (no focus stolen).
FOCUS_SCRIPT_TEMPLATE = """
tell application "iTerm2"
	set found to false
	repeat with w in windows
		repeat with t in tabs of w
			repeat with s in sessions of t
				if (tty of s) is "{tty}" then
					select w
					select t
					select s
					set found to true
				end if
			end repeat
		end repeat
	end repeat
	if found then
		activate
	end if
	return found
end tell
"""

@dataclass
class TabInfo:
    tty: str
    tab_id: str
    title: str
    busy: bool | None = None
    # True for the selected session of iTerm2's current window.
    selected: bool = False
    window_id: str | None = None
    tab_index: int | None = None
    # iTerm2's own view of the session's working directory; lsof can't be
    # relied on for another process's cwd here.
    path: str | None = None


@dataclass
class WindowInfo:
    window_id: str
    # AppleScript order: x1, y1, x2, y2. Can be negative (a monitor placed
    # above/left of the built-in display gives negative y/x).
    bounds: tuple[int, int, int, int]
    tab_count: int
    # iTerm2's own window number (the one in the title bar), read from the
    # "tab.window.number" variable; the rank of the window id stands in when
    # iTerm does not report it. iTerm reuses a closed window's number, so
    # the rank alone drifts from what the title bar shows.
    number: int | None = None


# Claude Code's leading tab-title glyph: a still "✳" means idle, a spinner
# frame means busy. Anything else (no glyph, unrecognized) is None -- the
# caller falls back to another signal.
IDLE_GLYPH = "✳"
BUSY_GLYPHS = "◐◓◑◒◴◷◶◵⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def busy_from_title(raw_name: str) -> bool | None:
    """Busy/idle from the tab title's leading status glyph; None when the
    glyph isn't recognized (or there's no leading glyph at all)."""
    name = raw_name.strip()
    if not name:
        return None
    glyph = name[0]
    if glyph == IDLE_GLYPH:
        return False
    if glyph in BUSY_GLYPHS:
        return True
    return None


_TITLE_SUFFIXES = (" (python)", " (claude)")


def strip_title(raw_name: str) -> str:
    """Strip Claude Code's leading status glyph and the trailing process
    name iTerm2 adds, ' (python)' or ' (claude)'."""
    name = raw_name.strip()
    if name and not name[0].isalnum():
        parts = name.split(" ", 1)
        if len(parts) == 2:
            name = parts[1]
    for suffix in _TITLE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def _parse_all(out: str) -> tuple[dict[str, TabInfo], dict[str, WindowInfo]]:
    """Parse LIST_SCRIPT output: an optional 'CURRENT | <tty>' line, one
    'W | id | x1 | y1 | x2 | y2 | tab_count [| number]' line per window, and one
    'S | tty | id | window_id | tab_index | path | name' line per session. Lines
    that don't match a known record type, or have the wrong field count for
    their type, are skipped rather than raising."""
    tabs: dict[str, TabInfo] = {}
    windows: dict[str, WindowInfo] = {}
    current_tty: str | None = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("CURRENT | "):
            current_tty = line[len("CURRENT | "):].strip()
            continue
        if line.startswith("W | "):
            parts = [p.strip() for p in line.split("|")]  # an empty number leaves a bare trailing "|"
            if len(parts) not in (7, 8):
                continue
            _, window_id, x1, y1, x2, y2, tab_count = parts[:7]
            try:
                bounds = (int(x1), int(y1), int(x2), int(y2))
                windows[window_id] = WindowInfo(window_id=window_id, bounds=bounds, tab_count=int(tab_count))
            except ValueError:
                continue
            if len(parts) == 8 and parts[7].isdigit():
                windows[window_id].number = int(parts[7])
            continue
        if line.startswith("S | "):
            parts = line.split(" | ", 6)
            if len(parts) != 7:
                continue
            _, tty, tab_id, window_id, tab_index, path, name = parts
            try:
                tab_index_val: int | None = int(tab_index)
            except ValueError:
                tab_index_val = None
            tabs[tty] = TabInfo(
                tty=tty,
                tab_id=tab_id,
                title=strip_title(name),
                busy=busy_from_title(name),
                path=path or None,
                window_id=window_id,
                tab_index=tab_index_val,
            )
            continue
        # Unrecognized record type: skip rather than raise.
    if current_tty in tabs:
        tabs[current_tty].selected = True
    _number_windows(windows)
    return tabs, windows


def _number_windows(windows: dict[str, WindowInfo]) -> None:
    def sort_key(window_id: str):
        try:
            return (0, int(window_id))
        except ValueError:
            return (1, 0)

    # Windows iTerm gave no number get the lowest free ones, in id order.
    used = {w.number for w in windows.values() if w.number is not None}
    free = (n for n in range(1, len(windows) + len(used) + 1) if n not in used)
    for window_id in sorted(windows, key=sort_key):
        if windows[window_id].number is None:
            windows[window_id].number = next(free)


def parse_listing(out: str) -> dict[str, TabInfo]:
    """tty -> TabInfo, as before window-awareness was added."""
    tabs, _ = _parse_all(out)
    return tabs


def parse_windows(out: str) -> dict[str, WindowInfo]:
    """window_id -> WindowInfo, parsed from the same LIST_SCRIPT output."""
    _, windows = _parse_all(out)
    return windows


def get_tty_for_pid(pid: int) -> str | None:
    try:
        out = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    if not out or out == "??":
        return None
    return out if out.startswith("/dev/") else f"/dev/{out}"


def _normalize_tty(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or raw == "??":
        return None
    return raw if raw.startswith("/dev/") else f"/dev/{raw}"


def get_ttys_for_pids(pids: list[int]) -> dict[int, str]:
    """Batch of get_tty_for_pid: one 'ps -p <all pids>' call instead of one
    subprocess per pid (the per-session cost in a refresh)."""
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,tty=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    result: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, tty_raw = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        tty = _normalize_tty(tty_raw)
        if tty is not None:
            result[pid] = tty
    return result


# Every script here walks iTerm2's windows, tabs and sessions; with a dozen
# sessions and the TUI polling the listing too, iTerm2 has taken six seconds
# to answer, and a call that gives up early reads as "no such tab". So every
# call gets a long leash: a CLI run has no cache to fall back on, and the
# TUI runs the listing from a worker thread.
SCRIPT_TIMEOUT = 20.0
LIST_TIMEOUT = SCRIPT_TIMEOUT


def _run_applescript(script: str, timeout: float = SCRIPT_TIMEOUT) -> str:
    """stdout of the script. A script that fails with nothing on stdout
    raises CalledProcessError, so a caller can tell iTerm2 refusing from
    iTerm2 answering with nothing (no windows, no matching tty)."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0 and not result.stdout:
        raise subprocess.CalledProcessError(result.returncode, "osascript", result.stdout, result.stderr)
    return result.stdout


class ItermLister:
    """Caches the iTerm2 tab listing; refresh_interval seconds between
    real AppleScript calls."""

    def __init__(self, refresh_interval: float = 10.0):
        self.refresh_interval = refresh_interval
        self._tabs: dict[str, TabInfo] = {}
        self._windows: dict[str, WindowInfo] = {}
        self._last_refresh = 0.0

    def _refresh(self) -> None:
        # A failed listing keeps the last good one: an empty listing would
        # strip every session of its terminal number and title.
        try:
            out = _run_applescript(LIST_SCRIPT, timeout=LIST_TIMEOUT)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("iTerm2 listing failed: %s", getattr(exc, "stderr", None) or exc)
            return
        self._tabs, self._windows = _parse_all(out)
        self._last_refresh = time.time()

    def _maybe_refresh(self, force: bool) -> None:
        if force or (time.time() - self._last_refresh) > self.refresh_interval:
            self._refresh()

    def tabs(self, force: bool = False) -> dict[str, TabInfo]:
        self._maybe_refresh(force)
        return self._tabs

    def windows(self, force: bool = False) -> dict[str, WindowInfo]:
        """Same cached AppleScript call as tabs() -- one osascript run
        populates both."""
        self._maybe_refresh(force)
        return self._windows

    def tab_for_pid(self, pid: int) -> TabInfo | None:
        tty = get_tty_for_pid(pid)
        if tty is None:
            return None
        return self.tabs().get(tty)

    def tabs_for_pids(self, pids: list[int]) -> dict[int, TabInfo | None]:
        """Batch tab_for_pid: one 'ps' call for every pid instead of one
        subprocess per pid, plus the (already-cached) tab listing."""
        ttys = get_ttys_for_pids(pids)
        tabs = self.tabs()
        return {pid: tabs.get(ttys[pid]) if pid in ttys else None for pid in pids}


def focus_tty(tty: str) -> bool:
    """Select and activate the iTerm2 tab whose session has this tty.
    Returns True if a matching tab was found (and focused)."""
    script = FOCUS_SCRIPT_TEMPLATE.format(tty=tty)
    try:
        out = _run_applescript(script)
    except (subprocess.SubprocessError, OSError):
        return False
    return out.strip().lower() == "true"


# A tty's input queue holds 1024 bytes (TTYHOG, the same number as
# MAX_CANON). `write text` hands iTerm2 the whole string at once and the
# kernel drops what does not fit before the reader gets to it: a 1132-byte
# message reached a session as its last 1024 bytes (2026-10-04). So the
# text goes in pieces well under that, with a pause after each, wrapped in
# the bracketed-paste markers (ESC [200~ ... ESC [201~) that Claude Code
# and zsh read as one paste: the pieces join into one prompt, newlines
# inside stay line breaks, and Enter after the last piece submits it.
PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"
PASTE_CHUNK_BYTES = 512
PASTE_CHUNK_DELAY = 0.05

# argv-based (never string-interpolated) so quotes/unicode in the message
# survive; also never select/activate the target tab. Item 1 is the tty,
# every item after it one piece of the text, written in order; then Enter.
SEND_SCRIPT_LINES = [
    "on run argv",
    '  tell application "iTerm2"',
    "    set targetTty to item 1 of argv",
    "    set found to false",
    "    repeat with w in windows",
    "      repeat with t in tabs of w",
    "        repeat with s in sessions of t",
    "          if (tty of s) is targetTty then",
    # The pieces go in one at a time with a pause between them, so the
    # reader drains the tty's input queue before the next arrives (see
    # paste_chunks). Enter goes separately from the text: one burst reads
    # as a paste in Claude Code and the newline becomes a line break
    # instead of a submit.
    "            repeat with i from 2 to (count of argv)",
    "              tell s to write text (item i of argv) newline NO",
    f"              delay {PASTE_CHUNK_DELAY}",
    "            end repeat",
    "            delay 0.3",
    '            tell s to write text ""',
    "            set found to true",
    "          end if",
    "        end repeat",
    "      end repeat",
    "    end repeat",
    "    return found",
    "  end tell",
    "end run",
]


# argv-based (never string-interpolated) so quotes/unicode survive. The
# window is made in the background: the app in front and iTerm2's own
# current window are noted first and put back right after, so the new
# window never keeps the keyboard. The launch command is the session's own
# command (see session_command), never keystrokes written into a shell
# that is still starting: keys the user types into whatever has focus
# cannot mix into it (2026-10-04: "actorcd /Users/... && claude" after a
# new window took focus mid-sentence). Returns the window id and the new
# session's tty, tab-separated. The separator is `character id 9`: inside
# `tell application "iTerm2"` a bare `tab` is iTerm2's tab class and comes
# out as the word "tab".
OPEN_WINDOW_SCRIPT_LINES = [
    "on run argv",
    "  set targetProfile to item 1 of argv",
    "  set targetCommand to item 2 of argv",
    '  set frontName to ""',
    "  try",
    '    tell application "System Events" to set frontName to name of first application process whose frontmost is true',
    "  end try",
    "  set prevWindow to missing value",
    '  if frontName is "iTerm2" then',
    "    try",
    '      tell application "iTerm2" to set prevWindow to id of current window',
    "    end try",
    "  end if",
    '  tell application "iTerm2"',
    '    if targetProfile is "" then set targetProfile to "Default"',
    '    if targetCommand is "" then',
    "      set w to (create window with profile targetProfile)",
    "    else",
    "      set w to (create window with profile targetProfile command targetCommand)",
    "    end if",
    "    set result_ to ((id of w) as text) & (character id 9) & (tty of current session of w)",
    "  end tell",
    "  try",
    '    if frontName is "iTerm2" then',
    "      if prevWindow is not missing value then",
    '        tell application "iTerm2" to select (first window whose id is prevWindow)',
    "      end if",
    '    else if frontName is not "" then',
    '      tell application "System Events" to set frontmost of process frontName to true',
    "    end if",
    "  end try",
    "  return result_",
    "end run",
]


def session_command(command: str) -> str:
    """`command` as a session command: run by a login shell, which then
    stays as the window's shell so the window outlives the command."""
    return f"/bin/zsh -lic {shlex.quote(command + '; exec /bin/zsh -l')}"


def open_window_with_tty(command: str | None = None, profile: str | None = None) -> tuple[str, str] | None:
    """Create a new iTerm2 window in the background (profile `profile` by
    name, else "Default") and return (window id, tty of its session) -- the
    id in the same space as WindowInfo.window_id -- or None if iTerm2
    couldn't be reached. `command`, if given, runs as the session's command
    and then leaves a shell, so the window survives it."""
    cmd = ["osascript"]
    for line in OPEN_WINDOW_SCRIPT_LINES:
        cmd += ["-e", line]
    cmd += [profile or "", session_command(command) if command else ""]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    window_id, _, tty = result.stdout.strip().partition("\t")
    if not window_id:
        return None
    return window_id, tty


def open_window(command: str | None = None, profile: str | None = None) -> str | None:
    """open_window_with_tty, for callers that want only the window id."""
    opened = open_window_with_tty(command, profile)
    return opened[0] if opened else None


# A tty that matches nothing returns "" (no marker needed -- an empty
# session also returns "").
READ_SESSION_SCRIPT_TEMPLATE = """
tell application "iTerm2"
	repeat with w in windows
		repeat with t in tabs of w
			repeat with s in sessions of t
				if (tty of s) is "{tty}" then
					return contents of s
				end if
			end repeat
		end repeat
	end repeat
	return ""
end tell
"""


def read_session_text(tty: str, max_lines: int = 200) -> str | None:
    """Last `max_lines` lines of a tty's on-screen text (trimmed here, not
    in AppleScript -- `contents` includes scrollback, ~150 KB). None on
    unknown tty or any osascript failure, never raises."""
    script = READ_SESSION_SCRIPT_TEMPLATE.format(tty=tty)
    try:
        out = _run_applescript(script, timeout=5.0)
    except (subprocess.SubprocessError, OSError):
        return None
    if not out:
        return None
    lines = out.splitlines()
    return "\n".join(lines[-max_lines:])


# zsh's PS2 names what is still open ("dquote> ", "cmdsubst quote> "),
# bash's is a bare "> ": a shell showing one is in the middle of a command,
# and text typed there joins it instead of starting a new one.
_CONTINUATION_RE = re.compile(r"^\s*(?:[a-z]+ )*[a-z]*>\s*$")


def screen_at_empty_prompt(text: str | None) -> bool:
    """Whether a shell's screen text ends at a prompt that is not a
    continuation line. Unreadable or blank text is not a prompt."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    return not _CONTINUATION_RE.match(lines[-1])


# Text that only a screen waiting for the user to answer shows: Claude
# Code's folder trust dialog, its menus' footer, a y/n question.
_BLOCKING_RE = re.compile(
    r"do you trust the files|yes, i trust this folder|enter to confirm"
    r"|press enter to continue|\(y/n\)|\[y/n\]|^\s*[❯>]\s*1\. ",
    re.IGNORECASE | re.MULTILINE,
)


def screen_waits_for_input(text: str | None) -> bool:
    """Whether the end of a screen shows a dialog or question that blocks
    until the user answers (the folder trust dialog above all)."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return bool(_BLOCKING_RE.search("\n".join(lines[-25:])))


def at_empty_prompt(tty: str) -> bool:
    """A shell is free only when its screen ends at a fresh prompt: the
    foreground process alone cannot tell a prompt from a half-typed line
    the shell is still waiting to finish."""
    return screen_at_empty_prompt(read_session_text(tty, max_lines=20))


def paste_chunks(text: str, limit: int = PASTE_CHUNK_BYTES) -> list[str]:
    """`text` as the argv items for SEND_SCRIPT_LINES: one plain item for a
    short single line, otherwise bracketed-paste pieces of at most `limit`
    bytes of UTF-8 each (markers included), split between characters."""
    if "\n" not in text and len(text.encode()) <= limit:
        return [text]
    budget = limit - len(PASTE_START) - len(PASTE_END)
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for ch in text:
        n = len(ch.encode())
        if current and size + n > budget:
            pieces.append("".join(current))
            current, size = [], 0
        current.append(ch)
        size += n
    pieces.append("".join(current))
    pieces[0] = PASTE_START + pieces[0]
    pieces[-1] = pieces[-1] + PASTE_END
    return pieces


def send_text(tty: str, text: str, timeout: float = SCRIPT_TIMEOUT) -> bool:
    """Write `text` into the iTerm2 session whose tty matches, then Enter
    (submitting a Claude Code prompt, or queuing behind one that is still
    running). Any length arrives whole as one prompt: see paste_chunks.
    Never selects or activates the tab. Returns True iff a matching tab was
    found."""
    chunks = paste_chunks(text)
    cmd = ["osascript"]
    for line in SEND_SCRIPT_LINES:
        cmd += ["-e", line]
    cmd += [tty, *chunks]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout + len(chunks) * PASTE_CHUNK_DELAY,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.stdout.strip().lower() == "true"


# argv-based, same reason as SEND_SCRIPT_LINES. Closes the session on the
# tty, which closes its tab when it is the only one there. Returns
# "closed <window id> last" when that was the window's only session,
# "closed <window id> more" otherwise, or "missing" when no session has
# the tty.
CLOSE_SCRIPT_LINES = [
    "on run argv",
    '  tell application "iTerm2"',
    "    set targetTty to item 1 of argv",
    "    repeat with w in windows",
    "      repeat with t in tabs of w",
    "        repeat with s in sessions of t",
    "          if (tty of s) is targetTty then",
    "            set wid to id of w",
    "            set lastOne to ((count of tabs of w) is 1 and (count of sessions of t) is 1)",
    "            tell s to close",
    '            if lastOne then return "closed " & wid & " last"',
    '            return "closed " & wid & " more"',
    "          end if",
    "        end repeat",
    "      end repeat",
    "    end repeat",
    '    return "missing"',
    "  end tell",
    "end run",
]

# Closing a window's last session leaves the window in iTerm2's AppleScript
# view with no tabs (verified), and such a window broke the listing once.
# iTerm2 ignores a close of that window for several seconds (about 7 in a
# test), even from the same script, so close_tty runs this again until the
# window is gone. Returns "gone" or "waiting".
CLOSE_EMPTY_WINDOW_SCRIPT_LINES = [
    "on run argv",
    '  tell application "iTerm2"',
    "    try",
    "      set w to (first window whose id is ((item 1 of argv) as integer))",
    "    on error",
    '      return "gone"',
    "    end try",
    "    if (count of tabs of w) is 0 then close w",
    '    return "waiting"',
    "  end tell",
    "end run",
]
EMPTY_WINDOW_WAIT_SECONDS = 20.0


def _osascript_lines(lines: list[str], args: list[str], timeout: float) -> str | None:
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    cmd += args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None


def close_tty(tty: str, timeout: float = SCRIPT_TIMEOUT, wait: float = EMPTY_WINDOW_WAIT_SECONDS) -> bool:
    """Close the iTerm2 session on this tty, and its tab or window when
    nothing else is in it. True iff a session had the tty; False (never
    raises) when none did or iTerm2 could not be reached."""
    out = _osascript_lines(CLOSE_SCRIPT_LINES, [tty], timeout) or ""
    parts = out.split()
    if len(parts) != 3 or parts[0] != "closed":
        return False
    _, window_id, rest = parts
    if rest == "last":
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if _osascript_lines(CLOSE_EMPTY_WINDOW_SCRIPT_LINES, [window_id], timeout) == "gone":
                break
            time.sleep(0.5)
        else:
            log.warning("iTerm2 window %s kept with no tabs after %s s", window_id, wait)
    return True


# The session podbay itself runs in, found by the UUID half of
# ITERM_SESSION_ID ("w0t1p0:UUID"); `rows`/`columns` give the visible grid
# and `contents` the whole buffer, whose last `rows` lines are the screen.
OWN_SCREEN_SCRIPT_TEMPLATE = """
tell application "iTerm2"
	repeat with w in windows
		repeat with t in tabs of w
			repeat with s in sessions of t
				if (id of s) is "{uuid}" then
					return (rows of s as string) & "x" & (columns of s as string) & linefeed & (contents of s)
				end if
			end repeat
		end repeat
	end repeat
	return ""
end tell
"""


def capture_own_screen() -> list[str] | None:
    """The text currently visible in the iTerm2 session podbay was launched
    from, one string per screen row, each exactly `columns` wide. None when
    not in iTerm2 or on any osascript failure, never raises. Plain text
    only: `contents` carries no colours."""
    session_id = os.environ.get("ITERM_SESSION_ID", "")
    if ":" not in session_id:
        return None
    uuid = session_id.split(":", 1)[1]
    try:
        out = _run_applescript(OWN_SCREEN_SCRIPT_TEMPLATE.format(uuid=uuid), timeout=5.0)
    except (subprocess.SubprocessError, OSError):
        return None
    if not out or "\n" not in out:
        return None
    header, body = out.split("\n", 1)
    try:
        rows_s, cols_s = header.split("x", 1)
        rows, cols = int(rows_s), int(cols_s)
    except ValueError:
        return None
    if rows <= 0 or cols <= 0:
        return None
    lines = body.split("\n")[-rows:]
    lines = [""] * (rows - len(lines)) + lines
    return [line[:cols].ljust(cols) for line in lines]
