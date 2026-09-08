#!/usr/bin/env python3
"""
dictate.py - local, unlimited, offline dictation.

    Dictate.cmd              start it
    Dictate-Everywhere.cmd   same, but also reaches administrator windows

HOW YOU USE IT

    F9                    start talking, press again to stop
    tray icon             right-click for Settings, Edit my words, Quit
    double-click tray     Settings

The tray icon is the app. It sits in the notification area, changes colour
while it works, and is where you quit from.

Nothing else is on screen while you are not dictating. When you press F9 a
small pill appears with a live microphone meter, so you can see it is hearing
you; it disappears again when you stop. The pill never takes keyboard focus,
so your text goes to the window you were already in. Drag it to move it.

SPEAKING

    "full stop"  "new line"  "new paragraph"     punctuation
    "scratch that"                               undo the last thing typed
    "cap that"                                   capitalise the previous word
    "literally <word>"                           type a command word instead
                                                 of running it

Ordinary uses are left alone: "the car came to a full stop" types as written.

SETTINGS

Right-click the tray icon. Cleanup level, how long a pause ends a phrase,
whether to start with Windows, and your own vocabulary. Everything is stored
in settings.json next to this file. Environment variables (DICTATE_POLISH,
DICTATE_PAUSE and the rest) still override it, which is what the tests use.

Everything stays on this machine. No account, no quota, no limit.
"""

import os
import sys
import time
import queue
import datetime
import threading

import numpy as np
import sounddevice as sd
import keyboard

# Running detached from a console (pythonw, or launched from Explorer) means
# there is nowhere for print() to go, and on some builds writing to a missing
# stdout raises. Send everything to a file instead, so the app is still
# diagnosable when it has no window at all.
def _redirect_output_if_headless():
    # Only when this file is the program being run. Importing it - which the
    # tests do - must never hijack the importer's stdout; that silently ate
    # the end-to-end test's own output the first time.
    if __name__ != "__main__":
        return None
    try:
        import ctypes
        if ctypes.windll.kernel32.GetConsoleWindow():
            return None                      # a real console exists
    except Exception:
        pass
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dictate.log")
        stream = open(path, "a", encoding="utf-8", buffering=1)
        stream.write("\n=== started %s ===\n"
                     % __import__("datetime").datetime.now())
        sys.stdout = stream
        sys.stderr = stream
        return path
    except Exception:
        return None


_LOG_FILE = _redirect_output_if_headless()

import dictate_core as core
core.enable_cuda_dlls()          # must precede the faster_whisper import
import faster_whisper            # noqa: F401  imported here so the CUDA DLL
                                 # ordering above is proven at startup, not on
                                 # the first thing you dictate
import dictate_stream
import dictate_overlay
import dictate_polish

import dictate_config
import dictate_settings
import dictate_tray

# One settings file, not eleven environment variables. Environment variables
# still win when set, so every test and tuning note stays valid.
CFG = dictate_config.load()

MODEL_NAME = CFG["model"]
USE_VOCAB = CFG["vocab"]
USE_COMMANDS = CFG["commands"]
USE_STREAM = CFG["stream"]
USE_FUZZY = CFG["fuzzy"]
POLISH = CFG["polish"]
PAUSE_S = CFG["pause_s"]
VAD_THRESHOLD = CFG["vad_threshold"]
USE_GATE = CFG["noise_gate"]

# Learns how loud you are, so a television can be told from you. Seeded from
# the last run, so the gate works on the first phrase rather than needing to
# hear you four times again every launch.
_voice = core.VoiceLevel([CFG["voice_level"]] * core.GATE_MIN_SAMPLES
                         if CFG.get("voice_level") else [])
USE_OVERLAY = CFG["overlay"]
HIDE_CONSOLE = CFG["hide_console"]
HOTKEY = CFG["hotkey"]
DEVICE_PREF = CFG["device"]
os.environ.setdefault("DICTATE_DEVICE", DEVICE_PREF)

MAX_BACKSPACE = 400
SAMPLE_RATE = 16000
MIN_SECONDS = 0.4

# A recording nobody stops never ends on its own. The microphone callback keeps
# appending to an unbounded queue at 64 KB/s, so a forgotten F9 costs about
# 230 MB an hour and ends in a single transcribe over the whole thing.
#
# Five minutes is roughly 600 words at the 120 wpm this was measured at, so no
# real dictation reaches it, and what was said still gets typed rather than
# thrown away.
MAX_SECONDS = 300.0
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "transcript.log")

