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


def main():
    results.append(test_config_roundtrip())
    results.append(test_window_and_focus())
    results.append(test_startup_shortcut())
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
