"""Pure geometry for arranging selected panes across desktops and screens.

AppleScript window bounds are (x1, y1, x2, y2). Nothing here shells out to
osascript or switches desktops -- a later module applies a plan produced
here. Keeping this pure (no subprocess) is what makes it unit-testable
without a real display attached.
"""

from __future__ import annotations

from dataclasses import dataclass

Bounds = tuple[int, int, int, int]


@dataclass(frozen=True)
class Screen:
    """A screen's AppleScript bounds frame. y can be negative -- a monitor
    stacked above the built-in display sits at negative y in that space."""

    x1: int
    y1: int
    x2: int
    y2: int

    def as_bounds(self) -> Bounds:
        return (self.x1, self.y1, self.x2, self.y2)


@dataclass(frozen=True)
class Placement:
    pane_id: object
    desktop_index: int  # 0-based, in selection order
    slot: str  # "left" | "right"
    bounds: Bounds


def _frame_bounds(frame: Screen | Bounds) -> Bounds:
    return frame.as_bounds() if isinstance(frame, Screen) else tuple(frame)  # type: ignore[return-value]


def half_frames(frame: Screen | Bounds) -> tuple[Bounds, Bounds]:
    """Left/right maximized halves of `frame`, no gap; the right half
    absorbs an odd pixel of width so left+right always covers the frame."""
    x1, y1, x2, y2 = _frame_bounds(frame)
    left_width = (x2 - x1) // 2
    mid = x1 + left_width
    return (x1, y1, mid, y2), (mid, y1, x2, y2)


def desktops_needed(count: int, per_desktop: int = 2) -> int:
    if count <= 0:
        return 0
    return -(-count // per_desktop)  # ceil division


def plan_layout(pane_ids: list, screen_frame: Screen | Bounds, per_desktop: int = 2) -> list[Placement]:
    """One placement per pane, in selection order: desktops fill up
    `per_desktop` panes at a time. A lone pane on the last desktop gets the
    LEFT half only -- it is never stretched to fill the screen."""
    if per_desktop != 2:
        raise ValueError("only per_desktop=2 (left/right halves) is supported")
    left, right = half_frames(screen_frame)
    placements = []
    for index, pane_id in enumerate(pane_ids):
        slot = "left" if index % per_desktop == 0 else "right"
        placements.append(
            Placement(
                pane_id=pane_id,
                desktop_index=index // per_desktop,
                slot=slot,
                bounds=left if slot == "left" else right,
            )
        )
    return placements