_q = queue.Queue()
_rec = threading.Event()
_last_typed = ""     # what emit() last sent, so "scratch that" can undo it
_level = 0.0         # live microphone loudness, drives the pill meter
_toggle = threading.Event()   # set by the hotkey; watched by the hotkey loop
_stream = None                # the live audio stream, so resume can replace it
_model = None                 # the transcriber, so resume can rebuild it
_target_hwnd = None  # the window that had focus when recording started
_target_title = ""


class NullUI:
    """Stands in for the overlay when it is switched off.

    Lets the audio path report state unconditionally instead of guarding every
    call site with "if the overlay exists".
    """

    def set_state(self, state, detail=""):
        pass

    def stop(self):
        pass


_ui = NullUI()
_tray = None


def set_state(state, detail=""):
    """Fan one state change out to both indicators."""
    _ui.set_state(state, detail)
    if _tray:
        _tray.set_state(state)


def apply_settings(new, changed):
    """Save settings and apply what can change without a restart.

    Returns a list of messages for the settings window. Being explicit about
    which changes need a restart matters: silently ignoring one would look
    like the setting does not work.
    """
    global POLISH, PAUSE_S, USE_FUZZY, USE_COMMANDS, CFG
    messages = []

    if "run_at_login" in changed:
        ok, msg = dictate_config.set_startup(new["run_at_login"])
        messages.append(msg if ok else "Startup: " + msg)

    # Live, because nothing is holding onto them.
    POLISH = new["polish"]
    PAUSE_S = float(new["pause_s"])
    USE_FUZZY = bool(new["fuzzy"])
    globals()["VAD_THRESHOLD"] = float(new.get("vad_threshold", 0.6))
    USE_COMMANDS = bool(new["commands"])
    globals()["USE_GATE"] = bool(new.get("noise_gate", True))

    if POLISH == "llm":
        ok, _names = dictate_polish.llm_available()
        if not ok:
            POLISH = "fast"
            new["polish"] = "fast"
            messages.append("%s is not running, using Fast cleanup."
                            % dictate_polish.LLM_MODEL)
        else:
            threading.Thread(target=dictate_polish.preload,
                             daemon=True).start()

    restart = sorted({k for k in changed if dictate_config.needs_restart(k)})
    if restart:
        messages.append("Restart to apply: " + ", ".join(restart) + ".")

    try:
        dictate_config.save(new)
        CFG = dict(new)
    except Exception as e:
        messages.append("Could not save settings: %s" % e)
    return messages


def _gate_phrase(audio, look=None):
    """True when this phrase is the room rather than you.

    Shared by both the batch and streaming paths, so they cannot drift apart -
    the last two guards were added to one path only and the other kept the bug.
    """
    loudness = (look.rms if look is not None
                else core.speech_rms(audio, threshold=VAD_THRESHOLD))
    background, floor = _voice.is_background(loudness)
    if background:
        print("  too quiet to be you (%.4f, floor %.4f), ignored as room noise"
              % (loudness, floor))
        # Say so on screen. Dropping a phrase with the reason only in a log
        # file is how this locked the user out unnoticed.
        set_state("listening" if _rec.is_set() else "typed",
                  "ignored, too quiet")
        return True
    if _voice.last_reason:
        print("  noise filter: %s" % _voice.last_reason)
        set_state("listening" if _rec.is_set() else "typed",
                  "noise filter off")
        _voice.last_reason = ""
    _voice.learn(loudness)
    _remember_voice_level()
    return False


def _remember_voice_level():
    """Save the learned level, but only when it has actually moved.

    Writing settings.json on every phrase would be a lot of disk for a number
    that barely changes.
    """
    level = _voice.level
    if not level:
        return
    stored = CFG.get("voice_level") or 0.0
    if stored and abs(level - stored) / max(stored, 1e-9) < 0.10:
        return
    try:
        CFG["voice_level"] = round(level, 5)
        dictate_config.save(CFG)
    except Exception:
        pass


def key_finder():
    print("\n  KEY FINDER")
    print("  Press the key you want to use. ESC to finish.\n")
    while True:
        ev = keyboard.read_event()
        if ev.event_type != keyboard.KEY_DOWN:
            continue
        if ev.name == "esc":
            print("\n  Set it with:   set DICTATE_KEY=<name>\n")
            return
        print("    name: %-16s scan code: %s" % (repr(ev.name), ev.scan_code))


