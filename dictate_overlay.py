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

# state -> (dot colour, label)
STATES = {
    "idle":      ("#5a5a5a", "F9 to talk"),
    "listening": ("#ff4d4d", "listening"),
    "thinking":  ("#ffb020", "thinking"),
    "typed":     ("#3ddc84", "typed"),
    "cpu":       ("#ffb020", "on CPU"),
}

# Smaller and tighter after real use: the first version was too wide, with a
# visible gap between the label and the meter that made it look unfinished.
PILL_W = 208
PILL_H = 30

# Microphone meter. Speech RMS sits around 0.01-0.15 on this machine; below
# SILENT_RMS nothing is really arriving.
METER_BARS = 5
METER_OFF = "#2e2e2e"
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

BG = "#161616"
FG = "#e8e8e8"
DIM = "#8a8a8a"


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

        wrap = tk.Frame(root, bg=BG, padx=9, pady=5)
        wrap.pack(fill="both", expand=True)

        self.dot = tk.Canvas(wrap, width=10, height=10, bg=BG,
                             highlightthickness=0)
        self._dot_id = self.dot.create_oval(1, 1, 9, 9, fill="#5a5a5a",
                                            outline="")
        self.dot.pack(side="left", padx=(0, 7))

        self.label = tk.Label(wrap, text="F9 to talk", bg=BG, fg=FG,
                              font=("Segoe UI", 9), anchor="w")
        self.label.pack(side="left", fill="x", expand=True)

        # Live microphone meter. A muted mic and a working one look identical
        # without this, which is the single most confusing way for the tool to
        # fail: the pill says "listening" and nothing ever arrives.
        self.meter = tk.Canvas(wrap, width=METER_BARS * 4, height=11, bg=BG,
                               highlightthickness=0)
        self._bars = [
            self.meter.create_rectangle(i * 4, 3, i * 4 + 2, 11,
                                        fill=METER_OFF, outline="")
            for i in range(METER_BARS)]
        self.meter.pack(side="right", padx=(5, 0))

        self.timer = tk.Label(wrap, text="", bg=BG, fg=DIM,
                              font=("Consolas", 8))
        self.timer.pack(side="right")

        root.update_idletasks()             # foreground is taken here
        self._place(root)
        self._make_non_activating(root)
        self._show_without_activating(root)
        self._give_focus_back(prev_fg, root)
        self._visible = True

        for w in (root, wrap, self.label, self.dot, self.timer):
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
        w, h = PILL_W, PILL_H
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
            self.timer.config(text="%4.1fs" % (time.time() - self._started))
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
        lit = int(round(frac * METER_BARS))
        for i, bar in enumerate(self._bars):
            if i < lit:
                colour = METER_HOT if i >= METER_BARS - 1 else METER_ON
            else:
                colour = METER_OFF
            self.meter.itemconfig(bar, fill=colour)

        now = time.time()
        if level > SILENT_RMS:
            self._silent_since = None
            self._heard_anything = True
            if self.label.cget("text") != "listening":
                self.label.config(text="listening", fg=FG)
        else:
            if self._silent_since is None:
                self._silent_since = now
            elif now - self._silent_since > SILENT_WARN_S:
                if self._heard_anything:
                    # A pause, not a fault. Say so plainly rather than
                    # implying something is wrong.
                    self.label.config(text="listening - go on", fg=DIM)
                else:
                    self.label.config(text="no sound - check mic",
                                      fg=METER_HOT)

    def _clear_meter(self):
        for bar in self._bars:
            self.meter.itemconfig(bar, fill=METER_OFF)
        self._silent_since = None

    def _apply(self, state, detail):
        colour, text = STATES.get(state, STATES["idle"])
        if self.auto_hide:
            self._set_visible(state != "idle")
        if state == "idle":
            text = "%s to talk" % self.hotkey
        self._state = state
        self.dot.itemconfig(self._dot_id, fill=colour)
        shown = detail or text
        if len(shown) > 20:
            shown = shown[:19] + "…"
        self.label.config(text=shown,
                          fg=FG if state != "idle" else DIM)

        if state == "listening":
            self._started = time.time()
            self._clear_at = 0.0
            self._silent_since = None
            self._heard_anything = False
            self.timer.config(text=" 0.0s")
        elif state == "typed":
            self.timer.config(text="")
            self._clear_meter()
            self._clear_at = time.time() + 2.5
        else:
            self.timer.config(text="")
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
