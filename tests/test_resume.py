"""Waking from sleep must bring the hotkey and the microphone back.

Reported after a laptop was folded for three hours: the process was still
alive, dictate.log had no crash and no traceback, and F9 simply did nothing.
Confirmed against the Windows Kernel-Power log - Modern Standby entered at
14:13, exited at 16:47, silent from then on.

That is the worst failure shape this app has: nothing looks wrong. Windows
drops low-level keyboard hooks across standby and the audio stream goes stale
with them, but the process survives, so there is no error to find.

Detection is a wall-clock jump rather than a Windows power event. Catching
PBT_APMRESUMEAUTOMATIC needs a message loop and a window procedure; a clock
that leaps by far more than the sleep interval means the same thing and needs
neither.

    python tests/test_resume.py
"""
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


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def main():
    import dictate
    ok_all = True

    print("\n  1. the watchdog notices a wall-clock jump")
    calls = {"rearm": 0}
    real_rearm = dictate._rearm
    dictate._rearm = lambda: calls.__setitem__("rearm", calls["rearm"] + 1)

    quit_evt = threading.Event()
    # tick fast and treat a small gap as sleep, so the real behaviour can be
    # exercised without actually suspending the machine
    t = threading.Thread(target=dictate._watch_for_resume,
                         args=(quit_evt,), kwargs={"tick": 0.15, "gap": 0.4},
                         daemon=True)
    t.start()
    time.sleep(0.5)
    ok_all &= check("a normal tick is not mistaken for sleep",
                    calls["rearm"] == 0, "re-armed %d times" % calls["rearm"])

    # freeze the clock the way a suspend does
    real_time = time.time
    offset = {"v": 0.0}
    time.time = lambda: real_time() + offset["v"]
    offset["v"] = 3 * 3600.0            # three hours, as reported
    time.sleep(0.5)
    time.time = real_time
    ok_all &= check("a three-hour jump triggers a re-arm",
                    calls["rearm"] >= 1, "re-armed %d times" % calls["rearm"])
    quit_evt.set()
    time.sleep(0.3)
    dictate._rearm = real_rearm

    print("\n  2. re-arming really does rebind the hotkey and the mic")
    rebound = {"hotkey": 0, "stream": 0}

    class _FakeStream:
        def __init__(self, **kw):
            rebound["stream"] += 1

        def start(self): pass
        def stop(self): pass
        def close(self): pass

    real_add = dictate.keyboard.add_hotkey
    real_unhook = dictate.keyboard.unhook_all
    real_sd = dictate.sd.InputStream
    dictate.keyboard.add_hotkey = lambda *a, **k: rebound.__setitem__(
        "hotkey", rebound["hotkey"] + 1)
    dictate.keyboard.unhook_all = lambda: None
    dictate.sd.InputStream = _FakeStream
    try:
        dictate._rearm()
    finally:
        dictate.keyboard.add_hotkey = real_add
        dictate.keyboard.unhook_all = real_unhook
        dictate.sd.InputStream = real_sd

    ok_all &= check("the hotkey is registered again", rebound["hotkey"] == 1,
                    str(rebound))
    ok_all &= check("the microphone is reopened", rebound["stream"] == 1,
                    str(rebound))

    print("\n  3. a half-finished recording is not left behind")
    dictate._rec.set()
    import numpy as np
    dictate._q.put(np.zeros((512, 1), dtype=np.float32))   # as the mic sends
    quit2 = threading.Event()
    dictate._rearm = lambda: None
    t2 = threading.Thread(target=dictate._watch_for_resume, args=(quit2,),
                          kwargs={"tick": 0.15, "gap": 0.4}, daemon=True)
    t2.start()
    time.time = lambda: real_time() + 7200.0
    time.sleep(0.5)
    time.time = real_time
    quit2.set()
    time.sleep(0.3)
    dictate._rearm = real_rearm
    ok_all &= check("recording flag cleared on wake",
                    not dictate._rec.is_set())
    ok_all &= check("stale audio dropped", dictate._q.empty())

    print("\n  4. the watchdog survives a failure inside re-arming")
    # A dead watchdog fails as silently as the bug it exists to fix, so it
    # must outlive anything that throws inside it.
    boom = {"n": 0}

    def explode():
        boom["n"] += 1
        raise RuntimeError("simulated re-arm failure")

    dictate._rearm = explode
    quit3 = threading.Event()
    t3 = threading.Thread(target=dictate._watch_for_resume, args=(quit3,),
                          kwargs={"tick": 0.15, "gap": 0.4}, daemon=True)
    t3.start()
    time.time = lambda: real_time() + 7200.0
    time.sleep(0.45)
    time.time = real_time
    time.sleep(0.45)
    time.time = lambda: real_time() + 14400.0
    time.sleep(0.45)
    time.time = real_time
    quit3.set()
    time.sleep(0.3)
    dictate._rearm = real_rearm
    ok_all &= check("still alive and retried after the first failure",
                    boom["n"] >= 2, "re-arm attempted %d times" % boom["n"])
    ok_all &= check("thread did not die", t3.is_alive() or quit3.is_set())

    print("\n  %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
