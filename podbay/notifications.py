"""Notification history: every toast podbay shows and every warning the
collision watch types into a session, one line each, so a toast that
vanished while Vesa looked elsewhere can be read back with `h`.

Line format: `<iso seconds>\t<kind>\t<text>`, text on one line. The file
rolls over to `.1` past MAX_BYTES. A write or read that fails never
stops podbay: it just loses that history line.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / ".local" / "state" / "podbay" / "notifications.log"
MAX_BYTES = 1_000_000
_WS_RE = re.compile(r"\s+")


def format_line(when: datetime, kind: str, text: str) -> str:
    return f"{when.isoformat(timespec='seconds')}\t{kind}\t{_WS_RE.sub(' ', text).strip()}\n"


def record(path: Path, when: datetime, kind: str, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name(path.name + ".1"))
        with path.open("a", encoding="utf-8") as f:
            f.write(format_line(when, kind, text))
    except OSError:
        pass


def parse_lines(lines: list[str]) -> list[tuple[datetime, str, str]]:
    entries = []
    for line in lines:
        parts = line.rstrip("\n").split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            when = datetime.fromisoformat(parts[0])
        except ValueError:
            continue
        entries.append((when, parts[1], parts[2]))
    return entries


def read_since(path: Path, since: datetime) -> list[tuple[datetime, str, str]]:
    """Entries at or after `since`, newest first, from the file and its
    rolled-over `.1` copy."""
    lines: list[str] = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            lines += p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
    return sorted((e for e in parse_lines(lines) if e[0] >= since), key=lambda e: e[0], reverse=True)
