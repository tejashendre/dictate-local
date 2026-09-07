"""Settings: do they persist, apply, and leave the pill's focus rule intact?

The pill must never take keyboard focus. The settings window is allowed to -
you are not dictating while you configure - but the pill must still behave
after the settings window has opened and closed. That interaction is the part
worth testing, along with the plain round-trip.

Also guards the decision NOT to use a popup menu: tk_popup on a non-activating
window hangs the process, so settings are a normal Toplevel instead.

    python tests/test_settings.py
"""
import ctypes
import json
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_config as cfg        # noqa: E402
import dictate_overlay             # noqa: E402
import dictate_settings            # noqa: E402

u32 = ctypes.windll.user32


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def foreground():
    h = u32.GetForegroundWindow()
    b = ctypes.create_unicode_buffer(200)
    u32.GetWindowTextW(h, b, 200)
    return h, b.value[:40]


results = []


def test_config_roundtrip():
    print("\n  1. settings file round-trip")
    backup = None
    if os.path.exists(cfg.PATH):
        backup = cfg.PATH + ".bak"
        shutil.copy2(cfg.PATH, backup)
    try:
        loaded = cfg.load()
        ok = check("defaults load", loaded["polish"] in ("off", "fast", "llm"))
        ok &= check("pause is a float", isinstance(loaded["pause_s"], float))

        # Write to a scratch path, never the live file: a test's values once
        # got saved as the user's real settings and broke dictation.
        scratch = cfg.PATH + ".test"
        loaded["polish"] = "off"
        loaded["pause_s"] = 0.7
        cfg.save(loaded, path=scratch)
        again = cfg.load(scratch)
        ok &= check("saved values come back",
                    again["polish"] == "off" and abs(again["pause_s"] - 0.7) < 1e-9,
                    "%s %s" % (again["polish"], again["pause_s"]))

        with open(scratch, encoding="utf-8") as f:
            raw = json.load(f)
        ok &= check("file holds only known keys",
                    set(raw) <= set(cfg.SCHEMA), str(set(raw) - set(cfg.SCHEMA)))

        os.environ["DICTATE_POLISH"] = "llm"
        ok &= check("environment still overrides the file",
                    cfg.load()["polish"] == "llm")
        del os.environ["DICTATE_POLISH"]

        ok &= check("restart-needed flags are declared",
                    cfg.needs_restart("model") and not cfg.needs_restart("pause_s"))
        ok &= check("the live file is refused during tests",
                    cfg.save(loaded) is None)
        try:
            os.remove(scratch)
        except Exception:
            pass
    finally:
        if backup:
            shutil.move(backup, cfg.PATH)
        elif os.path.exists(cfg.PATH):
            os.remove(cfg.PATH)
    return ok


def test_window_and_focus():
    print("\n  2. settings window opens, applies, and the pill still behaves")
    applied = {}
    before = foreground()

    def on_apply(new, changed):
        applied["new"] = new
        applied["changed"] = changed
        return ["applied %d change(s)" % len(changed)]

    ov = dictate_overlay.Overlay(hotkey="F9")
    state = {}

    def driver():
        time.sleep(0.9)
        ours = ov._hwnd(ov.root)
        state["pill_fg_before_settings"] = (foreground()[0] == ours)

        holder = {}

        def open_it():
            holder["win"] = dictate_settings.SettingsWindow(
                ov.root, cfg.load(), on_apply)
        ov.root.after(0, open_it)
        time.sleep(1.2)
        state["opened"] = "win" in holder and bool(
            holder["win"].win.winfo_exists())

        # change something and save, the way a click would
        def do_save():
            w = holder["win"]
            w.vars["polish"].set("off")
            w.vars["pause_s"].set(0.9)
            w._save()
        ov.root.after(0, do_save)
        time.sleep(0.9)
        state["saved"] = "new" in applied

        ov.root.after(0, lambda: holder["win"]._close())
        time.sleep(0.6)

        # the pill must still not be the foreground window afterwards
        ov.set_state("listening")
        time.sleep(0.5)
        state["pill_fg_after_settings"] = (foreground()[0] == ours)
        ov.stop()

    backup = cfg.PATH + ".bak2" if os.path.exists(cfg.PATH) else None
    if backup:
        shutil.copy2(cfg.PATH, backup)
    t = threading.Thread(target=driver, daemon=True)
    t.start()
    ov.run()
    t.join(timeout=10)
    if backup:
        shutil.move(backup, cfg.PATH)

    ok = check("settings window opened", state.get("opened"))
    ok &= check("save applied the changes", state.get("saved"),
                str(applied.get("changed"))[:60])
    ok &= check("values reached the handler",
                applied.get("new", {}).get("polish") == "off"
                and abs(applied.get("new", {}).get("pause_s", 0) - 0.9) < 1e-9)
    ok &= check("pill did not hold focus before settings",
                not state.get("pill_fg_before_settings", True))
    ok &= check("pill still does not hold focus after settings closed",
                not state.get("pill_fg_after_settings", True))
    return ok


