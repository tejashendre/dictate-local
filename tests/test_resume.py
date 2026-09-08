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
import io
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


def test_modern_standby_is_detected():
    """The failure that survived three fixes, because it was never detected.

    F9 died four times after the laptop was left alone. Every fix so far
    improved what _rearm() does. None of them noticed that on this machine the
    watchdog never called it: the wall-clock detector needs a 60 second jump
    between two 5 second ticks, and Modern Standby does not produce one. The
    OS stays at low power and keeps scheduling threads, so time.sleep(5) keeps
    returning after five seconds while Windows still removes the keyboard hook.

    The evidence was in the log by absence. Kernel-Power recorded standby at
    20:36:15 and resume at 21:02:58, and across that whole run dictate.log
    contained not one "woke after" line.

    So the detector now also watches the gap between GetTickCount64, which
    counts standby, and QueryUnbiasedInterruptTime, which does not.
    """
    import dictate
    print("\n  3. Modern Standby, where the wall clock never jumps")
    ok = True

    real = dictate._standby_seconds
    value = {"s": 100.0}
    dictate._standby_seconds = lambda: value["s"]
    calls = {"rearm": 0}
    real_rearm = dictate._rearm
    dictate._rearm = lambda: calls.__setitem__("rearm", calls["rearm"] + 1)

    quit_evt = threading.Event()
    t = threading.Thread(
        target=dictate._watch_for_resume, args=(quit_evt,),
        kwargs={"tick": 0.1, "gap": 3600.0, "standby_gap": 5.0},
        daemon=True)
    t.start()
    try:
        time.sleep(0.4)
        ok &= check("ordinary running is not mistaken for standby",
                    calls["rearm"] == 0,
                    "re-armed %d times" % calls["rearm"])

        # A short standby must not trigger a full model rebuild.
        value["s"] = 102.0
        time.sleep(0.4)
        ok &= check("a 2 second standby is below the threshold",
                    calls["rearm"] == 0)

        # This is the real case: the wall clock has not moved at all, gap is
        # an hour so the old detector cannot possibly fire, and only standby
        # time has advanced.
        value["s"] = 102.0 + 1600.0
        time.sleep(0.4)
        ok &= check("26 minutes of standby IS detected",
                    calls["rearm"] >= 1,
                    "the wall clock never moved; this is the bug")

        before = calls["rearm"]
        time.sleep(0.4)
        ok &= check("and it re-arms once, not on every tick after",
                    calls["rearm"] == before,
                    "re-armed %d more times" % (calls["rearm"] - before))
    finally:
        quit_evt.set()
        t.join(timeout=2)
        dictate._standby_seconds = real
        dictate._rearm = real_rearm

    print("\n  4. the standby clock itself")
    a = dictate._standby_seconds()
    ok &= check("Windows reports standby time", a is not None,
                "%.0f seconds since boot" % a if a is not None else "None")
    if a is not None:
        time.sleep(0.35)
        b = dictate._standby_seconds()
        # If this drifted while awake it would re-arm at random, rebuilding
        # the CUDA model in the middle of dictation.
        ok &= check("and it does not drift while awake",
                    abs(b - a) < 0.25, "moved %.3fs" % (b - a))
    return ok


