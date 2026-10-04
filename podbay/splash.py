"""HAL 9000 eye screens: the ASCII eye above a typed dialog, used for the
startup splash and the quit sequence.

Both run the same sequence on a black screen: the eye materialises out of
the dark (core first), the dialog lines type out one at a time (the eye
pulses while HAL speaks, sits steady otherwise), a short hold, then the eye
dissolves back into the dark cell by cell while every colour darkens to
black. Each cell has a fixed materialise/dissolve moment, so the sequence
looks the same every time. Nothing here reads podbay's session state; the
lines come from `voice.py`.

The splash is pushed on app mount on top of the main screen (which is
already composing and kicking off its first background scan underneath)
and dismisses after the dissolve; app.py then fades the main screen in from
black (FADE_IN_SECONDS). A key press during the splash skips to the
dissolve. The shutdown screen is pushed by the quit action after the main
screen has faded to black, and exits the app after its dissolve; a key press
exits at once.

When podbay starts inside iTerm2, app.py captures the text that was on the
terminal before Textual took the screen and hands it in as the splash
backdrop: it is painted in dim gray under the eye, and dissolves out cell by
cell while the eye materialises, so HAL appears to draw himself onto the
terminal you were just looking at. Everything -- backdrop, eye, dialog -- is
composited into one full-screen Static, because a Textual widget paints its
cells with an opaque background and a stacked layout would have put the eye
in a black box over the backdrop (that is also why a translucent modal
crossfade into the main screen was tried and dropped).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.events import Key
from textual.screen import Screen
from rich.text import Text
from textual.events import Resize
from textual.widgets import Static

from . import voice

FPS = 12
FRAME_INTERVAL = 1 / FPS
TYPE_CHARS_PER_SECOND = 30
TYPE_INTERVAL = 1 / TYPE_CHARS_PER_SECOND
LINE_PAUSE_SECONDS = 0.6
HOLD_SECONDS = 2.0
SHUTDOWN_HOLD_SECONDS = 0.8
FADE_SECONDS = 0.9
FADE_FRAMES = int(FADE_SECONDS * FPS)
FADE_IN_SECONDS = 0.6
FADE_OUT_SECONDS = 0.4  # the main screen darkening before the shutdown eye

_USER_COLOR = "#d9c9a0"  # dim body text
_HAL_COLOR = "#e0201f"  # HAL red
_SEPARATOR = " > "

# Eye art: a filled ellipse banded into four concentric rings by normalised
# radius, each ring a fixed character + HAL-red shade. The "pulse" cycles
# which ring is drawn in its brighter highlight shade, core-out-and-back,
# giving the impression of a wave travelling through the eye. Every row is
# padded out to CONTAINER_WIDTH (below) so all rows -- and the eye as a
# whole -- are exactly the same width as the fixed-width splash container;
# without that, uneven trailing whitespace between rows left the eye
# looking off-centre.
_EYE_WIDTH = 37
_EYE_HEIGHT = 15
_BAND_CHARS = ["@", "o", ":", "."]  # core, inner, middle, outer
_BASE_COLORS = ["#fff2a8", "#e0201f", "#a01010", "#5a0000"]  # core, inner, middle, outer
_HIGHLIGHT_COLORS = ["#ffffff", "#ff5b3d", "#e0201f", "#a01010"]
_PULSE_SEQUENCE = [0, 1, 2, 3, 2, 1]
_STEADY_BAND = -1  # no band highlighted -> every ring drawn in its base shade


def _full_line(speaker: str, quote: str) -> str:
    return f"{speaker}{_SEPARATOR}{quote}"


# The lines name you, so the container is laid out for the longest name
# HAL uses (voice.NAME_MAX): the frames below are built once, at import.
_LONGEST_NAME = "W" * voice.NAME_MAX
_USER_FULL = _full_line(_LONGEST_NAME, voice.SPLASH_USER_LINE)
_HAL_FULL = _full_line("HAL".ljust(voice.NAME_MAX), f"I'm sorry, {_LONGEST_NAME}. I'm afraid I can't do that.")
_SHUTDOWN_FULL = _full_line(voice.SHUTDOWN_HAL_SPEAKER, f"My mind is going, {_LONGEST_NAME}. I can feel it.")

# The container has to be at least as wide as the eye and at least as wide
# as any dialog line, so pick the largest.
CONTAINER_WIDTH = max(_EYE_WIDTH, len(_USER_FULL), len(_HAL_FULL), len(_SHUTDOWN_FULL))
_EYE_LEFT_PAD = (CONTAINER_WIDTH - _EYE_WIDTH) // 2
_EYE_RIGHT_PAD = CONTAINER_WIDTH - _EYE_WIDTH - _EYE_LEFT_PAD


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


def _build_grid() -> list[list[int]]:
    a = (_EYE_WIDTH - 1) / 2
    b = (_EYE_HEIGHT - 1) / 2
    grid = []
    for y in range(_EYE_HEIGHT):
        row = [_band_for((x - a) / a, (y - b) / b) for x in range(_EYE_WIDTH)]
        grid.append(row)
    return grid


def _runs(row: list[int]) -> list[tuple[int, int]]:
    """Collapse a row into (band, run_length) pairs so each frame emits one
    markup span per contiguous ring segment instead of one per character."""
    runs = []
    cur_band, cur_len = row[0], 1
    for band in row[1:]:
        if band == cur_band:
            cur_len += 1
        else:
            runs.append((cur_band, cur_len))
            cur_band, cur_len = band, 1
    runs.append((cur_band, cur_len))
    return runs


def _render_frame(grid: list[list[int]], highlight_band: int) -> str:
    pad_left = " " * _EYE_LEFT_PAD
    pad_right = " " * _EYE_RIGHT_PAD
    lines = []
    for row in grid:
        parts = [pad_left]
        for band, length in _runs(row):
            if band == -1:
                parts.append(" " * length)
                continue
            chars = _BAND_CHARS[band] * length
            color = _HIGHLIGHT_COLORS[band] if band == highlight_band else _BASE_COLORS[band]
            parts.append(f"[{color}]{chars}[/{color}]")
        parts.append(pad_right)
        lines.append("".join(parts))
    return "\n".join(lines)


def _build_eye_frames() -> list[str]:
    grid = _build_grid()
    return [_render_frame(grid, band) for band in _PULSE_SEQUENCE]


_GRID = _build_grid()
STEADY_FRAME = _render_frame(_GRID, _STEADY_BAND)
EYE_FRAMES = _build_eye_frames()


def _darken(color: str, keep: float) -> str:
    """Scale a #rrggbb colour toward black; keep=1 is unchanged, 0 is black."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return f"#{int(r * keep):02x}{int(g * keep):02x}{int(b * keep):02x}"


