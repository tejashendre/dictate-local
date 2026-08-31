"""dictate_tray - the tray icon, which is what makes this feel like software.

A permanently floating pill is a distraction. Real Windows applications live in
the notification area: quiet when idle, there when you want them, and quit from
a right-click. That is the model here.

    tray icon      always present, colour shows state
    right-click    Settings, Edit my words, Start with Windows, Quit
    double-click   Settings
    the pill       appears only while listening or thinking, then hides

So when you are not dictating there is nothing on screen at all, and when you
are, the pill tells you it can hear you.

pystray runs its own Win32 message loop, and tkinter needs the main thread, so
the icon runs on a worker. Verified that the two coexist: the tray survives a
full tkinter mainloop and vice versa.
"""

import threading

try:
    import pystray
    from PIL import Image, ImageDraw
    AVAILABLE = True
except Exception:                       # pystray or Pillow missing
    AVAILABLE = False

# state -> (ring colour, fill colour, tooltip)
LOOK = {
    "idle":      ("#8a8a8a", None,      "Local Dictation - press F9 to talk"),
    "listening": ("#ff4d4d", "#ff4d4d", "Listening..."),
    "thinking":  ("#ffb020", "#ffb020", "Transcribing..."),
    "typed":     ("#3ddc84", "#3ddc84", "Typed"),
    "cpu":       ("#ffb020", None,      "Local Dictation - running on CPU"),
}


def _hex(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def make_icon(state="idle", size=64):
    """A microphone dot. Filled while active, hollow when idle, so the tray
    reads at a glance without needing colour vision."""
    ring, fill, _tip = LOOK.get(state, LOOK["idle"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, w = size * 0.16, max(2, size // 14)
    box = (pad, pad, size - pad, size - pad)
    d.ellipse(box, outline=_hex(ring), width=w)
    if fill:
        inner = size * 0.34
        d.ellipse((inner, inner, size - inner, size - inner), fill=_hex(fill))
    return img


class Tray:
    """Tray icon wrapper. Safe to construct even when pystray is missing."""

    def __init__(self, on_settings=None, on_quit=None, on_vocab=None,
                 on_toggle_startup=None, startup_enabled=lambda: False):
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.on_vocab = on_vocab
        self.on_toggle_startup = on_toggle_startup
        self.startup_enabled = startup_enabled
        self.icon = None
        self._thread = None
        self._state = "idle"

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("Settings", self._settings, default=True),
            pystray.MenuItem("Edit my words", self._vocab),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start with Windows", self._startup,
                             checked=lambda _i: bool(self.startup_enabled())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    # Menu callbacks run on the tray thread, so they only ever hand work back
    # to the UI thread rather than touching widgets themselves.
    def _settings(self, _icon=None, _item=None):
        if self.on_settings:
            self.on_settings()

    def _vocab(self, _icon=None, _item=None):
        if self.on_vocab:
            self.on_vocab()

    def _startup(self, _icon=None, _item=None):
        if self.on_toggle_startup:
            self.on_toggle_startup(not self.startup_enabled())
            if self.icon:
                self.icon.update_menu()

    def _quit(self, _icon=None, _item=None):
        self.stop()
        if self.on_quit:
            self.on_quit()

    # -- lifecycle --------------------------------------------------------

    def start(self):
        """Show the icon. Returns False if pystray is unavailable."""
        if not AVAILABLE:
            return False
        self.icon = pystray.Icon("dictate", make_icon("idle"),
                                 LOOK["idle"][2], self._menu())
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()
        return True

    def set_state(self, state):
        """Recolour the icon. Called from the audio and hotkey threads."""
        if not self.icon or state == self._state:
            return
        self._state = state
        try:
            self.icon.icon = make_icon(state)
            self.icon.title = LOOK.get(state, LOOK["idle"])[2]
        except Exception:
            pass

    def stop(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
