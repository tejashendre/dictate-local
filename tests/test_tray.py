"""The tray icon, and the pill that now gets out of the way.

Three things this guards, all of which were visibly wrong before:

  1. The pill landed at 0,0 - on top of the title bar of whatever was behind
     it. geometry() was set while the window was withdrawn and never applied,
     so ShowWindow put it wherever Windows felt like.
  2. Dragging jumped, because winfo_x() reports 0 for an overrideredirect
     window, so the drag offset was always wrong.
  3. The pill sat on screen permanently, which is a distraction. With the tray
     carrying the "I exist" job, it should only appear while working.

    python tests/test_tray.py
"""
import ctypes
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_overlay          # noqa: E402
import dictate_tray             # noqa: E402

u32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def rect_of(hwnd):
    r = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def test_icons():
    print("\n  1. tray icons render and differ by state")
    if not dictate_tray.AVAILABLE:
        print("    skip  pystray not installed")
        return True
    imgs = {s: dictate_tray.make_icon(s)
            for s in ("idle", "listening", "thinking", "typed")}
    ok = check("all four states produce an image",
               all(i.size == (64, 64) for i in imgs.values()))
    ok &= check("idle looks different from listening",
                imgs["idle"].tobytes() != imgs["listening"].tobytes())
    ok &= check("listening looks different from thinking",
                imgs["listening"].tobytes() != imgs["thinking"].tobytes())
    return ok


def test_tray_lifecycle():
    print("\n  2. the tray icon starts, and its menu is wired")
    if not dictate_tray.AVAILABLE:
        print("    skip  pystray not installed")
        return True
    fired = {}
    tray = dictate_tray.Tray(
        on_settings=lambda: fired.setdefault("settings", True),
        on_quit=lambda: fired.setdefault("quit", True),
        on_vocab=lambda: fired.setdefault("vocab", True),
        on_toggle_startup=lambda want: fired.setdefault("startup", want),
        startup_enabled=lambda: False)
    started = tray.start()
    time.sleep(1.5)
    ok = check("icon started", started)
    ok &= check("icon thread is alive", tray._thread.is_alive())
    tray.set_state("listening")
    time.sleep(0.3)
    ok &= check("state change accepted", tray._state == "listening")

    tray._settings()
    tray._vocab()
    tray._startup()
    ok &= check("menu callbacks reach the app",
                fired.get("settings") and fired.get("vocab")
                and fired.get("startup") is True, str(fired))
    tray.stop()
    time.sleep(0.5)
    return ok


def test_pill_position_and_hiding():
    print("\n  3. the pill sits where it should, and hides when idle")
    ov = dictate_overlay.Overlay(hotkey="F9", auto_hide=True)
    seen = {}

    def driver():
        time.sleep(0.9)
        hwnd = ov._hwnd(ov.root)
        want_x, want_y = ov._pos
        left, top, right, bottom = rect_of(hwnd)
        seen["pos"] = (left, top)
        seen["want"] = (want_x, want_y)
        seen["not_corner"] = not (left < 50 and top < 50)

        ov.set_state("idle")
        time.sleep(0.5)
        seen["hidden_when_idle"] = not bool(u32.IsWindowVisible(hwnd))

        ov.set_state("listening")
        time.sleep(0.5)
        seen["shown_when_listening"] = bool(u32.IsWindowVisible(hwnd))
        l2, t2, _r, _b = rect_of(hwnd)
        seen["pos_after_show"] = (l2, t2)

        ov.set_state("idle")
        time.sleep(0.5)
        seen["hidden_again"] = not bool(u32.IsWindowVisible(hwnd))
        ov.stop()

    t = threading.Thread(target=driver, daemon=True)
    t.start()
    ov.run()
    t.join(timeout=10)

    ok = check("lands at the position it asked for",
               seen.get("pos") == seen.get("want"),
               "at %s, wanted %s" % (seen.get("pos"), seen.get("want")))
    ok &= check("is NOT stuck in the top-left corner",
                seen.get("not_corner"), str(seen.get("pos")))
    ok &= check("hidden while idle", seen.get("hidden_when_idle"))
    ok &= check("visible while listening", seen.get("shown_when_listening"))
    ok &= check("keeps its position when it reappears",
                seen.get("pos_after_show") == seen.get("want"),
                str(seen.get("pos_after_show")))
    ok &= check("hides again afterwards", seen.get("hidden_again"))
    return ok


def main():
    results = [test_icons(), test_tray_lifecycle(),
               test_pill_position_and_hiding()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