def _dissolve_at(x: int, y: int) -> float:
    """The moment in [0, 1) at which cell (x, y) drops out of the eye. A
    fixed hash rather than random so the dissolve is deterministic and the
    outer rings, which have the highest band, go first on average."""
    h = (x * 73856093) ^ (y * 19349663)
    noise = ((h >> 3) % 1000) / 1000.0
    band = _GRID[y][x]
    bias = (3 - band) * 0.12 if band >= 0 else 0.0  # core 0.36 later than outer
    return min(0.98, noise * 0.6 + bias)


def _render_fade_frame(t: float) -> str:
    """The eye at dissolve progress t in [0, 1]: cells past their dissolve
    moment are blank, the rest are drawn in the base shade darkened by
    (1 - t)."""
    keep = max(0.0, 1.0 - t)
    pad_left = " " * _EYE_LEFT_PAD
    pad_right = " " * _EYE_RIGHT_PAD
    lines = []
    for y, row in enumerate(_GRID):
        parts = [pad_left]
        for x, band in enumerate(row):
            if band == -1 or t >= _dissolve_at(x, y):
                parts.append(" ")
                continue
            color = _darken(_BASE_COLORS[band], keep)
            parts.append(f"[{color}]{_BAND_CHARS[band]}[/{color}]")
        parts.append(pad_right)
        lines.append("".join(parts))
    return "\n".join(lines)


FADE_FRAMES_ART = [_render_fade_frame((i + 1) / FADE_FRAMES) for i in range(FADE_FRAMES)]
# The eye coming in is the dissolve run backwards: the core lights first,
# the outer rings last, and the whole thing brightens up from black.
MATERIALISE_FRAMES_ART = list(reversed(FADE_FRAMES_ART[:-1])) + [STEADY_FRAME]


_LABEL_COLOR = "#e0e0e0"  # speaker label and separator during the dissolve
_BACKDROP_COLOR = "#8a8a8a"  # the captured terminal text under the eye


def _backdrop_dissolve_at(x: int, y: int) -> float:
    """The moment in [0, 0.9) at which backdrop cell (x, y) drops out; a
    fixed hash so the dissolve is deterministic and evenly speckled."""
    h = (x * 83492791) ^ (y * 29669837)
    return ((h >> 4) % 1000) / 1000.0 * 0.9


def _backdrop_rows(lines: list[str], width: int, t: float) -> list[Text]:
    """The backdrop at dissolve progress t: cells past their moment are
    blank, the rest darken with t. t >= 0.9 leaves nothing."""
    color = _darken(_BACKDROP_COLOR, max(0.0, 1.0 - t * 0.8))
    rows = []
    for y, line in enumerate(lines):
        line = line[:width].ljust(width)
        if t <= 0:
            rows.append(Text(line, style=color))
            continue
        chars = [c if (c == " " or t < _backdrop_dissolve_at(x, y)) else " " for x, c in enumerate(line)]
        rows.append(Text("".join(chars), style=color))
    return rows