def log_line(text, held, took, speech_s=0.0):
    """Append-only record. Nothing spoken is ever lost.

    The words-per-minute figure is the point of this line. It is the only
    place real speaking rate gets measured on real speech - everything else in
    this project was calibrated against a synthetic voice. Streamed phrases
    used to log no duration at all, which threw that measurement away.
    """
    # A test must never write into the real transcript. test_endtoend pushes a
    # synthetic corpus through the real pipeline, and every phrase it spoke
    # landed here as though Tejas had said it. That is not just clutter: this
    # file is the only measurement of his real speaking rate on real speech, so
    # synthetic lines corrupt the one number it exists to produce. Same fault as
    # the run that once saved a test's audio level as his voice level.
    if os.environ.get("DICTATE_TESTING") == "1":
        return
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            words = len(text.split())
            span = speech_s or held
            if span > 0.3 and words:
                when = "%.1fs, %d words, %.0f wpm" % (span, words,
                                                      words / (span / 60.0))
                if took:
                    when += ", %.1fs to transcribe" % took
            elif held:
                when = "%.1fs spoken" % held
            else:
                when = "streamed"
            f.write("[%s] (%s) %s\n" % (stamp, when, text))
    except Exception:
        pass


def on_audio(indata, frames, t, status):
    """Audio callback. Also tracks how loud it is, so the pill can show you
    that the microphone is actually being heard.

    Without this, a muted mic or a wrong input device looks identical to
    working: the pill says "listening" and nothing ever appears. Knowing the
    difference is the whole point of having an indicator.
    """
    global _level
    if _rec.is_set():
        _q.put(indata.copy())
        rms = float(np.sqrt(np.mean(np.square(indata))))
        _level = max(rms, _level * 0.80)      # decay, so the meter is smooth


def drain():
    parts = []
    while not _q.empty():
        parts.append(_q.get())
    if not parts:
        return None
    return np.concatenate(parts, axis=0).flatten().astype(np.float32)


def emit(actions):
    """Execute the plan against the focused window.

    Backspaces are capped. A runaway delete caused by a misheard "scratch
    that" would be far worse than leaving a few stray characters behind.
    """
    for kind, value in actions:
        if kind == "backspace":
            n = min(int(value), MAX_BACKSPACE)
            if n < int(value):
                print("  refused to erase %d chars, capped at %d" % (value, n))
            for _ in range(n):
                keyboard.send("backspace")
        elif kind == "type":
            tail = "" if value.endswith("\n") else " "
            keyboard.write(value + tail, delay=0.005)


def deliver(text, rules, terms=(), held=0.0, took=0.0, settle=0.0,
            speech_s=0.0):
    """Corrections, then commands, then keystrokes. Shared by both modes.

    Batch and streaming differ only in when they have text; everything that
    happens to that text afterwards is identical, so it lives here once.
    """
    global _last_typed
    if not text:
        return

    fired = []
    if rules:
        text, fired = core.apply_corrections(text, rules)
    if terms and USE_FUZZY:
        # explicit rules first, then snap whatever variant they did not name
        text, snapped = core.fuzzy_snap(text, terms)
        fired += snapped
    if POLISH != "off":
        text, pnotes = dictate_polish.polish(text, POLISH, terms)
        if pnotes:
            print("  polish : %s" % ", ".join(pnotes))
    if USE_COMMANDS:
        actions, notes = core.plan(text, _last_typed)
    else:
        actions, notes = ([("type", text)] if text else []), []

    if fired:
        print("  fixed  : %s" % ", ".join("%s -> %s" % f for f in fired))
    if notes:
        print("  command: %s" % ", ".join(notes))
    if not actions:
        print("  > (nothing to type)")
        return

    typed = "".join(v for k, v in actions if k == "type")
    print("  > %s" % typed.replace("\n", "\\n"))
    log_line(text, held, took, speech_s)
    if settle:
        time.sleep(settle)                 # let the hotkey fully release

    # Aim at the window dictation started in. Focus drifts during a long
    # utterance - there is time to glance elsewhere - and text typed into the
    # wrong window is worse than no text at all.
    if _target_hwnd:
        here, title = core.foreground_window()
        if here != _target_hwnd:
            if core.focus_window(_target_hwnd):
                print("  aimed  : back at %r" % (_target_title[:40] or "?"))
                time.sleep(0.08)           # let the switch settle
            else:
                print("  WARNING: focus is on %r, could not return to %r; "
                      "typing here instead"
                      % (title[:28], _target_title[:28]))
    emit(actions)
    if typed:
        _last_typed = typed + ("" if typed.endswith("\n") else " ")
        _ui.set_state("typed", typed.replace("\n", " ").strip())