def test_a_dead_model_is_never_freed():
    """The crash that killed the process on every single resume.

    reload_after_resume knew the rule and wrote it down: after sleep the CUDA
    context is gone, and freeing the old model's buffers calls into that dead
    context and kills the process in native code, ucrtbase.dll 0xc0000409,
    with no Python traceback and nothing an except clause can catch. The stated
    fix was to leak about 500 MB per sleep rather than lose the process.

    It did not leak. `del old` dropped the only remaining reference, CPython's
    refcount hit zero, and the deallocator ran on the spot. The line written to
    prevent the crash was the line causing it.

    Confirmed from the Windows Application log on 8 September 2026: resume at
    13:15:58, pythonw faulting in ucrtbase.dll at 13:16:04, and the same
    signature again at 11:59:32. Pinned to the exact statement by what the log
    does NOT contain: the order is build, assign, `del old`, then print "model
    rebuilt after sleep", and that final line never appeared once.

    A sentinel object stands in for the model here. Freeing the real one takes
    the process with it, so the test can never use a real one; what it asserts
    is that nothing with a dead context is dropped, which is the property.
    """
    import gc
    import weakref
    import dictate_core as core

    print("\n  5. a model whose GPU context died is never deallocated")
    ok = True
    freed = []

    class DeadContextModel:
        def __init__(self, tag):
            self.tag = tag

        def __del__(self):
            freed.append(self.tag)

        def transcribe(self, *a, **k):
            return iter([]), None

    import faster_whisper
    real_cls = faster_whisper.WhisperModel
    real_plan = core.plan_device
    faster_whisper.WhisperModel = (
        lambda name, device=None, compute_type=None: DeadContextModel("new"))
    core.plan_device = lambda n: ("cpu", "int8", "stub")
    try:
        t = core.Transcriber("small.en", on_event=lambda m: None)
        doomed = DeadContextModel("slept")
        t.model, t.device, t.compute = doomed, "cuda", "int8_float16"
        ref = weakref.ref(doomed)
        del doomed
        t.reload_after_resume()
        gc.collect()
        ok &= check("reload_after_resume keeps the old model alive",
                    ref() is not None and "slept" not in freed,
                    "freeing it is what crashed the process five times")
        ok &= check("and it is held somewhere deliberate",
                    len(getattr(core, "_RETIRED_MODELS", [])) >= 1)

        freed[:] = []
        t2 = core.Transcriber("small.en", on_event=lambda m: None)
        d2 = DeadContextModel("tocpu")
        t2.model, t2.device = d2, "cuda"
        ref2 = weakref.ref(d2)
        del d2
        t2._to_cpu()
        gc.collect()
        # Assigning over self.model drops the last reference just as surely as
        # del does, and this path runs precisely when the GPU has failed.
        ok &= check("_to_cpu keeps it alive too",
                    ref2() is not None and "tocpu" not in freed,
                    "rebinding self.model frees it just like del")
    finally:
        faster_whisper.WhisperModel = real_cls
        core.plan_device = real_plan
    return ok


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

    print("\n  4. the model is rebuilt on wake, before anything transcribes")
    # Sleep destroys the CUDA context. Touching the old model afterwards kills
    # the process in native code - ucrtbase.dll, exception 0xc0000409 - with no
    # Python traceback and nothing to catch. So the rebuild has to happen on
    # wake, and it must not free the old model, because freeing it is itself a
    # touch of the dead context.
    order = []

    class _FakeModel:
        device = "cuda"

        def reload_after_resume(self):
            order.append("reload")
            return True

    real_model = dictate._model
    dictate._model = _FakeModel()
    dictate._rearm = lambda: order.append("rearm")
    quitm = threading.Event()
    tm = threading.Thread(target=dictate._watch_for_resume, args=(quitm,),
                          kwargs={"tick": 0.15, "gap": 0.4}, daemon=True)
    tm.start()
    time.time = lambda: real_time() + 3 * 3600.0
    time.sleep(0.5)
    time.time = real_time
    quitm.set()
    time.sleep(0.3)
    dictate._model = real_model
    dictate._rearm = real_rearm
    ok_all &= check("the model is rebuilt on wake", "reload" in order,
                    str(order))
    ok_all &= check("rebuilt BEFORE the hotkey and mic are re-armed",
                    order[:2] == ["reload", "rearm"], str(order))

    print("\n  5. rebuilding never frees the old model")
    src = open(os.path.join(ROOT, "dictate_core.py"), encoding="utf-8").read()
    body = src[src.index("def reload_after_resume"):
               src.index("def transcribe", src.index("def reload_after_resume"))]
    # Strip comments before looking for calls: the code says "never .close()"
    # in prose, and matching that would be a false positive.
    code_only = "\n".join(ln.split("#")[0] for ln in body.split("\n"))
    ok_all &= check("no close() or unload() against the dead context",
                    ".close()" not in code_only
                    and ".unload()" not in code_only)
    ok_all &= check("the old reference is only dropped, never released",
                    "del old" in body)

    print("\n  6. the watchdog survives a failure inside re-arming")
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

    print("\n  7. re-arming reinstalls the OS hook, not just the callback")
    # The failure this catches is the one that looked fixed twice.
    #
    # keyboard.unhook_all() clears the handler table but leaves the listener's
    # `listening` flag True. start_if_necessary() then sees it is already
    # listening and returns without doing anything, so the Windows low-level
    # hook that Modern Standby removed is never reinstalled. add_hotkey()
    # succeeds, "re-armed after resume: hotkey, microphone" is written to the
    # log, and not one key event ever arrives again.
    #
    # Observed after a 300 minute sleep with all three recovery steps logged as
    # complete and F9 still dead.
    src = io.open(os.path.join(ROOT, "dictate.py"), encoding="utf-8").read()
    body = src[src.index("def _rearm"):src.index("def ", src.index("def _rearm") + 8)]
    code = "\n".join(ln.split("#")[0] for ln in body.split("\n"))

    ok_all &= check("_rearm clears the listener flag",
                    "listening = False" in code,
                    "otherwise start_if_necessary() is a no-op")
    ok_all &= check("it happens before the hotkey is re-registered",
                    code.index("listening = False") < code.index("add_hotkey"),
                    "clearing it afterwards would be too late")
    ok_all &= check("the old listener threads are left alone",
                    "listening_thread" not in code
                    and "processing_thread" not in code,
                    "joining a thread blocked in a removed hook hangs, so the "
                    "old pair are left to be collected at exit")

    # And the library really does behave the way the fix assumes.
    import keyboard
    listener = keyboard._listener
    ok_all &= check("the listener exposes the flag the fix clears",
                    hasattr(listener, "listening"))
    import inspect
    starter = inspect.getsource(listener.start_if_necessary)
    ok_all &= check("start_if_necessary is gated on that flag",
                    "if not self.listening" in starter,
                    "this is why clearing it forces a fresh hook")
    unhook = inspect.getsource(keyboard.unhook_all)
    ok_all &= check("and unhook_all alone never clears it",
                    "listening" not in unhook.split("def ")[1],
                    "which is the whole bug")

    ok_all &= test_modern_standby_is_detected()
    ok_all &= test_a_dead_model_is_never_freed()

    print("\n  %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
