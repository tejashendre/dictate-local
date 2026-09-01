"""The floating pill, and the one property that makes it safe.

An always-on-top window that takes keyboard focus would be worse than no
window at all: every character the tool types would land in the pill instead
of the document you were writing in. So the test that matters is not "does it
appear" but "does the foreground window stay where it was".

Measured on this machine while writing it: a plain tkinter topmost window
moved the foreground window every single time. WS_EX_NOACTIVATE is what fixes
it, and this test is what stops someone removing that line later.

Shows a real window for a few seconds, then closes itself.

    python tests/test_overlay.py
"""
import ctypes
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dictate_overlay   # noqa: E402

u32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def foreground():
    h = u32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(h, buf, 256)
    return h, buf.value[:40]


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


results = []
seen_states = []


def driver(ov, before):
    """Cycle the pill through every state, watching the foreground window."""
    time.sleep(0.8)
    for state, detail in (("listening", ""), ("thinking", ""),
                          ("typed", "I applied to Zalando through Naukri"),
                          ("idle", "")):
        ov.set_state(state, detail)
        time.sleep(0.55)
        seen_states.append((state, foreground()))

    print("\n  1. focus is never stolen")
    ours = ov._hwnd(ov.root)
    # The question is not "did the foreground change" - the user or another
    # app can move it for unrelated reasons while the test runs - but "did it
    # ever become OUR window". Only the second is a real failure.
    took = [(s, fgw[1]) for s, fgw in seen_states if fgw[0] == ours]
    drifted = [(s, fgw[1]) for s, fgw in seen_states
               if fgw[0] != before[0] and fgw[0] != ours]
    results.append(check("the pill never became the foreground window",
                         not took, "took focus during: %s" % took if took
                         else "foreground stayed elsewhere"))
    if drifted:
        print("       note: foreground moved to another app during the test")
        print("       (%s) - unrelated to the pill, not a failure"
              % ", ".join("%s->%r" % d for d in drifted[:2]))

    print("\n  2. the window really is non-activating and out of alt-tab")
    hwnd = u32.GetParent(ov.root.winfo_id()) or ov.root.winfo_id()
    style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    results.append(check("WS_EX_NOACTIVATE set",
                         bool(style & WS_EX_NOACTIVATE)))
    results.append(check("WS_EX_TOOLWINDOW set, so no alt-tab entry",
                         bool(style & WS_EX_TOOLWINDOW)))

    print("\n  3. it is on top, and visible when there is something to show")
    # The pill auto-hides on idle now, so visibility has to be checked while
    # something is actually happening. Checking after the state cycle ends on
    # idle would be asserting the old, distracting behaviour.
    ov.set_state("listening")
    time.sleep(0.4)
    results.append(check("visible while listening",
                         bool(u32.IsWindowVisible(hwnd))))
    ov.set_state("idle")
    time.sleep(0.4)
    results.append(check("hidden again once idle",
                         not bool(u32.IsWindowVisible(hwnd))))
    results.append(check("topmost attribute held",
                         bool(ov.root.attributes("-topmost"))))

    print("\n  4. state changes from another thread did not crash the UI")
    results.append(check("all 4 states applied", len(seen_states) == 4))

    ov.stop()


def test_mic_meter():
    """The meter must light with sound and warn when the mic goes quiet.

    This is what makes a dead microphone diagnosable. Without it, a muted mic
    and a working one look identical: the pill says "listening" forever.
    """
    print("\n  5. microphone meter")
    level = {"v": 0.05}
    ov = dictate_overlay.Overlay(hotkey="F9", level_source=lambda: level["v"])
    seen = {}

    def driver():
        time.sleep(0.7)
        ov.set_state("listening")
        time.sleep(0.6)
        # The meter is a rendered image now, so assert on the level history
        # that drives it rather than on canvas item colours.
        seen["lit_with_sound"] = sum(1 for v in ov._history if v > 0)

        level["v"] = 0.0                     # a pause, having already heard
        time.sleep(dictate_overlay.SILENT_WARN_S + 1.0)
        seen["pause_text"] = ov.bg.itemcget(ov._label_id, "text")
        seen["not_alarming"] = "no sound" not in seen["pause_text"]
        seen["dark_when_silent"] = all(v <= 0 for v in ov._history)

        level["v"] = 0.08                    # it comes back
        time.sleep(0.6)
        seen["recovered"] = "no sound" not in ov.bg.itemcget(ov._label_id, "text")
        ov.stop()

    t = threading.Thread(target=driver, daemon=True)
    t.start()
    ov.run()
    t.join(timeout=10)

    ok = check("bars light while sound is arriving",
               seen.get("lit_with_sound", 0) > 0,
               "%s of %d bars lit" % (seen.get("lit_with_sound"),
                                      dictate_overlay.METER_BARS))
    ok &= check("the waveform is rendered, not drawn as raw rectangles",
                dictate_overlay.HD and ov._wave_img is not None,
                "HD=%s" % dictate_overlay.HD)
    ok &= check("meter goes dark on silence", seen.get("dark_when_silent"))
    # A thinking pause is not a fault. Warning on it made a working tool look
    # stuck, which is exactly what was reported from real use: one logged
    # phrase ran 22.8s for 27 words, so gaps are normal.
    ok &= check("a pause after hearing you is NOT reported as a fault",
                seen.get("not_alarming"), repr(seen.get("pause_text")))
    ok &= check("clears back to listening when sound returns",
                seen.get("recovered"))

    # But a microphone that never produced anything must still be called out.
    dead = {"v": 0.0}
    ov2 = dictate_overlay.Overlay(hotkey="F9",
                                  level_source=lambda: dead["v"])
    seen2 = {}

    def driver2():
        time.sleep(0.6)
        ov2.set_state("listening")
        time.sleep(dictate_overlay.SILENT_WARN_S + 1.2)
        seen2["text"] = ov2.bg.itemcget(ov2._label_id, "text")
        ov2.stop()

    t2 = threading.Thread(target=driver2, daemon=True)
    t2.start()
    ov2.run()
    t2.join(timeout=10)
    ok &= check("a mic that never made a sound IS reported",
                "no sound" in seen2.get("text", ""), repr(seen2.get("text")))
    return ok


def main():
    before = foreground()
    print("  foreground before showing the pill: %r\n" % before[1])

    ov = dictate_overlay.Overlay(hotkey="F9")
    t = threading.Thread(target=driver, args=(ov, before), daemon=True)
    t.start()
    ov.run()                      # blocks on the main thread until stop()
    t.join(timeout=5)

    results.append(test_mic_meter())
    ok = all(results) and len(results) == 8
    print("\n  %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