def _overlay(row: Text, col: int, piece: Text) -> Text:
    """`piece` written over `row` starting at `col`, keeping both styles."""
    end = col + len(piece.plain)
    out = row[:col]
    out.append_text(piece)
    out.append_text(row[end:])
    return out


def _overlay_ink(row: Text, col: int, piece: Text) -> Text:
    """Like _overlay but only the non-space runs of `piece` land, so the
    backdrop stays visible between and around the eye's rings."""
    plain = piece.plain
    x = 0
    while x < len(plain):
        if plain[x] == " ":
            x += 1
            continue
        start = x
        while x < len(plain) and plain[x] != " ":
            x += 1
        row = _overlay(row, col + start, piece[start:x])
    return row


def _typed_markup(speaker: str, quote: str, color: str, revealed: int, label_color: str | None = None) -> str:
    """Render the first `revealed` characters of "SPEAKER > quote", with the
    speaker label bold and the quote coloured for its speaker. label_color
    is only given during the dissolve, when the label has to darken too."""
    if revealed <= 0:
        return ""
    parts = []
    label_revealed = min(revealed, len(speaker))
    if label_color is None:
        parts.append(f"[bold]{speaker[:label_revealed]}[/bold]")
    else:
        parts.append(f"[bold {label_color}]{speaker[:label_revealed]}[/]")
    remaining = revealed - len(speaker)
    if remaining <= 0:
        return "".join(parts)
    sep_revealed = min(remaining, len(_SEPARATOR))
    sep = _SEPARATOR[:sep_revealed]
    parts.append(sep if label_color is None else f"[{label_color}]{sep}[/]")
    remaining -= len(_SEPARATOR)
    if remaining <= 0:
        return "".join(parts)
    quote_revealed = min(remaining, len(quote))
    parts.append(f"[{color}]{quote[:quote_revealed]}[/{color}]")
    return "".join(parts)


@dataclass(frozen=True)
class DialogLine:
    speaker: str
    quote: str
    color: str
    pulse: bool  # the eye pulses while this line types (HAL speaking)

    @property
    def full(self) -> str:
        return _full_line(self.speaker, self.quote)


def splash_lines() -> tuple[DialogLine, ...]:
    """Built when a screen starts, so a name given at the first start is in
    the lines."""
    user, hal = voice.splash_speakers()
    return (
        DialogLine(user, voice.SPLASH_USER_LINE, _USER_COLOR, pulse=False),
        DialogLine(hal, voice.splash_hal_line(), _HAL_COLOR, pulse=True),
    )


def shutdown_lines() -> tuple[DialogLine, ...]:
    return (DialogLine(voice.SHUTDOWN_HAL_SPEAKER, voice.shutdown_hal_line(), _HAL_COLOR, pulse=True),)


