"""Podbay's application log: one rotating file next to its state.

Textual owns the terminal while the TUI runs, and the traceback it prints
on a crash scrolls away as soon as podbay is started again in the same
tab. This log is where that traceback and every error podbay recovers from
land, so a "why did it crash" question has an answer after the fact.

Level comes from PODBAY_LOG_LEVEL (default INFO). A log that cannot be
opened must never stop podbay: configure() then leaves the logger without a
file handler and podbay runs as before.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_PATH = Path.home() / ".local" / "state" / "podbay" / "podbay.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_MARK = "_podbay_file_handler"


def configure(path: Path = LOG_PATH, level: str | None = None) -> logging.Logger:
    """Attach the rotating file handler to the `podbay` logger (the parent of
    every module logger in this package). Idempotent: a second call replaces
    the handler installed by the first, so tests can point it at tmp_path."""
    logger = logging.getLogger("podbay")
    level_name = (level or os.environ.get("PODBAY_LOG_LEVEL") or "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        return logger
    handler.setFormatter(logging.Formatter(_FORMAT))
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)
    return logger


def install_excepthook() -> None:
    """Log any exception that escapes to the interpreter (the plain
    subcommands: list, focus, send, inventory) before the
    default hook prints it. The TUI path does not reach here: Textual catches
    its own exceptions, see PodbayApp._handle_exception."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        logging.getLogger("podbay").critical(
            "unhandled exception", exc_info=(exc_type, exc, tb)
        )
        previous(exc_type, exc, tb)

    sys.excepthook = hook


# Mouse tracking (all modes and encodings), alternate screen, bracketed
# paste, focus reporting off; cursor on; attributes reset.
TERMINAL_RESET = (
    "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1005l\x1b[?1006l\x1b[?1015l"
    "\x1b[?1004l\x1b[?2004l\x1b[?1049l\x1b[?25h\x1b[0m"
)


def restore_terminal() -> None:
    """Hand the terminal back as a shell expects it. Written to /dev/tty so
    it works when stdout is redirected, and safe to call twice."""
    try:
        fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
    except OSError:
        return
    try:
        os.write(fd, TERMINAL_RESET.encode())
    except OSError:
        pass
    finally:
        os.close(fd)
