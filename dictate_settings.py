"""dictate_settings - modern settings window for Local Dictation.

Two key design constraints preserved:
1. Normal Toplevel window: Takes focus so user can type/interact comfortably.
2. Themed with sv-ttk (Sun Valley Windows 11 controls) with clean card-based layout.
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

HERE = os.path.dirname(os.path.abspath(__file__))

ACCENT = "#38bdf8"
ACCENT_GREEN = "#34d399"
MUTED = "#94a3b8"
WARN = "#fbbf24"


class SettingsWindow:
    """A normal window. Takes focus, unlike the pill, and that is intended."""

    def __init__(self, root, settings, on_apply, on_quit=None):
        self.settings = dict(settings)
        self.on_apply = on_apply
        self.on_quit = on_quit
        self.vars = {}

        win = tk.Toplevel(root)
        self.win = win
        win.title("Local Dictation Settings")
        win.resizable(False, False)
        icon = os.path.join(HERE, "dictation.ico")
        if os.path.exists(icon):
            try:
                win.iconbitmap(icon)
            except Exception:
                pass
        if THEMED:
            try:
                sv_ttk.set_theme("dark")
            except Exception:
                pass

        pad = ttk.Frame(win, padding=(24, 20, 24, 18))
        pad.grid(sticky="nsew")

        # Header with status badges
        header_frame = ttk.Frame(pad)
        header_frame.grid(sticky="ew", pady=(0, 10))

        title_row = ttk.Frame(header_frame)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Local Dictation",
                  font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Label(title_row, text="  ⚡ RTX 3050 CUDA · Offline",
                  foreground=ACCENT_GREEN,
                  font=("Segoe UI Semibold", 9)).pack(side="left", padx=(8, 0), pady=(4, 0))

        ttk.Label(header_frame,
                  text="Permanently local speech-to-text. No account, no cloud quota, 0 audio leaves this PC.",
                  foreground=MUTED,
                  font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

        # Notebook tabs
        nb = ttk.Notebook(pad)
        nb.grid(sticky="ew", pady=(8, 0))
        speech = ttk.Frame(nb, padding=(18, 16))
        words = ttk.Frame(nb, padding=(18, 16))
        system = ttk.Frame(nb, padding=(18, 16))
        nb.add(speech, text="  🎙️ Speech & VAD  ")
        nb.add(words, text="  📚 My Words  ")
        nb.add(system, text="  ⚙️ System & Keys  ")

        self._build_speech(speech)
        self._build_words(words)
        self._build_system(system)

        # Status notification line
        self.note = ttk.Label(pad, text="", foreground=WARN,
                              wraplength=460, justify="left",
                              font=("Segoe UI", 9))
        self.note.grid(sticky="w", pady=(12, 0))

        # Action buttons
        btns = ttk.Frame(pad)
        btns.grid(sticky="ew", pady=(12, 0))
        ttk.Button(btns, text="Quit App", command=self._quit,
                   width=11).pack(side="left")
        
        ttk.Button(btns, text="Close", command=self._close,
                   width=10).pack(side="right")
        save = ttk.Button(btns, text="Save Settings", command=self._save, width=14)
        save.pack(side="right", padx=(0, 8))
        if THEMED:
            save.configure(style="Accent.TButton")

        win.update_idletasks()
        self._centre(win)
        win.lift()
        win.focus_force()

    # -- tabs -------------------------------------------------------------

    def _build_speech(self, parent):
        # Card 1: Cleanup Level
        card1 = ttk.LabelFrame(parent, text=" Text Polish & Cleanup ", padding=(14, 10))
        card1.pack(fill="x", pady=(0, 10))

        self.vars["polish"] = tk.StringVar(
            value=self.settings.get("polish", "fast"))
        for value, label in (
                ("off", "Off  —  Type verbatim as transcribed"),
                ("fast", "Fast  —  Remove stutters, filler words (um, uh)  [Instant / Free]"),
                ("llm", "Full  —  Local LLM pass to tidy grammar and structure  [~2s]")):
            ttk.Radiobutton(card1, text=label, value=value,
                            variable=self.vars["polish"]).pack(anchor="w", pady=2)

        # Card 2: Speed & Pause Timing
        card2 = ttk.LabelFrame(parent, text=" Streaming & Pause Sensitivity ", padding=(14, 10))
        card2.pack(fill="x", pady=(0, 4))

        self.vars["stream"] = tk.BooleanVar(
            value=self.settings.get("stream", True))
        ttk.Checkbutton(card2, text="Live Streaming — Type at natural pauses while speaking",
                        variable=self.vars["stream"]).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(card2)
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Pause duration that ends a phrase:").pack(side="left")
        self.pause_label = ttk.Label(row, text="", foreground=ACCENT,
                                     font=("Segoe UI Semibold", 9))
        self.pause_label.pack(side="right")
        
        self.vars["pause_s"] = tk.DoubleVar(
            value=self.settings.get("pause_s", 0.7))
        ttk.Scale(card2, from_=0.3, to=1.5, orient="horizontal",
                  variable=self.vars["pause_s"], length=420,
                  command=lambda _v: self._pause_changed()).pack(fill="x", pady=2)
        self.pause_hint = ttk.Label(card2, text="", foreground=MUTED,
                                    wraplength=420, justify="left",
                                    font=("Segoe UI", 8))
        self.pause_hint.pack(anchor="w")
        self._pause_changed()

        row2 = ttk.Frame(card2)
        row2.pack(fill="x", pady=(10, 0))
        ttk.Label(row2, text="Silero VAD background noise cutoff:").pack(side="left")
        self.vad_label = ttk.Label(row2, text="", foreground=ACCENT,
                                   font=("Segoe UI Semibold", 9))
        self.vad_label.pack(side="right")
        
        self.vars["vad_threshold"] = tk.DoubleVar(
            value=self.settings.get("vad_threshold", 0.6))
        ttk.Scale(card2, from_=0.3, to=0.9, orient="horizontal",
                  variable=self.vars["vad_threshold"], length=420,
                  command=lambda _v: self._vad_changed()).pack(fill="x", pady=2)
        self.vad_hint = ttk.Label(card2, text="", foreground=MUTED,
                                  wraplength=420, justify="left",
                                  font=("Segoe UI", 8))
        self.vad_hint.pack(anchor="w")
        self._vad_changed()

    def _build_words(self, parent):
        card = ttk.LabelFrame(parent, text=" Custom Vocabulary Biasing & Rules ", padding=(14, 10))
        card.pack(fill="x", pady=(0, 10))

        ttk.Label(card, wraplength=420, justify="left", foreground=MUTED,
                  font=("Segoe UI", 9),
                  text=("Terms in vocabulary.txt are biased in Whisper decoding, "
                        "and phonetic near-misses (e.g. Arbeitszugnis -> Arbeitszeugnis) "
                        "are snapped cleanly without false rewrites.")
                  ).pack(anchor="w", pady=(0, 10))

        self.vars["vocab"] = tk.BooleanVar(
            value=self.settings.get("vocab", True))
        ttk.Checkbutton(card, text="Enable 3-Tier Custom Vocabulary Biasing",
                        variable=self.vars["vocab"]).pack(anchor="w", pady=3)

        self.vars["fuzzy"] = tk.BooleanVar(
            value=self.settings.get("fuzzy", True))
        ttk.Checkbutton(card, text="Auto-snap phonetic near-misses (Levenshtein guard 0.2)",
                        variable=self.vars["fuzzy"]).pack(anchor="w", pady=3)

        self.vars["commands"] = tk.BooleanVar(
            value=self.settings.get("commands", True))
        ttk.Checkbutton(card,
                        text='Spoken punctuation & commands ("full stop", "scratch that", "cap that")',
                        variable=self.vars["commands"]).pack(anchor="w", pady=3)

        btn_row = ttk.Frame(card)
        btn_row.pack(fill="x", pady=(12, 4))
        ttk.Button(btn_row, text="📝 Edit vocabulary.txt",
                   command=self._open_vocab).pack(side="left")
        ttk.Button(btn_row, text="📜 View transcript.log",
                   command=self._open_log).pack(side="left", padx=(8, 0))

    def _build_system(self, parent):
        card1 = ttk.LabelFrame(parent, text=" Startup & Floating Pill ", padding=(14, 10))
        card1.pack(fill="x", pady=(0, 10))

        self.vars["run_at_login"] = tk.BooleanVar(
            value=self.settings.get("run_at_login", False))
        ttk.Checkbutton(card1, text="Start Local Dictation automatically with Windows",
                        variable=self.vars["run_at_login"]).pack(anchor="w", pady=2)

        self.vars["overlay"] = tk.BooleanVar(
            value=self.settings.get("overlay", True))
        ttk.Checkbutton(card1, text="Show always-on-top mic indicator pill while talking",
                        variable=self.vars["overlay"]).pack(anchor="w", pady=2)

        card2 = ttk.LabelFrame(parent, text=" Global Hotkey & Model Architecture ", padding=(14, 10))
        card2.pack(fill="x", pady=(0, 4))

        row1 = ttk.Frame(card2)
        row1.pack(fill="x", pady=(2, 6))
        ttk.Label(row1, text="Activation Hotkey:").pack(side="left")
        self.vars["hotkey"] = tk.StringVar(
            value=self.settings.get("hotkey", "f9"))
        combo = ttk.Combobox(row1, textvariable=self.vars["hotkey"],
                             width=18, values=[
                                 "f9", "f8", "f4", "ctrl+alt+space",
                                 "ctrl+shift+d", "alt+`", "ctrl+alt+d"])
        combo.pack(side="right")

        row2 = ttk.Frame(card2)
        row2.pack(fill="x", pady=(6, 2))
        ttk.Label(row2, text="Whisper Model Engine:").pack(side="left")
        self.vars["model"] = tk.StringVar(
            value=self.settings.get("model", "small.en"))
        ttk.Combobox(row2, textvariable=self.vars["model"], width=18,
                     values=["small.en", "base.en", "distil-medium.en",
                             "medium.en"]).pack(side="right")
        
        ttk.Label(card2, wraplength=420, justify="left", foreground=MUTED,
                  font=("Segoe UI", 8),
                  text=("small.en (int8_float16) is verified optimal on this RTX 3050 GPU: "
                        "11–16x realtime speed and zero dropped words at 150+ WPM.")
                  ).pack(anchor="w", pady=(4, 0))

    # -- reactions --------------------------------------------------------

    def _pause_changed(self):
        v = float(self.vars["pause_s"].get())
        self.pause_label.config(text="%.1fs" % v)
        if v <= 0.5:
            hint = ("Snappy, but may cut off mid-sentence. "
                    "Measured splitting fast real speech at 0.4s.")
        elif v <= 0.9:
            hint = "Balanced (Recommended). Matches an ordinary thinking pause."
        else:
            hint = ("Patient. Wait longer before emitting text.")
        self.pause_hint.config(text=hint)

    def _vad_changed(self):
        v = float(self.vars["vad_threshold"].get())
        self.vad_label.config(text="%.2f" % v)
        if v <= 0.45:
            hint = "Sensitive. Captures quiet whispers, but may pick up ambient room sound."
        elif v <= 0.7:
            hint = "Balanced (Recommended). Ignores typing and background conversations."
        else:
            hint = "Strict. Only close-mic clear speech. May clip soft starts."
        self.vad_hint.config(text=hint)

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

    def _open_log(self):
        try:
            subprocess.Popen(["notepad.exe",
                              os.path.join(HERE, "transcript.log")])
        except Exception:
            self.note.config(text="Could not open transcript.log")

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
        self.note.config(text="   ".join(messages) if messages else "Settings saved successfully.",
                         foreground=WARN if messages else ACCENT_GREEN)

    def _close(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _quit(self):
        """Shut the whole tool down."""
        self._close()
        if self.on_quit:
            self.on_quit()