def transcribe_and_type(model, audio, held, prompt=None, rules=(), terms=()):
    if audio is None or held < MIN_SECONDS:
        print("  too short, ignored")
        return
    # Held long enough is not the same as actually said something. Real usage
    # produced "Thank you for watching." from half a second of near-silence,
    # which would have been typed into whatever window was focused.
    # One voice-activity pass, reused by the gate. Running it twice cost
    # 170 ms per utterance computing the same answer.
    look = core.analyse(audio, threshold=VAD_THRESHOLD)
    speaking, speech_s = look.has_speech(), look.speech_seconds
    if speaking and USE_GATE and _gate_phrase(audio, look):
        set_state("idle")
        return
    if not speaking:
        print("  no speech in that (%.1fs of sound), ignored" % speech_s)
        set_state("idle")
        return

    print("  transcribing... ", end="", flush=True)
    set_state("thinking")
    t = time.time()
    try:
        text = model.transcribe(audio, prompt=prompt)
    except Exception as e:
        print("error (%s)" % type(e).__name__)
        try:
            print("  attempting model recovery after sleep/error...")
            model.load(force_cpu=True)
            text = model.transcribe(audio, prompt=prompt)
        except Exception as e2:
            print("  recovery failed (%s)" % type(e2).__name__)
            set_state("idle")
            return
    took = time.time() - t
    if not text:
        print("nothing heard")
        return
    if core.looks_hallucinated(text, speech_s):
        print("%.1fs\n  ignored %r, model filler on a short clip\n"
              % (took, text))
        set_state("idle")
        return
    print("%.1fs" % took)
    deliver(text, rules, terms, held=held, took=took, settle=0.12)
    print("")


def stream_worker(session, stop_evt, rules, terms=()):
    """Feed captured audio to the session and type whatever it says is safe.

    Runs on its own thread so the hotkey stays responsive while a transcribe
    is in flight. The session decides what is safe to emit; this loop only
    moves audio in and keystrokes out.
    """
    time.sleep(0.12)                       # let the hotkey fully release
    while not stop_evt.is_set():
        session.feed(drain())
        try:
            for text in session.poll():
                deliver(text, rules, terms, held=session.last_phrase_s,
                        speech_s=session.last_speech_s)
        except Exception as e:
            print("  stream error (%s), still recording" % type(e).__name__)
        time.sleep(0.15)

    session.feed(drain())                  # whatever arrived while stopping
    try:
        for text in session.finish():
            deliver(text, rules, terms, held=session.last_phrase_s,
                    speech_s=session.last_speech_s)
    except Exception as e:
        print("  stream error on finish (%s)" % type(e).__name__)



