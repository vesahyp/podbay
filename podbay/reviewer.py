"""What Head Jeeves reads and writes. Head Jeeves is a standing Claude Code
session of its own (see skills/head-jeeves/SKILL.md), started and fed by
podbay; this module holds the two things podbay and that session share:

  excerpt      a session's last turns as plain text, `podbay excerpt <name>`,
               the one way Head Jeeves reads a transcript
  reviews      the files he writes under ~/.local/state/podbay/reviews/,
               <session-id>-<kind>.md, which podbay watches and shows with v.
               The kinds: a checkup (podbay asks for it when a session's
               prompts turn heated) and a handover (his own step before he
               restarts a session, see the skill)
"""

from __future__ import annotations

import logging
from pathlib import Path

from .sources import read_conversation

log = logging.getLogger(__name__)

REVIEWS_DIR = Path.home() / ".local" / "state" / "podbay" / "reviews"
TURNS = 80  # conversation entries in an excerpt, newest last
TOOL_CHARS = 300  # a TOOL line is cut to this many characters; USER and AGENT turns are whole
EXCERPT_CHARS = 60_000  # and the whole excerpt to this many
KINDS = ("checkup", "handover")


def excerpt(transcript_path: Path, turns: int = TURNS, last_agent: bool = False) -> str:
    """The session's last `turns` conversation entries as plain text. USER
    and AGENT entries are whole (a final report is a few thousand
    characters); TOOL lines are cut. With `last_agent`, only the newest
    AGENT entry, whole."""
    entries = read_conversation(transcript_path, limit=turns)
    if last_agent:
        entries = [e for e in entries if e.get("role") == "assistant"][-1:]
    lines: list[str] = []
    labels = {"user": "USER", "assistant": "AGENT", "tool": "TOOL"}
    for e in entries:
        text = " ".join((e.get("text") or "").split())
        if e.get("role") == "tool" and len(text) > TOOL_CHARS:
            text = text[:TOOL_CHARS] + " …"
        stamp = (e.get("timestamp") or "")[11:16]
        lines.append(f"[{stamp}] {labels.get(e.get('role'), 'NOTE')}: {text}")
    body = "\n".join(lines)
    if len(body) > EXCERPT_CHARS:
        body = "…\n" + body[-EXCERPT_CHARS:]
    return body


def review_path(session_id: str, kind: str, directory: Path | None = None) -> Path:
    return (directory or REVIEWS_DIR) / f"{session_id}-{kind}.md"


def newest_files(directory: Path | None = None) -> dict[str, float]:
    """Every review file and its mtime, for spotting the ones Head Jeeves
    just wrote. Empty when the directory does not exist yet."""
    directory = directory or REVIEWS_DIR
    out: dict[str, float] = {}
    try:
        for path in directory.glob("*.md"):
            out[path.name] = path.stat().st_mtime
    except OSError:
        pass
    return out


def latest(session_id: str, directory: Path | None = None) -> Path | None:
    """The newest saved review of a session, of either kind."""
    candidates = [p for p in (review_path(session_id, k, directory) for k in KINDS) if p.exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
