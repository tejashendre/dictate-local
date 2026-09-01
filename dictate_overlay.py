"""dictate_overlay - the small always-on-top pill that shows what the tool is doing.

Without this, dictation is invisible: you press a key and hope. The pill is the
difference between a script and something you can actually use in front of you.

The one thing that makes it possible, and the thing not to break:

    A normal always-on-top window STEALS KEYBOARD FOCUS when it appears.

That is fatal here. Everything this tool does is type into whatever window you
were already in, so an overlay that takes focus would type into itself instead
of your document.

WS_EX_NOACTIVATE alone does NOT fix it, which is the trap. Tracing the
foreground window through every step of construction showed where it actually
goes, and it is earlier than you would guess:

    tk.Tk()                     foreground unchanged
    root.withdraw()             foreground unchanged
    build widgets
    root.update_idletasks()     <-- FOREGROUND IS TAKEN HERE, by a window
                                    called "tk", before any style exists
    apply WS_EX_NOACTIVATE      too late, we already have it
    ShowWindow(SW_SHOWNOACTIVATE)   too late
    SetWindowPos(SWP_NOACTIVATE)    too late

`withdraw()` does not save you: `update_idletasks()` realises and maps the
window anyway. And once the foreground has been taken, re-asserting the style
does not give it back - that was measured too.

So there are two halves to the fix and both are needed:

  1. WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW, so the pill cannot be activated by
     clicking it afterwards and stays out of alt-tab.
  2. Record the foreground window BEFORE creating anything, and hand it back
     with SetForegroundWindow once the pill is up. This is what actually
     returns focus to the document you were writing in.

Verified after the fix: the pill never holds the foreground across repeated
state changes with mainloop running. tests/test_overlay.py checks precisely
that - it asserts the pill never *becomes* the foreground window, rather than
that the foreground never changes, because the user switching apps mid-test is
not a failure.

WS_EX_TOOLWINDOW is the smaller companion fix: it keeps the pill out of the
alt-tab list, which is what you want from a status indicator.

tkinter is not thread-safe, so the audio and hotkey threads never touch the
widgets. They push a state onto a queue and the UI thread drains it.
"""

import ctypes
import os
import queue
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
POS_PATH = os.path.join(HERE, ".overlay-position")

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_u32 = ctypes.windll.user32
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_SW_HIDE = 0
_SW_SHOWNOACTIVATE = 4
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040

import collections

try:
    import dictate_theme as theme
except Exception:
    theme = None

# Tk's canvas has no anti-aliasing: a 6-pixel create_oval draws a visible
# octagon, and the meter bars come out with hard jagged edges. Both are obvious
# the moment the pill is looked at closely.
#
# PIL does anti-alias, and is already here for the tray icon. So the dot and
# the waveform are drawn into an image at SUPERSAMPLE times the size and
# scaled down with LANCZOS, which is what actually makes the curves smooth.
try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    HD = True
except Exception:
    HD = False

SUPERSAMPLE = 4

# state -> (dot colour, label)
#
# The labels matter as much as the colours. A blue dot with no word next to it
# was reported as "the bot disappearing into a blue dot, I do not know what it
# means" - which is a fair complaint about a status indicator.
STATES = {
    "idle":      ("#5a6472", "F9 to talk"),
    "listening": ("#3ddc84", "listening"),
    "thinking":  ("#ffb020", "writing it"),
    "typed":     ("#4cc2ff", "done"),
    "cpu":       ("#ffb020", "on CPU"),
}

# One size, and it stays that way.
#
# A version that grew to 320x40 while listening was built and rejected: the
# bigger pill read as intrusive rather than impressive, and the compact chip
# was better all along. The refinement that was actually wanted is in the
# RENDERING - bloom, gradient, anti-aliased curves - not in the dimensions.
PILL_W = 189
PILL_H = 30

# Microphone waveform. Speech RMS sits around 0.01-0.15 on this machine;
# below SILENT_RMS nothing is really arriving.
#
# Bars show a scrolling HISTORY of recent levels, not the current level copied
# across, which is what makes it look alive rather than like a progress bar.
METER_BARS = 9
METER_OFF = "#2a3140"
METER_ON = "#3ddc84"
METER_HOT = "#ffb020"
SILENT_RMS = 0.002
# How long of nothing before saying the microphone is not being heard.
#
# Only ever shown when NOTHING has been heard since recording started. The
# first version warned after any 2.5s of quiet, which fired during ordinary
# thinking pauses mid-sentence and made a working tool look stuck. Real
# dictation has long gaps: one logged phrase ran 22.8s for 27 words.
SILENT_WARN_S = 3.0

