"""Small HAL 9000 glyphs for the prompt modals, so the two prompts that
matter most read differently at a glance: Message shows HAL's open red eye
(he is listening), Park shows the same eye gone dormant, a dim amber slit
(the hibernation pods). Both are a compact cousin of the splash eye and use
the same banded-ellipse rendering.
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


def _band_for(nx: float, ny: float) -> int:
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


def _render(width: int, height: int, chars: list[str], colors: list[str]) -> str:
    a = (width - 1) / 2
    b = (height - 1) / 2
    lines = []
    for y in range(height):
        parts = []
        run_band, run_len = None, 0
        for x in range(width + 1):
            band = _band_for((x - a) / a, (y - b) / b) if x < width else None
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
