"""GPU contention and fallback.

Three things are checked, and the first two are real failures this machine
actually produced rather than hypotheticals:

  1. A broken CUDA path is caught at STARTUP by the warmup, not on the first
     utterance. This is the cublas64_12.dll case: WhisperModel() constructs
     happily and only transcribe() falls over, so the warmup is what makes it
     survivable.
  2. A GPU that dies mid-session is retried on CPU and the words are NOT lost.
  3. plan_device picks CPU when Ollama has taken the card.

The two GPU scenarios run in child processes on purpose. Deliberately breaking
a CUDA context and then tearing it down in-process can hang on this driver,
and it is not a state the real tool ever reaches: a real run is one process
that falls back once at startup and then stays put. Each child also gets a
hard timeout, so a hang is reported as a failure instead of stalling the suite.

    python tests/test_fallback.py
"""
import os
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_core as core   # noqa: E402

AUDIO = os.path.join(HERE, "audio", "control_00.wav")
EXPECT = "quick brown fox"
CHILD_TIMEOUT = 180


def load_wav(path):
    with wave.open(path, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
    return np.interp(idx, np.arange(len(a)), a).astype(np.float32)


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


# --------------------------------------------------------------------------
# child scenarios
# --------------------------------------------------------------------------

class DyingGPU:
    """A model that works once, then fails the way a starved GPU does."""

    def __init__(self, real):
        self.real = real
        self.calls = 0

    def transcribe(self, *a, **kw):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("CUDA failed with error out of memory")
        return self.real.transcribe(*a, **kw)


def child_broken_cuda():
    """CUDA forced on with the cuBLAS DLL made unfindable."""
    audio = load_wav(AUDIO)
    events = []
    t = core.Transcriber("small.en", on_event=events.append).load()
    text = t.transcribe(audio)
    print("DEVICE=%s" % t.device)
    print("TEXT=%s" % text.strip())
    print("EXPLAINED=%s" % any("warmup" in e or "load failed" in e
                               for e in events))
    for e in events:
        print("EVENT=%s" % e)


def child_dying_gpu():
    """A healthy GPU that fails on the first real transcribe."""
    core.enable_cuda_dlls()
    audio = load_wav(AUDIO)
    events = []
    t = core.Transcriber("small.en", on_event=lambda m: None).load()
    if t.device != "cuda":
        print("SKIP=no working GPU")
        return
    t.on_event = events.append
    t.model = DyingGPU(t.model)
    text = t.transcribe(audio)
    print("DEVICE=%s" % t.device)
    print("TEXT=%s" % text.strip())
    print("EXPLAINED=%s" % any("mid-transcribe" in e for e in events))
    for e in events:
        print("EVENT=%s" % e)


CHILDREN = {"broken-cuda": child_broken_cuda, "dying-gpu": child_dying_gpu}


# --------------------------------------------------------------------------
# parent
# --------------------------------------------------------------------------

def run_child(name, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONUNBUFFERED"] = "1"
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--scenario", name],
                           capture_output=True, text=True,
                           timeout=CHILD_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return None, "child hung past %ds" % CHILD_TIMEOUT
    fields = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            fields.setdefault(k, []).append(v)
    if not fields:
        return None, (r.stderr.strip().splitlines() or ["no output"])[-1]
    return fields, None


def test_broken_cuda():
    print("\n  1. broken CUDA path, forced with DICTATE_DEVICE=cuda")
    # Strip the nvidia wheel dirs off PATH so cuBLAS cannot be found, which is
    # exactly the state this machine was in before it was installed.
    kept = [p for p in os.environ.get("PATH", "").split(os.pathsep)
            if "nvidia" not in p.lower().replace("\\", "/")]
    fields, err = run_child("broken-cuda", {
        "PATH": os.pathsep.join(kept),
        "DICTATE_DEVICE": "cuda",
        "DICTATE_SKIP_DLL_FIX": "1",
    })
    if err:
        return check("child ran", False, err)
    ok = check("fell back to CPU rather than crashing",
               fields.get("DEVICE") == ["cpu"], str(fields.get("DEVICE")))
    ok &= check("still transcribed correctly",
                EXPECT in " ".join(fields.get("TEXT", [])).lower(),
                repr(" ".join(fields.get("TEXT", []))[:50]))
    ok &= check("said why", fields.get("EXPLAINED") == ["True"],
                (fields.get("EVENT") or ["-"])[0][:70])
    return ok


def test_dying_gpu():
    print("\n  2. GPU dies mid-session, audio must survive")
    fields, err = run_child("dying-gpu", {})
    if err:
        return check("child ran", False, err)
    if "SKIP" in fields:
        print("    skip  no working GPU on this run")
        return True
    ok = check("retried and returned the words",
               EXPECT in " ".join(fields.get("TEXT", [])).lower(),
               repr(" ".join(fields.get("TEXT", []))[:50]))
    ok &= check("switched to CPU", fields.get("DEVICE") == ["cpu"])
    ok &= check("reported the switch", fields.get("EXPLAINED") == ["True"],
                (fields.get("EVENT") or ["-"])[0][:70])
    return ok


def test_plan_device():
    print("\n  3. device planning under contention")
    ok = check("plenty free -> GPU",
               core.plan_device("small.en", 3900)[0] == "cuda")
    ok &= check("Ollama holding the card -> CPU",
                core.plan_device("small.en", 300)[0] == "cpu")
    ok &= check("explains itself",
                "holds the card" in core.plan_device("small.en", 300)[2])
    ok &= check("smaller model still fits where small.en does not",
                core.plan_device("base.en", 700)[0] == "cuda")
    return ok


def main():
    if "--scenario" in sys.argv:
        CHILDREN[sys.argv[sys.argv.index("--scenario") + 1]]()
        return 0
    if not os.path.exists(AUDIO):
        print("  no corpus. run:  python tests/make_audio.py")
        return 1
    results = [test_broken_cuda(), test_dying_gpu(), test_plan_device()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