def main():
    if "--keys" in sys.argv:
        key_finder()
        return

    # A second copy is never harmless: both grab the global hotkey and both
    # type into the same window. Two instances once made the tool look
    # completely broken when nothing was wrong with it.
    if not core.claim_single_instance():
        msg = ("Local Dictation is already running.\n\n"
               "Look for its icon in the notification area, near the clock.\n"
               "Right-click it for settings, or to quit.")
        print("\n  " + msg.replace("\n", "\n  ") + "\n")
        # Detached there is no console to read, so say it in a way that is
        # actually visible. Silently doing nothing would look like a crash.
        if _LOG_FILE:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, msg, "Local Dictation", 0x40)      # MB_ICONINFORMATION
            except Exception:
                pass
        return

    terms = core.load_vocabulary() if USE_VOCAB else []
    rules = core.load_corrections() if USE_VOCAB else []
    prompt, used, dropped = core.build_prompt(terms)
    print("=" * 60)
    print("  LOCAL DICTATION")
    print("=" * 60)
    print("  model  : %s" % MODEL_NAME)
    if USE_VOCAB:
        print(core.vocabulary_report(terms, used, dropped))
        if rules:
            print("  fixes  : %d correction rules" % len(rules))
        print("  snap   : %s" % ("near-misses of your terms are corrected"
                                 if USE_FUZZY else "off (DICTATE_FUZZY=0)"))
    else:
        print("  vocab  : off (DICTATE_VOCAB=0)")
    print("  hotkey : %s  press to start, press again to stop"
          % HOTKEY.upper())
    if USE_STREAM:
        print("  stream : on, text appears at pauses of %.1fs" % PAUSE_S)
    else:
        print("  stream : off, text appears when you stop "
              "(set DICTATE_STREAM=1 to try it)")
    print("  pill   : %s" % ("appears only while talking. drag to move"
                            if USE_OVERLAY else "off"))
    print("  quit   : right-click the tray icon, or ESC")
    if core.is_elevated():
        print("  reach  : everywhere, including admin windows")
    else:
        print("  reach  : every normal window. NOT admin windows "
              "(Task Manager, admin terminals)")
        print("           run Dictate-Everywhere.cmd if you need those too")
    print("  polish : %s" % {
        "off": "off, typed exactly as heard",
        "fast": "fast, filler and stutter removal (free)",
        "llm": "local model cleanup (~2s per phrase)",
    }.get(POLISH, POLISH))
    try:
        mic = sd.query_devices(kind="input")["name"]
    except Exception:
        mic = "unknown"
    print("  mic    : %s" % mic)
    if USE_GATE:
        known = CFG.get("voice_level") or 0
        print("  room   : quieter voices ignored%s"
              % ("" if known else " once it has learnt your level"))
    battery, pct = core.on_battery()
    if battery:
        print("  power  : on battery%s. The GPU still runs but Windows clocks "
              "it down, so expect it to be slower"
              % ("" if pct is None else " (%d%%)" % pct))
    # Measured: 101 bytes per utterance, so a peak day is about 34 KB and a
    # year of heavy use is ~12 MB. Storage is not the risk it feels like, but
    # nothing should append forever either. Roughly a year of history is kept.
    core.trim_log(LOG_PATH, max_bytes=12_000_000, keep_bytes=8_000_000)
    core.trim_log(os.path.join(HERE, "dictate.log"),
                  max_bytes=1_000_000, keep_bytes=200_000)
    print("  log    : transcript.log")
    print("  config : settings.json, or right-click the tray icon")
    print("-" * 60)
    print("  loading model...")

    if POLISH == "llm":
        ok, names = dictate_polish.llm_available()
        if not ok:
            print("  polish : %s not reachable, using fast rules instead"
                  % dictate_polish.LLM_MODEL)
            globals()["POLISH"] = "fast"
        else:
            print("  polish : preloading %s so the first phrase is not the "
                  "slow one..." % dictate_polish.LLM_MODEL)
            loaded, secs = dictate_polish.preload()
            print("  polish : %s in %.1fs"
                  % ("resident" if loaded else "preload failed", secs))

    t0 = time.time()
    global _model
    _model = core.Transcriber(MODEL_NAME,
                              on_event=lambda m: print("  " + m)).load()
    model = _model
    print("  ready in %.1fs on %s" % (time.time() - t0, model.device))
    if model.degraded:
        print("  note   : CPU is roughly 5x slower than the GPU here. Long "
              "utterances will lag.")
    print("")
    print("  Press %s and start talking.\n" % HOTKEY.upper())

    global _stream
    _stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                             dtype="float32", callback=on_audio,
                             blocksize=1024)
    _stream.start()
    stream = _stream
    globals()["_audio_stream"] = stream

    quit_evt = threading.Event()

    # Started only after quit_evt exists. An earlier version launched it above
    # the definition and the app died on startup with UnboundLocalError.
    threading.Thread(target=_watch_for_resume, args=(quit_evt,),
                     daemon=True).start()

    if USE_OVERLAY:
        # tkinter must own the main thread, so the hotkey loop moves onto a
        # worker. The keyboard hook is global and works fine off-thread.
        global _tray

        def open_settings(root):
            return dictate_settings.SettingsWindow(
                root, CFG, apply_settings, on_quit=lambda: ui.stop())

        ui = dictate_overlay.Overlay(hotkey=HOTKEY.upper(),
                                     on_quit=quit_evt.set,
                                     on_settings=open_settings,
                                     level_source=lambda: _level)

        # The tray icon is the app's real home: quiet when idle, always
        # reachable, and where you quit from. Its menu callbacks arrive on the
        # tray thread, so they hand work to the UI thread rather than touching
        # any widget directly.
        _tray = dictate_tray.Tray(
            on_settings=lambda: ui.root.after(0, ui._open_settings),
            on_quit=lambda: ui.stop(),
            on_vocab=lambda: os.startfile(os.path.join(HERE, "vocabulary.txt")),
            on_toggle_startup=lambda want: dictate_config.set_startup(want),
            startup_enabled=dictate_config.is_startup_enabled)
        if _tray.start():
            print("  tray   : icon added. right-click it for settings or quit")
        else:
            print("  tray   : unavailable (pystray missing), pill only")
            ui.auto_hide = False
        globals()["_ui"] = ui
        loop = threading.Thread(
            target=hotkey_loop,
            args=(model, prompt, rules, terms, quit_evt), daemon=True)
        loop.start()
        set_state("cpu" if model.degraded else "idle",
                  "on CPU, slower" if model.degraded else "")
        if HIDE_CONSOLE:
            dictate_overlay.hide_console()
        try:
            ui.run()                       # blocks until quit
        finally:
            quit_evt.set()
            loop.join(timeout=5)
    else:
        try:
            hotkey_loop(model, prompt, rules, terms, quit_evt)
        except KeyboardInterrupt:
            pass

    _rec.clear()
    stream.stop(); stream.close()
    print("\n  bye\n")


