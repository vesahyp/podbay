"""Small glyphs for the prompt modals, so each prompt reads differently at
a glance, by shape and by colour: Message shows HAL's open red eye (he is
listening), Park shows the same eye gone dormant, a dim amber slit (the
hibernation pods). Both are a compact cousin of the splash eye. Message to
Head Jeeves shows a blue bow tie, the butler, since he is not HAL and not a
session. Open shows the pod bay doors with light in the gap.
"""

from __future__ import annotations

from dataclasses import dataclass

WIDTH = 25
OPEN_HEIGHT = 7
DORMANT_HEIGHT = 3

_OPEN_CHARS = ["@", "o", ":", "."]  # core, inner, middle, outer
_OPEN_COLORS = ["#fff2a8", "#e0201f", "#a01010", "#5a0000"]
_DORMANT_CHARS = ["-", "-", ":", "."]
_DORMANT_COLORS = ["#ffb000", "#b07800", "#6a4a00", "#3a2a00"]


@dataclass(frozen=True)
class Glyph:
    """One prompt's artwork: the rendered markup, the caption under it and
    the accent colour the modal border takes."""

    art: str
    caption: str
    accent: str


def _eye_band(nx: float, ny: float) -> int:
    r = (nx * nx + ny * ny) ** 0.5
    if r > 1.0:
        return -1
    if r < 0.22:
        return 0
    if r < 0.48:
        return 1
    if r < 0.78:
        return 2
    return 3


def _bowtie_band(nx: float, ny: float) -> int:
    """The knot in the middle, two wings that widen outwards."""
    ax, ay = abs(nx), abs(ny)
    if ax < 0.1 and ay < 0.5:
        return 0
    if ay > 0.2 + 0.9 * ax:
        return -1
    if ax < 0.45:
        return 1
    if ax < 0.8:
        return 2
    return 3


def _doors_band(nx: float, ny: float) -> int:
    """Two door panels with ribs, a frame round them, light in the gap."""
    ax, ay = abs(nx), abs(ny)
    if ax > 0.95 or ay > 0.95:
        return 3
    if ax < 0.05:
        return 0
    if ay < 0.05 or 0.6 < ay < 0.7:
        return 1
    return 2


def _render(width: int, height: int, chars: list[str], colors: list[str], band_for=_eye_band) -> str:
    a = (width - 1) / 2
    b = (height - 1) / 2
    lines = []
    for y in range(height):
        parts = []
        run_band, run_len = None, 0
        for x in range(width + 1):
            band = band_for((x - a) / a, (y - b) / b) if x < width else None
            if band == run_band:
                run_len += 1
                continue
            if run_band is not None and run_len:
                if run_band == -1:
                    parts.append(" " * run_len)
                else:
                    c = colors[run_band]
                    parts.append(f"[{c}]{chars[run_band] * run_len}[/{c}]")
            run_band, run_len = band, 1
        lines.append("".join(parts).rstrip())
    return "\n".join(lines)


MESSAGE = Glyph(
    art=_render(WIDTH, OPEN_HEIGHT, _OPEN_CHARS, _OPEN_COLORS),
    caption="HAL IS LISTENING",
    accent="#e0201f",
)

PARK = Glyph(
    art=_render(WIDTH, DORMANT_HEIGHT, _DORMANT_CHARS, _DORMANT_COLORS),
    caption="HIBERNATION",
    accent="#ffb000",
)

HEAD_JEEVES = Glyph(
    art=_render(WIDTH, OPEN_HEIGHT, ["#", "=", ":", "."], ["#e8f4ff", "#7fb0d0", "#4a7a9a", "#24465a"], _bowtie_band),
    caption="TO HEAD JEEVES",
    accent="#7fb0d0",
)

OPEN = Glyph(
    art=_render(WIDTH, OPEN_HEIGHT, ["|", "=", ":", "#"], ["#fff2a8", "#9a9a9a", "#5a5a5a", "#3a3a3a"], _doors_band),
    caption="OPEN THE POD BAY DOORS",
    accent="#c8c8c8",
)
