"""Is a bigger model worth it on a 4 GB card?

The question is not "is medium more accurate" - of course it is - but whether
the accuracy gained is worth the VRAM and the wait ON THIS HARDWARE, where the
GPU is 4 GB and shared with Ollama.

Measures each model on the same corpus: word error rate on ordinary speech,
vocabulary term recall, latency, and peak VRAM. Downloads what it does not
have, which is the one network access this project allows.

    python tests/bench_models.py
    python tests/bench_models.py --models small.en,distil-medium.en
"""
import argparse
import os
import subprocess
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ["DICTATE_TESTING"] = "1"

import dictate_core as core          # noqa: E402
core.enable_cuda_dlls()
from faster_whisper import WhisperModel      # noqa: E402
from phrases import VOCAB_PHRASES, CONTROL_PHRASES   # noqa: E402

AUDIO = os.path.join(HERE, "audio")
CANDIDATES = ["small.en", "distil-medium.en", "medium.en"]


def vram_used():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return 0


def load(path):
    with wave.open(path, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return a.astype(np.float32), n / sr


def words(s):
    import re
    return re.sub(r"[^a-z0-9\s]", " ", s.lower()).split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    if not r:
        return 0.0
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=np.int32)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1,
                          d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r), len(h)] / len(r)


def bench(name, prompt, terms):
    base = vram_used()
    t0 = time.time()
    try:
        model = WhisperModel(name, device="cuda", compute_type="int8_float16")
    except Exception as e:
        return {"name": name, "error": "%s: %s" % (type(e).__name__,
                                                   str(e)[:70])}
    load_s = time.time() - t0

    # force the encoder to touch cuBLAS before timing anything
    try:
        list(model.transcribe(np.zeros(8000, dtype=np.float32),
                              language="en", beam_size=1)[0])
    except Exception as e:
        return {"name": name, "error": "warmup failed: %s" % type(e).__name__}
    peak = vram_used() - base

    hits = misses = 0
    missed = []
    wers, secs, durs = [], [], []

    for i, (_ref, want) in enumerate(VOCAB_PHRASES):
        p = os.path.join(AUDIO, "vocab_%02d.wav" % i)
        if not os.path.exists(p):
            continue
        audio, dur = load(p)
        t0 = time.time()
        segs, _ = model.transcribe(audio, language="en", beam_size=1,
                                   vad_filter=True,
                                   condition_on_previous_text=False,
                                   initial_prompt=prompt)
        got = " ".join(s.text.strip() for s in segs).strip()
        secs.append(time.time() - t0)
        durs.append(dur)
        got, _ = core.apply_corrections(got, core.load_corrections())
        got, _ = core.fuzzy_snap(got, terms)
        for term in want:
            if term.lower() in got.lower():
                hits += 1
            else:
                misses += 1
                missed.append(term)

    for i, (ref, _w) in enumerate(CONTROL_PHRASES):
        p = os.path.join(AUDIO, "control_%02d.wav" % i)
        if not os.path.exists(p):
            continue
        audio, dur = load(p)
        t0 = time.time()
        segs, _ = model.transcribe(audio, language="en", beam_size=1,
                                   vad_filter=True,
                                   condition_on_previous_text=False,
                                   initial_prompt=prompt)
        got = " ".join(s.text.strip() for s in segs).strip()
        secs.append(time.time() - t0)
        durs.append(dur)
        wers.append(wer(ref, got))

    del model
    total = hits + misses
    return {
        "name": name,
        "load_s": load_s,
        "vram": peak,
        "recall": hits / total if total else 0.0,
        "hits": hits, "total": total, "missed": missed,
        "wer": sum(wers) / len(wers) if wers else 0.0,
        "rtf": sum(durs) / sum(secs) if secs else 0.0,
        "avg_ms": 1000 * sum(secs) / len(secs) if secs else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(CANDIDATES))
    args = ap.parse_args()

    terms = core.load_vocabulary()
    prompt, used, _dropped = core.build_prompt(terms)
    free = 4096 - vram_used()
    print("  %d vocabulary terms in the prompt, %d MB VRAM free\n"
          % (len(used), free))

    rows = []
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        print("  benchmarking %s ..." % name)
        r = bench(name, prompt, terms)
        rows.append(r)
        if "error" in r:
            print("    FAILED: %s\n" % r["error"])
        else:
            print("    %d MB VRAM, loaded in %.1fs, %.1fx realtime\n"
                  % (r["vram"], r["load_s"], r["rtf"]))

    print("\n  %-18s %7s %8s %9s %10s %9s"
          % ("MODEL", "VRAM", "TERMS", "WER", "REALTIME", "PER PHRASE"))
    print("  " + "-" * 68)
    for r in rows:
        if "error" in r:
            print("  %-18s  %s" % (r["name"], r["error"]))
            continue
        print("  %-18s %5d MB %5d/%-2d %8.1f%% %9.1fx %8.0f ms"
              % (r["name"], r["vram"], r["hits"], r["total"],
                 100 * r["wer"], r["rtf"], r["avg_ms"]))

    ok = [r for r in rows if "error" not in r]
    if len(ok) > 1:
        base = ok[0]
        print("\n  against %s:" % base["name"])
        for r in ok[1:]:
            print("    %-18s %+d terms, WER %+.1f points, %.1fx the wait, "
                  "%+d MB"
                  % (r["name"], r["hits"] - base["hits"],
                     100 * (r["wer"] - base["wer"]),
                     (r["avg_ms"] / base["avg_ms"]) if base["avg_ms"] else 0,
                     r["vram"] - base["vram"]))
    print("\n  Note: this corpus is synthetic speech. It ranks models against\n"
          "  each other honestly, but the absolute numbers are not your voice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