def _rearm():
    """Re-register the hotkey and reopen the microphone.

    Windows drops low-level keyboard hooks across Modern Standby, and the
    audio stream goes stale with them. The process survives, so nothing looks
    wrong: no crash, nothing in the log, and F9 simply stops doing anything.
    Reported after a laptop was folded for three hours, and confirmed against
    the Kernel-Power log - standby 14:13, resume 16:47, silent ever since.
    """
    global _stream
    ok = []
    try:
        keyboard.unhook_all()
    except Exception:
        pass

    # unhook_all() is not enough, and this is why the first fix looked like it
    # worked and did not. It clears the handler table but leaves the listener's
    # `listening` flag True, so the next add_hotkey() calls
    # start_if_necessary(), sees listening is already True, and returns without
    # doing anything. The Windows low-level hook that standby removed is never
    # reinstalled.
    #
    # The result is the worst shape of failure: re-arming reports success,
    # "re-armed after resume: hotkey, microphone" appears in the log, and no
    # key event ever arrives again. Confirmed after a 300 minute sleep with all
    # three recovery steps logged as done and F9 still dead.
    #
    # Clearing the flag forces init(), which calls _os_keyboard.init() and
    # spawns fresh listening and processing threads with a new OS hook. The old
    # pair are daemon threads on a dead hook; they are left to be collected at
    # exit rather than joined, because joining a thread blocked in a removed
    # Windows hook is how this hangs instead of recovering.
    try:
        listener = getattr(keyboard, "_listener", None)
        if listener is not None and getattr(listener, "listening", False):
            listener.listening = False
    except Exception as e:
        print("  could not reset the keyboard listener (%s)" % type(e).__name__)

    try:
        keyboard.add_hotkey(HOTKEY, _toggle.set, suppress=True)
        ok.append("hotkey")
    except Exception:
        try:
            keyboard.add_hotkey(HOTKEY, _toggle.set)
            ok.append("hotkey (unsuppressed)")
        except Exception as e:
            print("  could not re-arm the hotkey: %s" % type(e).__name__)

    try:
        if _stream is not None:
            try:
                _stream.stop(); _stream.close()
            except Exception:
                pass
        _stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                 dtype="float32", callback=on_audio,
                                 blocksize=1024)
        _stream.start()
        ok.append("microphone")
    except Exception as e:
        print("  could not reopen the microphone: %s" % type(e).__name__)
    print("  re-armed after resume: %s" % (", ".join(ok) or "nothing"))
    return bool(ok)


# How recently a resume-restart happened, so a failure to come back cannot
# become a restart loop. Kept next to the app rather than in memory because the
# whole point is that the process is replaced.
_RESTART_MARKER = os.path.join(HERE, ".resume-restart")
_RESTART_MIN_GAP_S = 90.0


def _restart_after_resume():
    """Hand off to a fresh process rather than rebuild CUDA in this one.

    Returns False if it declined, in which case the caller carries on with the
    in-process path and whatever risk that carries.
    """
    now = time.time()
    try:
        with open(_RESTART_MARKER, "r", encoding="utf-8") as f:
            last = float(f.read().strip() or 0)
    except Exception:
        last = 0.0
    if now - last < _RESTART_MIN_GAP_S:
        print("  restarted %.0fs ago already, not looping" % (now - last))
        return False

    launcher = os.path.join(HERE, "Dictate.cmd")
    if not os.path.exists(launcher):
        print("  no launcher next to the app, cannot hand off")
        return False

    try:
        with open(_RESTART_MARKER, "w", encoding="utf-8") as f:
            f.write("%f" % now)
    except Exception:
        pass

    print("  handing off to a fresh process; the GPU cannot be rebuilt in "
          "this one")
    try:
        import subprocess
        # Wait before launching: the single-instance mutex is held until this
        # process is gone, so a launch that raced us would be refused.
        subprocess.Popen(
            ["cmd", "/c", "timeout /t 6 /nobreak >nul & \"%s\"" % launcher],
            creationflags=0x00000008 | 0x00000200,   # DETACHED, NEW_GROUP
            close_fds=True)
    except Exception as e:
        print("  could not spawn the replacement (%s), staying up"
              % type(e).__name__)
        return False

    # Re-arm first so the few seconds before the replacement arrives are not
    # dead, then leave. os._exit rather than sys.exit: interpreter shutdown
    # would deallocate the model and call into the destroyed CUDA context,
    # which is the crash this whole path exists to avoid.
    try:
        _rearm()
    except Exception:
        pass
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)


