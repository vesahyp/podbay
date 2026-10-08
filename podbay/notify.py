"""`podbay notify <text>`: one push to the user's phone, through whatever
command the machine has for that.

podbay has no push transport of its own. `podbay config notify-command
<command>` names one (a script, with any fixed arguments, split like a
shell line), and `podbay notify` runs it with the text appended as one more
argument, then the link as the last one when there is a link. Head Jeeves
calls it when a session needs a decision from the user or has shipped
something for them to test (rules in his skill).

The link is where tapping the notification lands. By default it is the
board's URL (board.load_url), because every line Head Jeeves pushes is a
line on the board, and the board is where the user answers it: tapping the
line opens its composer. `podbay notify --url <url>` sends another link,
and `--no-url` none.

Empty command: off, nothing runs. A command that fails, hangs or is
missing is logged and reported in one line; it never raises, because the
caller is an agent in the middle of a reply.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess

from . import config

log = logging.getLogger(__name__)

TIMEOUT = 30  # seconds the command may take; a push service answers in a few


def argv(command: str, text: str, url: str | None = None) -> list[str]:
    """The command line to run: the configured command split like a shell
    line, `~` in its program expanded, the text as one argument, whatever
    it contains, and the link after it when there is one."""
    parts = shlex.split(command)
    if parts:
        parts[0] = os.path.expanduser(parts[0])
    return parts + [text] + ([url] if url else [])


def send(text: str, command: str | None = None, timeout: float = TIMEOUT, url: str | None = None) -> tuple[bool, str]:
    """Run the notify command with `text` and the link `url` (None: the
    recorded board URL, if any; "": no link). Returns (sent, reason): sent
    is True when the command exited 0; otherwise reason says in one line
    why not, and the same line is in the log. Never raises."""
    command = config.notify_command() if command is None else command
    if not command.strip():
        return False, "off"
    if url is None:
        from . import board

        url = board.load_url()
    try:
        parts = argv(command, text, url)
    except ValueError as exc:
        reason = f"notify-command does not parse as a command line: {exc}"
        log.error(reason)
        return False, reason
    try:
        result = subprocess.run(parts, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        reason = f"{parts[0]} did not finish in {timeout:g} s"
        log.error("notify: %s", reason)
        return False, reason
    except OSError as exc:
        reason = f"{parts[0]} could not be run: {exc.strerror or exc}"
        log.error("notify: %s", reason)
        return False, reason
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        detail = f": {tail[-1]}" if tail else ""
        reason = f"{parts[0]} exited {result.returncode}{detail}"
        log.error("notify: %s", reason)
        return False, reason
    log.info("notify: sent %d chars through %s%s", len(text), parts[0], f" with link {url}" if url else "")
    return True, ""
