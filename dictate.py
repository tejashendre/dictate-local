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
USE_OVERLAY = CFG["overlay"]
HIDE_CONSOLE = CFG["hide_console"]
HOTKEY = CFG["hotkey"]
DEVICE_PREF = CFG["device"]
os.environ.setdefault("DICTATE_DEVICE", DEVICE_PREF)

MAX_BACKSPACE = 400
SAMPLE_RATE = 16000
MIN_SECONDS = 0.4
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "transcript.log")

_q = queue.Queue()
_rec = threading.Event()
_last_typed = ""     # what emit() last sent, so "scratch that" can undo it
_level = 0.0         # live microphone loudness, drives the pill meter
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
    speaking, speech_s = core.has_speech(audio, threshold=VAD_THRESHOLD)
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
    model = core.Transcriber(MODEL_NAME, on_event=lambda m: print("  " + m)).load()
    print("  ready in %.1fs on %s" % (time.time() - t0, model.device))
    if model.degraded:
        print("  note   : CPU is roughly 5x slower than the GPU here. Long "
              "utterances will lag.")
    print("")
    print("  Press %s and start talking.\n" % HOTKEY.upper())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            callback=on_audio, blocksize=1024)
    stream.start()
    globals()["_audio_stream"] = stream

    quit_evt = threading.Event()

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
    toggle = threading.Event()
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
                        vad_threshold=VAD_THRESHOLD)
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
