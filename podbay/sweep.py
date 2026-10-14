"""The comment sweep: a timed /head-jeeves comments command, so a board
answer reaches Head Jeeves even when Claude Code's own comment wake does not.

That wake is the only thing that brings a comment on the board's artifact to
a session, and it fails quietly: auto mode can refuse to approve the
auto-reply turn, and the watch drops with the connection until the next
publish re-arms it (2026-10-14: two Yes answers sat "awaiting a reply" for
hours). podbay cannot read the threads itself (ArtifactComments exists only
inside a session, not in `claude -p`), so it asks Head Jeeves to read them.

The command goes every SWEEP_EVERY while a board is published, and only when
Head Jeeves is idle and has answered the last command podbay typed into him,
so sweeps never queue behind each other. An empty sweep costs him one read
and no reply. The handling is under "/head-jeeves comments" in
skills/head-jeeves/SKILL.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .model import NEEDS_YOU, Session

SWEEP_EVERY = timedelta(minutes=5)


def command(board_url: str) -> str:
    return f"/head-jeeves comments {board_url}"


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