def test_startup_shortcut():
    print("\n  3. start-with-Windows shortcut")
    was = cfg.is_startup_enabled()
    try:
        ok_on, msg_on = cfg.set_startup(True)
        ok = check("shortcut created", ok_on and cfg.is_startup_enabled(), msg_on)
        ok_off, msg_off = cfg.set_startup(False)
        ok &= check("shortcut removed",
                    ok_off and not cfg.is_startup_enabled(), msg_off)
    finally:
        cfg.set_startup(was)
    return ok


def test_a_changed_default_reaches_an_existing_user():
    print("\n  a changed default has to reach someone who already has a file")
    import io
    import json
    import tempfile
    import dictate_config as dc

    # The bug this exists for. "stream" defaulted to True in v1 and Section 5.1
    # changed it to False, but a stored value beats a default, so the whole v2
    # decode path never reached the one person already using the tool. Nothing
    # looked broken from outside: dictation worked, it was just measurably worse
    # on the utterances streaming split, and nothing said so.
    d = tempfile.mkdtemp()
    p = os.path.join(d, "settings.json")
    v1 = {"hotkey": "f9", "stream": True, "pause_s": 0.7,
          "voice_level": 0.00853, "vad_threshold": 0.5959128065395096,
          "model": "small.en"}
    io.open(p, "w", encoding="utf-8").write(json.dumps(v1, indent=2))

    cfg = dc.load(p)
    ok = check("a v1 file has streaming retired", cfg["stream"] is False)
    ok &= check("and is stamped so it happens once",
                cfg["settings_version"] == dc.SETTINGS_VERSION)

    disk = json.load(io.open(p, encoding="utf-8"))
    ok &= check("the change is persisted, not just returned",
                disk.get("stream") is False
                and disk.get("settings_version") == dc.SETTINGS_VERSION)

    # A migration that quietly dropped a calibrated value would be a worse bug
    # than the one it fixes. voice_level and vad_threshold were measured from
    # this microphone and cannot be recovered from a default.
    ok &= check("voice_level survives untouched",
                disk.get("voice_level") == 0.00853,
                repr(disk.get("voice_level")))
    ok &= check("vad_threshold survives to full precision",
                disk.get("vad_threshold") == 0.5959128065395096,
                repr(disk.get("vad_threshold")))
    ok &= check("every original key is still there",
                all(k in disk for k in v1),
                str([k for k in v1 if k not in disk]))

    # Once it has run, the user owns the setting again.
    disk["stream"] = True
    io.open(p, "w", encoding="utf-8").write(json.dumps(disk, indent=2))
    ok &= check("turning it back on afterwards is respected",
                dc.load(p)["stream"] is True,
                "a migration that kept overruling a choice would be the bug")
    ok &= check("and it is still respected on the next load",
                dc.load(p)["stream"] is True)

    # A fresh install has no file. The defaults already carry the new
    # behaviour, so migrating would mean writing a file nobody asked for.
    p2 = os.path.join(tempfile.mkdtemp(), "settings.json")
    fresh = dc.load(p2)
    ok &= check("a fresh install already has the new default",
                fresh["stream"] is False)
    ok &= check("and no settings file is created just by reading it",
                not os.path.exists(p2))

    # Garbage must not migrate into something that looks valid.
    for name, blob in (("null", "null"), ("a list", "[1,2]"),
                       ("not json", "<<<>>>"), ("empty", "")):
        p3 = os.path.join(tempfile.mkdtemp(), "settings.json")
        io.open(p3, "w", encoding="utf-8").write(blob)
        try:
            c = dc.load(p3)
            ok &= check("%-8s still falls back to defaults" % name,
                        c["hotkey"] == "f9" and c["stream"] is False)
        except Exception as e:
            ok &= check("%-8s still falls back to defaults" % name, False,
                        "RAISED %s" % type(e).__name__)

    # True == 1 in Python, so a bool stored where the version goes would have
    # compared equal to version 1 and skipped the migration silently.
    p4 = os.path.join(tempfile.mkdtemp(), "settings.json")
    io.open(p4, "w", encoding="utf-8").write(
        json.dumps({"stream": True, "settings_version": True}))
    ok &= check("a bool version does not count as version 1",
                dc.load(p4)["stream"] is False)
    return ok


def main():
    results.append(test_config_roundtrip())
    results.append(test_window_and_focus())
    results.append(test_startup_shortcut())
    results.append(test_a_changed_default_reaches_an_existing_user())
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
