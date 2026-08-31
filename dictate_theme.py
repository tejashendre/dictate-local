"""dictate_theme - the Windows 11 window effects that make Tk stop looking like Tk.

Tk cannot draw a rounded window or a blurred backdrop; those are compositor
features. But the compositor will apply them to any window if you ask it
through DWM, which is what this does. Probed on this machine (build 26200) and
all of it is supported, including on the borderless pill:

    dark title bar          OK
    rounded corners         OK      also on an overrideredirect window
    Mica backdrop           OK
    Acrylic backdrop        OK
    custom border colour    OK

Everything degrades silently. On an older build the calls fail, the window is
square, and the app still works - so none of this is load-bearing.

Palette note: the colours below are a single set used by both the pill and the
settings window, so the app reads as one thing rather than two programs that
happen to ship together.
"""

import ctypes
from ctypes import wintypes

_dwm = ctypes.windll.dwmapi
_u32 = ctypes.windll.user32

# DWM window attributes
_DARK_MODE = 20
_CORNER_PREFERENCE = 33
_BORDER_COLOR = 34
_CAPTION_COLOR = 35
_CAPTION_TEXT_COLOR = 36
_BACKDROP_TYPE = 38

# corner preference values
CORNER_DEFAULT, CORNER_SQUARE, CORNER_ROUND, CORNER_ROUND_SMALL = 0, 1, 2, 3

# backdrop values
BACKDROP_NONE, BACKDROP_MICA, BACKDROP_ACRYLIC, BACKDROP_MICA_ALT = 1, 2, 3, 4

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------
# Slate rather than pure grey: neutral greys go muddy against Windows 11's
# own tinted surfaces, and a slight blue cast reads as deliberate.

SURFACE   = "#1c1c1c"     # settings window body, matches sv-ttk dark
BG        = "#12151c"     # pill body
BG_SOFT   = "#1a1f29"     # raised surface
BORDER    = "#2a3140"
FG        = "#e8edf5"
DIM       = "#8b96a8"

ACCENT    = "#4cc2ff"     # Windows 11 system accent blue
LIVE      = "#3ddc84"     # hearing you
BUSY      = "#ffb020"     # working
IDLE      = "#5a6472"


def _set(hwnd, attr, value):
    try:
        val = ctypes.c_int(int(value))
        return _dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), wintypes.DWORD(attr),
            ctypes.byref(val), ctypes.sizeof(val)) == 0
    except Exception:
        return False


def hwnd_of(widget):
    """The real toplevel handle behind a Tk widget."""
    try:
        wid = widget.winfo_id()
        return _u32.GetParent(wid) or wid
    except Exception:
        return None


def round_corners(widget, small=False):
    """Ask the compositor to round this window. Works on borderless windows."""
    hwnd = hwnd_of(widget)
    return bool(hwnd) and _set(hwnd, _CORNER_PREFERENCE,
                               CORNER_ROUND_SMALL if small else CORNER_ROUND)


def backdrop(widget, kind=BACKDROP_ACRYLIC):
    """Blurred system backdrop behind the window."""
    hwnd = hwnd_of(widget)
    return bool(hwnd) and _set(hwnd, _BACKDROP_TYPE, kind)


def dark_titlebar(widget):
    """Dark title bar, so a dark window does not wear a white hat."""
    hwnd = hwnd_of(widget)
    return bool(hwnd) and _set(hwnd, _DARK_MODE, 1)


def _to_bgr(hex_colour):
    """DWM wants BGR, not RGB. Swapping them is the classic bug here."""
    h = hex_colour.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def caption_colour(widget, bg="#1c1c1c", fg=FG):
    """Force the title bar dark.

    Dark mode alone is not enough. If "show accent colour on title bars" is on
    in Windows personalisation - it is on this machine - an ACTIVE window gets
    painted with the system accent instead, measured as #0078d4 bright blue
    above a dark window. Setting the caption colour explicitly overrides it,
    measured taking the title bar to #1c1c1c.
    """
    hwnd = hwnd_of(widget)
    if not hwnd:
        return False
    ok = _set(hwnd, _CAPTION_COLOR, _to_bgr(bg))
    _set(hwnd, _CAPTION_TEXT_COLOR, _to_bgr(fg))
    return ok


def border_colour(widget, hex_colour=BORDER):
    """DWM wants BGR, not RGB - swapping them is the usual bug here."""
    hwnd = hwnd_of(widget)
    if not hwnd:
        return False
    return _set(hwnd, _BORDER_COLOR, _to_bgr(hex_colour))


def modernise(widget, kind="window"):
    """Apply the whole set. kind is 'window' or 'pill'.

    Returns a dict of what actually took, so callers can report honestly
    rather than assume.
    """
    if kind == "pill":
        return {
            "rounded": round_corners(widget, small=False),
            "backdrop": backdrop(widget, BACKDROP_ACRYLIC),
        }
    return {
        "dark": dark_titlebar(widget),
        "caption": caption_colour(widget, SURFACE, FG),
        "rounded": round_corners(widget),
        "backdrop": backdrop(widget, BACKDROP_MICA),
        "border": border_colour(widget),
    }
