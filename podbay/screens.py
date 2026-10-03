"""Live display geometry, straight from CoreGraphics.

macOS exposes no screen list to a scripting client, and iTerm2 doesn't know
about displays either, so this calls CoreGraphics through ctypes: public,
documented API, nothing to install. Coordinates are the same top-left-origin
space AppleScript window bounds use, so a display's bounds can be handed to
iterm.set_window_bounds as they are -- the window server clamps the result
under the menu bar and above the dock by itself.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass

MAX_DISPLAYS = 16


@dataclass(frozen=True)
class Display:
    display_id: int
    bounds: tuple[int, int, int, int]  # x1, y1, x2, y2
    is_main: bool

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]


class _CGRect(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
        ("w", ctypes.c_double),
        ("h", ctypes.c_double),
    ]


def _core_graphics():
    path = ctypes.util.find_library("CoreGraphics")
    if path is None:
        return None
    try:
        lib = ctypes.CDLL(path)
    except OSError:
        return None
    lib.CGDisplayBounds.restype = _CGRect
    lib.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    lib.CGMainDisplayID.restype = ctypes.c_uint32
    return lib


def displays() -> list[Display]:
    """Every active display, main one first. Empty when CoreGraphics can't
    be reached (a non-mac, or a headless run) -- never raises."""
    lib = _core_graphics()
    if lib is None:
        return []
    count = ctypes.c_uint32()
    ids = (ctypes.c_uint32 * MAX_DISPLAYS)()
    try:
        if lib.CGGetActiveDisplayList(MAX_DISPLAYS, ids, ctypes.byref(count)) != 0:
            return []
        main_id = lib.CGMainDisplayID()
    except OSError:
        return []
    found = []
    for index in range(count.value):
        display_id = ids[index]
        rect = lib.CGDisplayBounds(display_id)
        bounds = (
            int(rect.x),
            int(rect.y),
            int(rect.x + rect.w),
            int(rect.y + rect.h),
        )
        found.append(Display(display_id=display_id, bounds=bounds, is_main=display_id == main_id))
    found.sort(key=lambda d: not d.is_main)
    return found


def arrange_display() -> Display | None:
    """Where windows should be laid out: the external display when one is
    connected, otherwise the built-in one."""
    found = displays()
    if not found:
        return None
    external = [d for d in found if not d.is_main]
    return external[0] if external else found[0]
