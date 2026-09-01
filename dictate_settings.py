"""dictate_settings - the settings window.

Three things here are deliberate, and two of them were mistakes first.

**Not a popup menu.** tk_popup on a WS_EX_NOACTIVATE window hangs the process:
it grabs input that a window which cannot take focus will never receive. So
settings open as an ordinary window, which is allowed to take focus because
you are not dictating while you are configuring. The pill stays
non-activating, and that is what protects your typing.

**Pure ttk, never plain tk.** A screenshot of the previous version showed why:
the window had a LIGHT GREY body behind dark content, section frames rendered
as harsh black rectangles, and the checkboxes sat on black chips. The cause
was mixing 31 plain `tk` widgets with 49 `ttk` ones. A `tk` widget paints its
own background and ignores the theme entirely, and a Toplevel defaults to
`SystemButtonFace`, which is light. Themed widgets only look right when
everything around them is also themed, including the window itself.

**Cards and switches, not label frames and tick boxes.** sv-ttk ships
`Card.TFrame` and `Switch.TCheckbutton`, which are the actual Windows 11
surfaces and toggles. `ttk.LabelFrame` is a Motif-era groove box and looks it.
"""

import os
import subprocess
import tkinter as tk
from tkinter import ttk

try:
    import sv_ttk
    THEMED = True
except Exception:
    THEMED = False

import dictate_theme as theme

HERE = os.path.dirname(os.path.abspath(__file__))

# Sun Valley dark's own body colour. The Toplevel must be set to this by hand:
# ttk styles it its children, never the window they sit in.
SURFACE = theme.SURFACE
RAISED = theme.RAISED    # cards, so grouping is visible against the body
ACCENT = theme.ACCENT
LIVE = theme.LIVE
MUTED = theme.MUTED
WARN = theme.WARN


def _force_dark_containers():
    """Make ttk containers dark on a Toplevel.

    sv-ttk themes the ROOT window, not other toplevels, and the difference is
    visible rather than academic. Sampled from the framebuffer on a Toplevel:

        ttk.Frame        #d9d9d9   light
        Card.TFrame      #1c1c1c   dark

    which is exactly the "light body with black cards" the window had. Setting
    the Toplevel's own background does NOT fix it - that was tested and made
    no difference, because a ttk widget paints itself from its style, not from
    the window behind it. Configuring the style is what works.

    Cards are given a slightly raised tone so they read as surfaces; sv-ttk
    ships them the same colour as the body, which makes grouping invisible.
    """
    st = ttk.Style()
    for name in ("TFrame", "TLabel", "TNotebook", "TRadiobutton",
                 "TCheckbutton", "Switch.TCheckbutton", "TLabelframe",
                 "TLabelframe.Label"):
        try:
            st.configure(name, background=SURFACE)
        except Exception:
            pass
    try:
        st.configure("Card.TFrame", background=RAISED)
        # widgets sitting on a card must match the card, not the body
        st.configure("Card.TLabel", background=RAISED)
    except Exception:
        pass