def _standby_seconds():
    """Total time this machine has spent in low-power standby since boot.

    GetTickCount64 counts milliseconds since boot INCLUDING standby.
    QueryUnbiasedInterruptTime counts 100ns intervals EXCLUDING it. The
    difference is standby, and it is the only one of the two that moves when
    Windows suspends without stopping the clock.

    Returns None if either call is unavailable, so the caller falls back to
    the wall clock rather than losing the watchdog entirely.
    """
    try:
        import ctypes                    # local, like every other use in here
        k32 = ctypes.windll.kernel32
        k32.GetTickCount64.restype = ctypes.c_ulonglong
        unbiased = ctypes.c_ulonglong()
        if not k32.QueryUnbiasedInterruptTime(ctypes.byref(unbiased)):
            return None
        return (k32.GetTickCount64() / 1000.0) - (unbiased.value / 10000000.0)
    except Exception:
        return None


def _watch_for_resume(quit_evt, tick=5.0, gap=60.0, standby_gap=15.0):
    """Notice that the machine slept, and re-arm.

    Two detectors, because they catch different kinds of sleep and the missing
    one is what let this bug survive three fixes.

    The wall clock catches S3 sleep and hibernation, where the machine really
    stops and time.time() jumps. That was the original detector and it is
    correct for those.

    It is silent on Modern Standby, which is what this laptop actually does.
    There the OS stays at low power and keeps scheduling threads, so
    time.sleep(5) keeps returning after about five seconds and no jump ever
    appears - while Windows still removes the low-level keyboard hook. Four
    silent F9 failures, and not one "woke after" line in the log to show for
    them.

    _standby_seconds() measures exactly the time the wall clock cannot see. On
    this machine it read 40 hours of accumulated standby since boot and moved
    by 0.000s across a second of ordinary running, so a rise in it means
    standby and nothing else.
    """
    last = time.time()
    last_standby = _standby_seconds()
    while not quit_evt.is_set():
        time.sleep(tick)
        now = time.time()
        standby = _standby_seconds()

        slept = now - last > gap
        why = "%.0f minutes asleep" % ((now - last) / 60.0) if slept else ""
        if standby is not None and last_standby is not None:
            in_standby = standby - last_standby
            if in_standby > standby_gap:
                slept = True
                why = "%.0f minutes in standby" % (in_standby / 60.0)
        last_standby = standby

        if slept:
            print("  woke after %s, re-arming" % why)
            # Everything here is best-effort. If any of it raises, the thread
            # must not die: it is the only thing that will notice the NEXT
            # sleep, and a dead watchdog fails exactly as silently as the bug
            # it exists to fix.
            try:
                _rec.clear()
                drain()
                # Do NOT rebuild the model here. Sleep destroys the CUDA
                # context, and every route back into it - plan_device's
                # get_cuda_device_count, the WhisperModel constructor, or
                # freeing the old model - crashes the process in native code
                # with no traceback and nothing an except clause can catch.
                # Five fixes tried; the log shows the fifth still faulting
                # seven seconds after "re-arming".
                #
                # A fresh process has never once failed, so hand off to one.
                # _restart_after_resume does not return when it succeeds.
                if _model is not None and _model.device != "cpu":
                    _restart_after_resume()
                _rearm()
                set_state("idle")
            except Exception as e:
                print("  re-arm failed (%s), the watchdog is still running"
                      % type(e).__name__)
        last = now


