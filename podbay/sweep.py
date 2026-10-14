"""The comment sweep: a timed plain prompt asking for a comment check, so a board
answer reaches Head Jeeves even when Claude Code's own comment wake does not.

That wake is the only thing that brings a comment on the board's artifact to
a session, and it fails quietly: auto mode can refuse to approve the
auto-reply turn, and the watch drops with the connection until the next
publish re-arms it (2026-10-14: two Yes answers sat "awaiting a reply" for
hours). podbay cannot read the threads itself (ArtifactComments exists only
inside a session, not in `claude -p`), so it asks Head Jeeves to read them.

The command goes every SWEEP_EVERY while a board is published, and only when
Head Jeeves is idle and has answered the last command podbay typed into him,
so sweeps never queue behind each other. An empty sweep costs him one
watch listing and the word none. The handling is under "Comment sweep" in
skills/head-jeeves/SKILL.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .model import NEEDS_YOU, Session

SWEEP_EVERY = timedelta(minutes=5)


def command(board_url: str) -> str:
    """A plain prompt, not a slash command: the slash command expands the whole
    head-jeeves skill (about 10k tokens) into his context on every sweep. The
    first step is the bare watch listing (no url). Its row for the board names
    the threads sent to Claude that still await a reply, and only while there
    are some, so an idle sweep never reads the threads (about 5k tokens with
    the resolved ones). The watch call with a url prints no count and is only
    for re-arming a watch that is missing or stopped."""
    return (
        f"Comment sweep for {board_url} . Call ArtifactComments action watch with no url. "
        "If the row for that board has no 'awaiting a reply' phrase, answer only the word none "
        "and stop: do not read the threads, do not load a skill, do not refresh the board. "
        "If the row has that phrase, read the threads and handle each thread whose last comment "
        "is sent to Claude and has no reply as in the head-jeeves skill, section Messages from "
        "the board. If the row is missing or says stopped, call action watch with that url to "
        "arm it again, then read the threads."
    )


def due(
    head: Session, now: datetime, board_url: str | None,
    last_sweep_at: datetime | None, last_sent_at: datetime | None,
) -> bool:
    if not board_url:
        return False
    if last_sweep_at is not None and now - last_sweep_at < SWEEP_EVERY:
        return False
    if head.last_turn != "end_turn" or head.last_turn_ts is None:
        return False
    if head.awaiting_prompt or head.derive_status(now) != NEEDS_YOU:
        return False
    # his newest turn is younger than the last command podbay typed into him
    return last_sent_at is None or head.last_turn_ts > last_sent_at