class SettingsWindow:
    """A normal window. Takes focus, unlike the pill, and that is intended."""

    def __init__(self, root, settings, on_apply, on_quit=None):
        self.settings = dict(settings)
        self.on_apply = on_apply
        self.on_quit = on_quit
        self.vars = {}

        win = tk.Toplevel(root)
        self.win = win
        win.title("Local Dictation")
        win.resizable(False, False)
        win.configure(bg=SURFACE)          # the fix for the light-grey body

        icon = os.path.join(HERE, "dictation.ico")
        if os.path.exists(icon):
            try:
                win.iconbitmap(icon)
            except Exception:
                pass
        if THEMED:
            try:
                sv_ttk.set_theme("dark")
                _force_dark_containers()
            except Exception:
                pass

        outer = ttk.Frame(win, padding=(22, 18, 22, 18))
        outer.pack(fill="both", expand=True)

        self._build_header(outer)

        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True, pady=(16, 0))
        for build, title in ((self._build_speech, "  Speech  "),
                             (self._build_words, "  My words  "),
                             (self._build_system, "  System  ")):
            tab = ttk.Frame(nb, padding=(4, 14, 4, 4))
            nb.add(tab, text=title)
            build(tab)

        self.note = ttk.Label(outer, text="", foreground=MUTED,
                              wraplength=440, justify="left",
                              font=("Segoe UI", 9))
        self.note.pack(fill="x", pady=(14, 0))

        self._build_buttons(outer)

        win.update_idletasks()
        self._centre(win)
        # Rounded corners, Mica and a dark title bar come from the compositor,
        # and only apply once the window actually exists.
        if theme is not None:
            try:
                theme.modernise(win, "window")
            except Exception:
                pass
        win.lift()
        win.focus_force()

    # -- pieces -----------------------------------------------------------

    def _build_header(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text="Local Dictation",
                  font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Label(row, text="local  ·  unlimited", foreground=LIVE,
                  font=("Segoe UI Semibold", 9)).pack(side="right", pady=(6, 0))
        ttk.Label(parent, text="Nothing you say leaves this machine.",
                  foreground=MUTED,
                  font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

    def _card(self, parent, title):
        """A Windows 11 surface with a heading above it."""
        ttk.Label(parent, text=title.upper(), foreground=MUTED,
                  font=("Segoe UI Semibold", 8)).pack(anchor="w",
                                                      pady=(0, 6))
        card = ttk.Frame(parent, style="Card.TFrame", padding=(16, 14))
        card.pack(fill="x", pady=(0, 16))
        return card

    def _slider(self, card, key, label, lo, hi, fmt, hint_for):
        row = ttk.Frame(card)
        row.pack(fill="x")
        ttk.Label(row, text=label).pack(side="left")
        value = ttk.Label(row, text="", foreground=ACCENT,
                          font=("Cascadia Mono", 10))
        value.pack(side="right")
        var = tk.DoubleVar(value=self.settings.get(key, lo))
        self.vars[key] = var
        hint = ttk.Label(card, text="", foreground=MUTED, wraplength=400,
                         justify="left", font=("Segoe UI", 8))

        def changed(_v=None):
            v = float(var.get())
            value.config(text=fmt % v)
            hint.config(text=hint_for(v))

        ttk.Scale(card, from_=lo, to=hi, orient="horizontal", variable=var,
                  command=changed).pack(fill="x", pady=(6, 4))
        hint.pack(anchor="w")
        changed()

    def _switch(self, parent, key, label, default=True):
        var = tk.BooleanVar(value=self.settings.get(key, default))
        self.vars[key] = var
        ttk.Checkbutton(parent, text=label, variable=var,
                        style="Switch.TCheckbutton").pack(anchor="w", pady=3)

    # -- tabs -------------------------------------------------------------

    def _build_speech(self, parent):
        card = self._card(parent, "Cleanup")
        self.vars["polish"] = tk.StringVar(
            value=self.settings.get("polish", "fast"))
        for value, label in (
                ("off", "Off      type exactly what I said"),
                ("fast", "Fast     remove um, uh, stutters   ·  free"),
                ("llm", "Full     local model tidies each phrase   ·  ~2s")):
            ttk.Radiobutton(card, text=label, value=value,
                            variable=self.vars["polish"]).pack(anchor="w",
                                                               pady=2)

        card = self._card(parent, "Speed")
        self._switch(card, "stream", "Type as I pause, not only when I stop")
        self._slider(
            card, "pause_s", "Pause that ends a phrase", 0.3, 1.5, "%.1fs",
            lambda v: ("Snappy, but it will cut you off mid-thought."
                       if v <= 0.5 else
                       "Balanced. Matches an ordinary thinking pause."
                       if v <= 0.9 else
                       "Patient. You lose most of the live typing."))
        self._switch(card, "noise_gate",
                     "Ignore voices quieter than mine (a TV, the next room)")
        self._slider(
            card, "vad_threshold", "Ignore background noise", 0.3, 0.9,
            "%.2f",
            lambda v: ("Sensitive. Picks up the room along with you."
                       if v <= 0.45 else
                       "Balanced. Ignores most background conversation."
                       if v <= 0.7 else
                       "Strict. Only close speech; may clip a soft start."))

    def _build_words(self, parent):
        card = self._card(parent, "Your vocabulary")
        ttk.Label(card, wraplength=400, justify="left", foreground=MUTED,
                  font=("Segoe UI", 9),
                  text=("Terms in vocabulary.txt are fed to the model as a "
                        "bias, and near-misses are snapped back to them. Add "
                        "whatever it keeps getting wrong.")
                  ).pack(anchor="w", pady=(0, 10))
        self._switch(card, "vocab", "Use my vocabulary")
        self._switch(card, "fuzzy", "Fix near-misses of my terms")
        self._switch(card, "commands",
                     'Spoken punctuation and "scratch that"')
        ttk.Button(card, text="Edit my words",
                   command=self._open_vocab).pack(anchor="w", pady=(12, 0))

    def _build_system(self, parent):
        card = self._card(parent, "Startup")
        self._switch(card, "run_at_login", "Start when Windows starts", False)
        self._switch(card, "overlay", "Show the pill while talking")

        card = self._card(parent, "Hotkey")
        self.vars["hotkey"] = tk.StringVar(
            value=self.settings.get("hotkey", "f9"))
        ttk.Combobox(card, textvariable=self.vars["hotkey"], width=26,
                     values=["f9", "f8", "f4", "ctrl+alt+space",
                             "ctrl+shift+d", "alt+`"]).pack(anchor="w")
        ttk.Label(card, wraplength=400, justify="left", foreground=MUTED,
                  font=("Segoe UI", 8),
                  text=("F9 collides with Immersive Reader in Edge. It is "
                        "suppressed so it no longer reaches Edge, but a "
                        "combination avoids the clash entirely.")
                  ).pack(anchor="w", pady=(6, 0))

        card = self._card(parent, "Model")
        self.vars["model"] = tk.StringVar(
            value=self.settings.get("model", "small.en"))
        ttk.Combobox(card, textvariable=self.vars["model"], width=26,
                     values=["small.en", "base.en", "distil-medium.en",
                             "medium.en"]).pack(anchor="w")
        ttk.Label(card, wraplength=400, justify="left", foreground=MUTED,
                  font=("Segoe UI", 8),
                  text=("small.en is the right point on this GPU. base.en "
                        "starts dropping words at your 150 wpm.")
                  ).pack(anchor="w", pady=(6, 0))

    def _build_buttons(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(16, 0))
        ttk.Button(row, text="Quit app", command=self._quit,
                   width=11).pack(side="left")
        ttk.Button(row, text="Close", command=self._close,
                   width=11).pack(side="right")
        save = ttk.Button(row, text="Save", command=self._save, width=13)
        save.pack(side="right", padx=(0, 8))
        if THEMED:
            save.configure(style="Accent.TButton")

    # -- helpers ----------------------------------------------------------

    def _centre(self, win):
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry("+%d+%d" % (max(0, (sw - w) // 2),
                                     max(30, (sh - h) // 3)))
        except Exception:
            pass

    def _open_vocab(self):
        try:
            subprocess.Popen(["notepad.exe",
                              os.path.join(HERE, "vocabulary.txt")])
        except Exception:
            self.note.config(text="Could not open vocabulary.txt")

    # -- actions ----------------------------------------------------------

    def collect(self):
        out = dict(self.settings)
        for key, var in self.vars.items():
            out[key] = var.get()
        return out

    def _save(self):
        new = self.collect()
        changed = [k for k in new if new[k] != self.settings.get(k)]
        messages = self.on_apply(new, changed) or []
        self.settings = new
        self.note.config(text="   ".join(messages) if messages else "Saved.",
                         foreground=WARN if messages else MUTED)

    def _close(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _quit(self):
        self._close()
        if self.on_quit:
            self.on_quit()