def hotkey_loop(model, prompt, rules, terms, quit_evt):
    """Watch the hotkey and drive recording.

    Uses keyboard.add_hotkey rather than reading raw key events, for two
    reasons found in real use:

      1. A raw event loop can only match a single key. F9 alone collides with
         Microsoft Edge, where it opens Immersive Reader, so a combination has
         to be expressible.
      2. add_hotkey can SUPPRESS the key, which stops it reaching the app
         underneath. Without that, starting dictation in Edge also flipped
         Edge into reading mode.

    The callback runs on the keyboard library's own thread, so it only sets an
    event; all the real work stays on this thread.
    """
    toggle = _toggle
    started = 0.0
    worker = None
    stop_evt = None
    suppressed = True

    try:
        keyboard.add_hotkey(HOTKEY, toggle.set, suppress=True)
    except Exception:
        # Some keys cannot be suppressed. Better to work unsuppressed than to
        # not work at all - the startup banner says which one you got.
        suppressed = False
        try:
            keyboard.add_hotkey(HOTKEY, toggle.set)
        except Exception as e:
            print("  could not bind %r (%s). Set another key in settings."
                  % (HOTKEY, type(e).__name__))
            quit_evt.set()
            return
    globals()["_hotkey_suppressed"] = suppressed

    try:
        while not quit_evt.is_set():
            if keyboard.is_pressed("esc"):
                break
            if not toggle.wait(0.15):
                # Nothing pressed. If a recording is running anyway, this is
                # the only place that can notice it has run too long: the
                # hotkey is what normally ends one, and it is not coming.
                if _rec.is_set() and time.time() - started > MAX_SECONDS:
                    print("  [%d minute limit reached] typing what was said..."
                          % (MAX_SECONDS / 60))
                    toggle.set()
                continue
            toggle.clear()

            if not _rec.is_set():
                drain()
                # If OS sleep stopped the stream or invalidated it, restart it
                try:
                    astream = globals().get("_audio_stream")
                    if astream is not None and not astream.active:
                        print("  restarting audio stream after sleep/reset...")
                        try:
                            astream.stop()
                            astream.close()
                        except Exception:
                            pass
                        new_stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                                    dtype="float32", callback=on_audio,
                                                    blocksize=1024)
                        new_stream.start()
                        globals()["_audio_stream"] = new_stream
                except Exception as e:
                    print("  audio stream check note: %s" % e)
                _rec.set()
                started = time.time()
                # Remember where the words are meant to go. Focus can drift
                # during a long dictation, and text typed into the wrong
                # window is worse than no text at all.
                globals()["_target_hwnd"], globals()["_target_title"] =                     core.foreground_window()
                set_state("listening")
                print("  [recording... press %s to stop]" % HOTKEY.upper())
                if USE_STREAM:
                    session = dictate_stream.StreamingSession(
                        model, prompt=prompt, pause_s=PAUSE_S,
                        vad_threshold=VAD_THRESHOLD,
                        gate=_gate_phrase if USE_GATE else None)
                    stop_evt = threading.Event()
                    worker = threading.Thread(
                        target=stream_worker,
                        args=(session, stop_evt, rules, terms), daemon=True)
                    worker.start()
            else:
                _rec.clear()
                held = time.time() - started
                if USE_STREAM:
                    print("  [stopped after %.1fs] finishing..." % held)
                    set_state("thinking")
                    stop_evt.set()
                    worker.join(timeout=60)
                    worker = None
                    print("")
                else:
                    print("  [stopped after %.1fs] " % held, end="", flush=True)
                    transcribe_and_type(model, drain(), held, prompt, rules,
                                        terms)
                set_state("idle")
    except KeyboardInterrupt:
        pass
    finally:
        _rec.clear()
        try:
            keyboard.remove_hotkey(HOTKEY)
        except Exception:
            pass
        quit_evt.set()
        _ui.stop()


def _fatal(exc):
    """Detached there is no console, so a crash would otherwise be silent:
    the icon simply never appears and nothing says why. Always leave a trace
    the user can actually see."""
    import traceback
    detail = "".join(traceback.format_exception(type(exc), exc,
                                                exc.__traceback__))
    try:
        print(detail)
    except Exception:
        pass
    try:
        with open(os.path.join(HERE, "dictate.log"), "a",
                  encoding="utf-8") as f:
            f.write("\n=== CRASHED ===\n" + detail)
    except Exception:
        pass
    if _LOG_FILE:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Local Dictation could not start.\n\n%s: %s\n\n"
                "Details were written to dictate.log next to the app."
                % (type(exc).__name__, str(exc)[:200]),
                "Local Dictation", 0x10)              # MB_ICONERROR
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:            # noqa: BLE001  last line of defence
        _fatal(exc)
        sys.exit(1)