class EyeScreen(Screen[None]):
    """The eye sequence: materialise -> lines type out -> hold -> dissolve
    -> on_complete(). Subclasses pick the lines, the hold and what a key
    press does. The whole frame is composited into one Static (see the
    module docstring for why)."""

    LINES: tuple[DialogLine, ...] = ()
    HOLD: float = HOLD_SECONDS

    def _lines(self) -> tuple[DialogLine, ...]:
        return splash_lines()

    DEFAULT_CSS = """
    EyeScreen {
        background: #000000;
    }
    #canvas {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, on_complete: Callable[[], None] | None = None, backdrop: list[str] | None = None) -> None:
        super().__init__()
        self._on_complete = on_complete
        self._backdrop = backdrop
        self.LINES = self._lines()
        self._backdrop_t = 0.0  # dissolve progress of the backdrop, 1 = gone
        self._eye = MATERIALISE_FRAMES_ART[0]
        self._keep = 1.0  # colour brightness of the dialog during the dissolve
        self._typed = [0] * len(self.LINES)
        self._line_idx = 0
        self._frame_idx = 0
        self._mat_idx = 0
        self._fade_idx = 0
        self._done = False
        self._fading = False
        self._type_timer = None
        self._pulse_timer = None
        self._mat_timer = None
        self._fade_timer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="canvas")

    # -- painting ----------------------------------------------------------

    def _paint(self) -> None:
        width, height = self.app.size
        if width <= 0 or height <= 0:
            return
        if self._backdrop is not None and self._backdrop_t < 1.0:
            rows = _backdrop_rows(self._backdrop[-height:], width, self._backdrop_t)
            rows = [Text(" " * width)] * (height - len(rows)) + rows
        else:
            rows = [Text(" " * width) for _ in range(height)]
        block_h = _EYE_HEIGHT + 2 * len(self.LINES) - 1
        top = max(0, (height - block_h) // 2)
        left = max(0, (width - CONTAINER_WIDTH) // 2)
        for i, eye_row in enumerate(self._eye.split("\n")):
            y = top + i
            if y < height:
                rows[y] = _overlay_ink(rows[y], left, Text.from_markup(eye_row))
        label = None if self._keep >= 1.0 else _darken(_LABEL_COLOR, self._keep)
        for i, line in enumerate(self.LINES):
            y = top + _EYE_HEIGHT + 2 * i
            if y >= height or self._typed[i] <= 0:
                continue
            markup = _typed_markup(line.speaker, line.quote, _darken(line.color, self._keep), self._typed[i], label_color=label)
            rows[y] = _overlay(rows[y], left, Text.from_markup(markup))
        self.query_one("#canvas", Static).update(Text("\n").join(rows))

    def on_resize(self, event: Resize) -> None:
        self._paint()

    # -- materialise -------------------------------------------------------

    def on_mount(self) -> None:
        self._paint()
        self._mat_timer = self.set_interval(FRAME_INTERVAL, self._tick_materialise)

    def _tick_materialise(self) -> None:
        if self._mat_idx >= len(MATERIALISE_FRAMES_ART):
            self._mat_timer.stop()
            self._backdrop_t = 1.0
            self._paint()
            self._start_line(0)
            return
        self._eye = MATERIALISE_FRAMES_ART[self._mat_idx]
        self._mat_idx += 1
        self._backdrop_t = self._mat_idx / len(MATERIALISE_FRAMES_ART)
        self._paint()

    # -- dialog ------------------------------------------------------------

    def _start_line(self, idx: int) -> None:
        if self._fading or self._done:
            return
        self._line_idx = idx
        self._type_timer = self.set_interval(TYPE_INTERVAL, self._tick_type)
        if self.LINES[idx].pulse:
            self._pulse_timer = self.set_interval(FRAME_INTERVAL, self._tick_pulse)

    def _tick_pulse(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(EYE_FRAMES)
        self._eye = EYE_FRAMES[self._frame_idx]
        self._paint()

    def _tick_type(self) -> None:
        idx = self._line_idx
        line = self.LINES[idx]
        self._typed[idx] += 1
        self._paint()
        if self._typed[idx] < len(line.full):
            return
        self._type_timer.stop()
        if self._pulse_timer is not None:
            self._pulse_timer.stop()
            self._pulse_timer = None
            self._eye = STEADY_FRAME
            self._paint()
        if idx + 1 < len(self.LINES):
            self.set_timer(LINE_PAUSE_SECONDS, lambda: self._start_line(idx + 1))
        else:
            self.set_timer(self.HOLD, self._start_fade)

    # -- dissolve ----------------------------------------------------------

    def _start_fade(self) -> None:
        if self._fading or self._done:
            return
        self._fading = True
        self._backdrop_t = 1.0
        for timer in (self._mat_timer, self._type_timer, self._pulse_timer):
            if timer is not None:
                timer.stop()
        self._fade_timer = self.set_interval(FRAME_INTERVAL, self._tick_fade)

    def _tick_fade(self) -> None:
        self._fade_idx += 1
        if self._fade_idx > FADE_FRAMES:
            self._finish()
            return
        self._keep = 1.0 - self._fade_idx / FADE_FRAMES
        self._eye = FADE_FRAMES_ART[self._fade_idx - 1]
        self._paint()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        for timer in (self._mat_timer, self._type_timer, self._pulse_timer, self._fade_timer):
            if timer is not None:
                timer.stop()
        if self._on_complete is not None:
            self._on_complete()

    def on_key(self, event: Key) -> None:
        event.stop()
        self._start_fade()


class SplashScreen(EyeScreen):
    """Startup: the eye comes up through the captured terminal text, you ask,
    HAL refuses, the eye dissolves and the screen dismisses so app.py
    can fade the main screen in. Any key skips to the dissolve."""

    HOLD = HOLD_SECONDS

    def _finish(self) -> None:
        if self._done:
            return
        super()._finish()
        self.dismiss(None)


class ShutdownScreen(EyeScreen):
    """Quit: the eye comes back up, HAL's mind goes, the eye dissolves and
    on_complete (app.exit) runs. Any key exits at once."""

    HOLD = SHUTDOWN_HOLD_SECONDS

    def _lines(self) -> tuple[DialogLine, ...]:
        return shutdown_lines()

    def on_key(self, event: Key) -> None:
        event.stop()
        self._finish()