# Slate rather than neutral grey: flat grey goes muddy against the tinted
# surfaces Windows 11 puts behind a translucent window.
BG = "#12151c"        # bottom of the gradient
BG_TOP = "#1b2029"    # top of the gradient, slightly lifted
EDGE = "#2c3442"      # the lit hairline along the top edge
FG = "#e8edf5"
DIM = "#8b96a8"


class Overlay:
    """A borderless status pill that never takes focus.

    Call set_state() from any thread. Call run() on the main thread.
    """

    def __init__(self, hotkey="F9", on_quit=None, on_settings=None,
                 level_source=None, auto_hide=True):
        self.hotkey = hotkey
        self.on_quit = on_quit
        self.on_settings = on_settings     # called with the tk root
        self.level_source = level_source   # returns current mic loudness
        # A pill that is always on screen is a distraction. With the tray icon
        # carrying the "I exist" job, the pill only needs to appear while
        # something is actually happening.
        self.auto_hide = auto_hide
        self._visible = not auto_hide
        self._settings_win = None
        self._silent_since = None
        self._heard_anything = False
        self._q = queue.Queue()
        self._state = "idle"
        self._detail = ""
        self._started = 0.0
        self._clear_at = 0.0
        self.root = None

    # -- public, thread-safe ---------------------------------------------

    def set_state(self, state, detail=""):
        """Safe to call from the audio or hotkey thread."""
        self._q.put((state, detail))

    def stop(self):
        self._q.put(("__quit__", ""))

    # -- construction ----------------------------------------------------

    def _build(self):
        # Must be captured before tk.Tk() exists. update_idletasks() below
        # takes the foreground, and this is what we hand it back to.
        prev_fg = _u32.GetForegroundWindow()

        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        try:
            root.attributes("-alpha", 0.94)
        except tk.TclError:
            pass

        # One canvas for the whole pill, so a rendered gradient can sit behind
        # everything. Text stays native: Windows draws small type with
        # subpixel anti-aliasing that beats anything PIL would produce here,
        # so only the graphics go through PIL.
        self._w, self._h = PILL_W, PILL_H
        self._bars = METER_BARS
        self.bg = tk.Canvas(root, width=self._w, height=self._h, bg=BG,
                            highlightthickness=0, bd=0)
        self.bg.pack(fill="both", expand=True)
        self._bg_img = None
        self._bg_id = self.bg.create_image(self._w // 2, self._h // 2)
        self._draw_background()

        cy = self._h // 2
        self._dot_img = None
        self._dot_id = self.bg.create_image(15, cy)
        self._draw_dot(STATES["idle"][0])

        self._label_id = self.bg.create_text(
            27, cy, text="F9 to talk", anchor="w", fill=FG,
            font=("Segoe UI Semibold", 9))

        self._bar_gap = 4
        self._meter_h = self._h - 12
        self._meter_w = self._bars * self._bar_gap
        self._wave_img = None
        self._wave_id = self.bg.create_image(self._w - 58, cy)

        self._timer_id = self.bg.create_text(
            self._w - 12, cy, text="", anchor="e", fill=DIM,
            font=("Cascadia Mono", 8))

        self._history = collections.deque([0.0] * self._bars,
                                          maxlen=self._bars)
        self._draw_wave()

        root.update_idletasks()             # foreground is taken here
        self._place(root)
        self._make_non_activating(root)
        self._show_without_activating(root)
        self._give_focus_back(prev_fg, root)
        self._visible = True

        # Rounded corners come from the compositor, not from Tk. Verified
        # supported on this build even for a borderless window; a machine
        # without it simply keeps square corners.
        if theme is not None:
            try:
                theme.round_corners(root)
            except Exception:
                pass

        # One canvas now, so only two widgets need the bindings.
        for w in (root, self.bg):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Button-3>", lambda _e: self._open_settings())
            w.bind("<Double-Button-1>", lambda _e: self._open_settings())

        self.root = root

    @staticmethod
    def _hwnd(root):
        """The real toplevel. With overrideredirect set, tkinter's own window
        is a child of it, so the style has to go on the parent."""
        return _u32.GetParent(root.winfo_id()) or root.winfo_id()

    def _make_non_activating(self, root):
        """The load-bearing part. Must run while the window is still hidden."""
        hwnd = self._hwnd(root)
        style = _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE,
                            style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW)

    def _show_without_activating(self, root):
        """Map the window explicitly, at the position we chose, without
        giving it the foreground."""
        hwnd = self._hwnd(root)
        x, y = getattr(self, "_pos", (0, 0))
        _u32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        _u32.SetWindowPos(hwnd, _HWND_TOPMOST, int(x), int(y), 0, 0,
                          _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW)

    def _set_visible(self, show):
        """Show or hide without activating. Never withdraw/deiconify here -
        deiconify re-maps the window and takes the foreground."""
        if show == self._visible:
            return
        hwnd = self._hwnd(self.root)
        if show:
            x, y = getattr(self, "_pos", (0, 0))
            _u32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
            _u32.SetWindowPos(hwnd, _HWND_TOPMOST, int(x), int(y), 0, 0,
                              _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW)
        else:
            _u32.ShowWindow(hwnd, _SW_HIDE)
        self._visible = show

    def _give_focus_back(self, prev_fg, root):
        """Return the foreground to whatever had it before the pill appeared.

        This is the half that actually works. SetForegroundWindow normally
        refuses callers that do not already own the foreground - here we do own
        it, having just taken it by accident, so handing it back is allowed.
        """
        if prev_fg and prev_fg != self._hwnd(root):
            try:
                _u32.SetForegroundWindow(prev_fg)
            except Exception:
                pass

    def _place(self, root):
        """Restore the last position, else sit above the taskbar, centred.

        update_idletasks() after geometry() is load-bearing. Without it tkinter
        has not applied the position yet when ShowWindow takes over, and the
        pill lands at 0,0 - on top of the title bar of whatever is behind it.
        That is exactly what it was doing.
        """
        w, h = self._w, self._h
        pos = None
        try:
            with open(POS_PATH) as f:
                x, y = (int(v) for v in f.read().split(","))
                pos = (x, y)
        except Exception:
            pass
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        default = ((sw - w) // 2, sh - h - 68)
        # Reject a stored position that is off-screen or jammed into a corner.
        # A position saved by an earlier, buggy build should not strand the
        # pill where it cannot be seen or dragged.
        if pos is not None:
            x, y = pos
            if not (0 <= x <= sw - 40 and 0 <= y <= sh - 20) or (x < 40 and y < 40):
                pos = None
        if pos is None:
            pos = default
        root.geometry("%dx%d+%d+%d" % (w, h, pos[0], pos[1]))
        root.update_idletasks()        # apply it before ShowWindow takes over
        self._pos = pos

    # -- dragging --------------------------------------------------------

    def _window_xy(self):
        """Real screen position. winfo_x/winfo_y report 0 for an
        overrideredirect window, which made dragging jump."""
        rect = _RECT()
        if _u32.GetWindowRect(self._hwnd(self.root), ctypes.byref(rect)):
            return rect.left, rect.top
        return 0, 0

    def _drag_start(self, event):
        x, y = self._window_xy()
        self._dx = event.x_root - x
        self._dy = event.y_root - y

    def _drag_move(self, event):
        x = event.x_root - self._dx
        y = event.y_root - self._dy
        self.root.geometry("+%d+%d" % (x, y))
        try:
            with open(POS_PATH, "w") as f:
                f.write("%d,%d" % (x, y))
        except Exception:
            pass

    # -- loop ------------------------------------------------------------

    def _pump(self):
        while True:
            try:
                state, detail = self._q.get_nowait()
            except queue.Empty:
                break
            if state == "__quit__":
                self._quit()
                return
            self._apply(state, detail)

        if self._state == "listening":
            self.bg.itemconfigure(self._timer_id,
                                  text="%4.1fs" % (time.time() - self._started))
            self._update_meter()
        elif self._clear_at and time.time() > self._clear_at:
            self._apply("idle", "")

        self.root.after(100, self._pump)

    def _update_meter(self):
        """Light the bars, and say so if the microphone has gone quiet.

        The warning is what makes a dead microphone diagnosable instead of
        mysterious. A real pause between sentences is under a second; two and
        a half seconds of true silence while recording means the input device
        is muted, wrong, or has no permission.
        """
        level = 0.0
        if self.level_source:
            try:
                level = float(self.level_source())
            except Exception:
                level = 0.0

        # Speech is roughly 0.002 to 0.15 RMS. Square-root spreads the quiet
        # end out so normal talking fills most of the meter.
        frac = 0.0 if level <= SILENT_RMS else min(1.0, (level / 0.15) ** 0.5)
        self._history.append(frac)
        self._draw_wave()

        now = time.time()
        if level > SILENT_RMS:
            self._silent_since = None
            self._heard_anything = True
            if self.bg.itemcget(self._label_id, "text") != "listening":
                self.bg.itemconfigure(self._label_id, text="listening", fill=FG)
        else:
            if self._silent_since is None:
                self._silent_since = now
            elif now - self._silent_since > SILENT_WARN_S:
                if self._heard_anything:
                    # A pause, not a fault. Say so plainly rather than
                    # implying something is wrong.
                    self.bg.itemconfigure(self._label_id,
                                          text="listening - go on",
                                          fill=DIM)
                else:
                    self.bg.itemconfigure(self._label_id,
                                          text="no sound - check mic",
                                          fill=METER_HOT)

    @staticmethod
    def _rgb(hex_colour):
        h = hex_colour.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    def _draw_dot(self, colour):
        """A round dot with a soft halo.

        create_oval at this size draws a visible octagon. The halo is what
        stops it reading as a flat sticker: a live indicator on Windows 11 has
        some bloom to it, and without that the pill looks printed on.
        """
        if not HD:
            return
        d = 15                       # room around the dot for the glow
        big = d * SUPERSAMPLE
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = big / 2.0
        rgb = self._rgb(colour)

        r = 3.0 * SUPERSAMPLE
        draw.ellipse((cx - r, cx - r, cx + r, cx + r), fill=rgb + (255,))

        # A real blur under the dot, not concentric rings. Side by side the
        # ring version came out dull and muddy; a blurred copy composited
        # underneath reads as an actual light source. The dot only redraws on
        # a state change, so the blur costs nothing per frame.
        glow = img.filter(ImageFilter.GaussianBlur(radius=2.2 * SUPERSAMPLE))
        img = Image.alpha_composite(
            Image.alpha_composite(Image.new("RGBA", (big, big), (0, 0, 0, 0)),
                                  glow), img)
        img = img.resize((d, d), Image.LANCZOS)
        self._dot_img = ImageTk.PhotoImage(img)   # keep a reference or it goes
        self.bg.itemconfig(self._dot_id, image=self._dot_img)

    def _draw_background(self):
        """A gradient body with a lit top edge.

        Flat fill is what made this look cheap however sharp the graphics
        were. Real Windows 11 surfaces are slightly lighter at the top and
        carry a one-pixel highlight along the upper edge; that is most of the
        difference between "a coloured rectangle" and "a surface".
        """
        if not HD:
            return
        w, h = self._w, self._h
        big_w, big_h = w * 2, h * SUPERSAMPLE
        img = Image.new("RGBA", (big_w, big_h))
        draw = ImageDraw.Draw(img)
        top = self._rgb(BG_TOP)
        bottom = self._rgb(BG)
        for y in range(big_h):
            f = y / max(big_h - 1, 1)
            draw.line([(0, y), (big_w, y)],
                      fill=tuple(int(top[c] + (bottom[c] - top[c]) * f)
                                 for c in range(3)) + (255,))
        # the highlight along the very top, as Windows does it
        draw.rectangle((0, 0, big_w, SUPERSAMPLE - 1),
                       fill=self._rgb(EDGE) + (255,))
        img = img.resize((w, h), Image.LANCZOS)
        self._bg_img = ImageTk.PhotoImage(img)
        self.bg.itemconfig(self._bg_id, image=self._bg_img)

    def _show_meter(self, on):
        """The meter belongs to listening and nothing else.

        Left visible in the other states it shows a row of stubs that reads as
        leftover debris rather than as an idle meter.
        """
        try:
            self.bg.itemconfigure(self._wave_id,
                                  state="normal" if on else "hidden")
        except Exception:
            pass

    def _draw_wave(self):
        """Symmetric bars grown from a centre line, oldest on the left.

        This is what people expect a voice meter to look like. The previous
        left-to-right fill read as a battery gauge, which is why it never
        looked like it was doing anything.

        Drawn through PIL with rounded caps and supersampling: the canvas
        version had hard jagged edges, and when quiet it left a row of dots
        that read as broken rather than idle.
        """
        if not HD:
            return
        w, h = self._meter_w, self._meter_h
        big_w, big_h = w * SUPERSAMPLE, h * SUPERSAMPLE
        # Transparent, not filled with BG: an opaque backing shows up as a
        # dark rectangle sitting on the gradient, which is exactly the kind of
        # detail that makes a UI look pasted together.
        img = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        mid = big_h / 2.0
        bar_w = 2 * SUPERSAMPLE
        gap = self._bar_gap * SUPERSAMPLE
        radius = bar_w / 2.0

        for i in range(self._bars):
            v = self._history[i] if i < len(self._history) else 0.0
            x0 = i * gap
            x1 = x0 + bar_w
            if v <= 0.0:
                # A short stub, not a dot: reads as "quiet" instead of "dead".
                half = radius
                colour = self._rgb(METER_OFF)
            else:
                half = max(radius, v * (mid - radius))
                # Colour walks toward cyan as it gets louder, so the meter
                # carries amplitude in hue as well as height. A single flat
                # green reads as a static graphic however tall the bars are.
                colour = (int(60 + 40 * v), int(220 - 20 * v),
                          int(130 + 110 * v))
            draw.rounded_rectangle((x0, mid - half, x1, mid + half),
                                   radius=radius, fill=colour + (255,))

        # Bloom, the same trick as the dot. Measured below 2 ms per frame at
        # this size, against a 100 ms redraw, so it is affordable.
        glow = img.filter(ImageFilter.GaussianBlur(radius=2.5 * SUPERSAMPLE))
        img = Image.alpha_composite(
            Image.alpha_composite(Image.new("RGBA", (big_w, big_h),
                                            (0, 0, 0, 0)), glow), img)
        img = img.resize((w, h), Image.LANCZOS)
        self._wave_img = ImageTk.PhotoImage(img)
        self.bg.itemconfig(self._wave_id, image=self._wave_img)

    def _clear_meter(self):
        """Flatten the waveform. Clears the history too, so the next
        recording starts from silence rather than replaying the last one."""
        self._history.clear()
        self._history.extend([0.0] * self._bars)
        self._draw_wave()
        self._silent_since = None

    def _apply(self, state, detail):
        colour, text = STATES.get(state, STATES["idle"])
        if self.auto_hide:
            self._set_visible(state != "idle")
        self._show_meter(state == "listening")
        if state == "idle":
            text = "%s to talk" % self.hotkey
        self._state = state
        self._draw_dot(colour)
        shown = detail or text
        if len(shown) > 17:
            shown = shown[:16] + "…"
        self.bg.itemconfigure(self._label_id, text=shown,
                              fill=FG if state != "idle" else DIM)

        if state == "listening":
            self._started = time.time()
            self._clear_at = 0.0
            self._silent_since = None
            self._heard_anything = False
            self.bg.itemconfigure(self._timer_id, text=" 0.0s")
        elif state == "typed":
            self.bg.itemconfigure(self._timer_id, text="")
            self._clear_meter()
            self._clear_at = time.time() + 2.5
        else:
            self.bg.itemconfigure(self._timer_id, text="")
            self._clear_meter()
            self._clear_at = 0.0

        # Deliberately no geometry() call here. Resizing a shown
        # overrideredirect window re-maps it and takes the foreground, which
        # was measured: with a resize on every state change, "listening" stole
        # focus even though the style was correct. The pill is a fixed width
        # and the text is truncated to fit instead.

    def _open_settings(self):
        """Right-click or double-click. Never a popup menu - see
        dictate_settings for why that hangs the process."""
        if not self.on_settings:
            return
        try:
            if self._settings_win and self._settings_win.win.winfo_exists():
                self._settings_win.win.lift()
                self._settings_win.win.focus_force()
                return
        except Exception:
            pass
        try:
            self._settings_win = self.on_settings(self.root)
        except Exception as e:
            print("  settings failed to open (%s)" % type(e).__name__)

    def _quit(self):
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        """Runs the UI. Must be called on the main thread."""
        self._build()
        self.root.after(100, self._pump)
        self.root.mainloop()


def hide_console():
    """Hide the console window, for when the pill is the only UI you want.

    Anything printed still goes to transcript.log, so nothing is lost.
    """
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            _u32.ShowWindow(hwnd, 0)      # SW_HIDE
            return True
    except Exception:
        pass
    return False
