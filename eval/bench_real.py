"""Run every candidate model over the same real recordings.

    python eval/bench_real.py --check              what is cached, what is not
    python eval/bench_real.py --models small.en
    python eval/bench_real.py --allow-download     permission to fetch weights

This is tests/bench_models.py pointed at Tejas's own voice instead of Windows
SAPI speech, with the metrics the synthetic harness could not produce: raw
versus corrected versus final accuracy, protected names and numbers, invented
words, and the silence false-positive rate.

It never changes the production model. It loads candidates, measures them, and
writes a result file. Choosing a winner is a separate decision made after
reading the numbers.

Nothing is downloaded without --allow-download. Weights land in the shared
Hugging Face cache, which is outside this repository and gitignored anyway.
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("DICTATE_TESTING", "1")

import corpus as C                       # noqa: E402
import score as S                        # noqa: E402
import dictate_core as core              # noqa: E402

RESULTS = os.path.join(C.PRIVATE, "results")

# Candidates, with what each one costs to bring onto this machine.
#
# The sizes are the published repository sizes and are treated as estimates
# until a download actually happens: the guard below checks real free disk
# against the estimate plus a margin, and reports both, rather than trusting
# the table.
MODELS = {
    "small.en": {
        "repo": "Systran/faster-whisper-small.en",
        "mb": 490,
        "compute": "int8_float16",
        "note": "the current production model, already cached",
    },
    "distil-large-v3": {
        "repo": "Systran/faster-distil-whisper-large-v3",
        "mb": 1550,
        "compute": "int8_float16",
        "note": "distilled large, aimed at large-v3 accuracy at lower cost",
    },
    "large-v3-turbo": {
        "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "mb": 1620,
        "compute": "int8_float16",
        "note": "reduced decoder layers, int8 to fit a 4 GB card",
    },
}

DEFAULT_CANDIDATES = ["small.en", "distil-large-v3", "large-v3-turbo"]


# --------------------------------------------------------------------------
# Resource measurement, same approach as tests/bench_models.py
# --------------------------------------------------------------------------

def vram_used():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return 0


def vram_free():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return 0


class _MEMORYCOUNTERS(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def peak_ram_mb():
    """Peak working set of this process, without adding a psutil dependency."""
    try:
        counters = _MEMORYCOUNTERS()
        counters.cb = ctypes.sizeof(counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(counters), counters.cb)
        return int(counters.PeakWorkingSetSize / (1024 * 1024)) if ok else 0
    except Exception:
        return 0


def free_disk_mb(path=None):
    try:
        return int(shutil.disk_usage(path or ROOT).free / (1024 * 1024))
    except Exception:
        return 0


def hf_cache_dir():
    for var in ("HF_HOME", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(var):
            return os.environ[var]
    return os.path.join(os.path.expanduser("~"), ".cache", "huggingface")


def is_cached(name):
    """Best-effort check for weights already on disk.

    Conservative on purpose: a false 'not cached' costs a permission prompt,
    while a false 'cached' would start a silent download.
    """
    spec = MODELS.get(name, {})
    repo = spec.get("repo", "")
    root = os.path.join(hf_cache_dir(), "hub")
    if not os.path.isdir(root):
        return False
    slug = "models--" + repo.replace("/", "--")
    target = os.path.join(root, slug)
    if not os.path.isdir(target):
        return False
    for dirpath, _dirs, files in os.walk(target):
        for fn in files:
            if fn.endswith(".bin") and os.path.getsize(
                    os.path.join(dirpath, fn)) > 1024 * 1024:
                return True
    return False


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------

def load_wav(path):
    """16 kHz mono float32, and the clip duration in seconds."""
    with wave.open(path, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return a.astype(np.float32), (n / sr if sr else 0.0)


# --------------------------------------------------------------------------
# Running one candidate
# --------------------------------------------------------------------------

def run_model(name, records, prompt, terms, allow_download=False):
    """Load a candidate, transcribe every recording, score all three levels."""
    spec = MODELS.get(name, {"compute": "int8_float16", "mb": 0, "repo": name})
    cached = is_cached(name)
    if not cached and not allow_download:
        return {"name": name,
                "error": "not cached, and downloading was not permitted"}

    core.enable_cuda_dlls()
    from faster_whisper import WhisperModel

    base_vram = vram_used()
    device, compute = "cuda", spec.get("compute", "int8_float16")
    t0 = time.time()
    try:
        model = WhisperModel(name, device=device, compute_type=compute)
    except Exception as e:
        return {"name": name,
                "error": "load failed: %s: %s" % (type(e).__name__, str(e)[:80])}
    load_s = time.time() - t0

    # Warm up before accepting the model, and before timing anything. A CUDA
    # path that is going to fail fails here, on half a second of silence,
    # rather than in the middle of the run.
    try:
        list(model.transcribe(np.zeros(8000, dtype=np.float32),
                              language="en", beam_size=1)[0])
    except Exception as e:
        del model
        print("    warmup failed on GPU (%s), retrying on CPU"
              % type(e).__name__)
        device, compute = "cpu", "int8"
        try:
            model = WhisperModel(name, device=device, compute_type=compute)
            list(model.transcribe(np.zeros(8000, dtype=np.float32),
                                  language="en", beam_size=1)[0])
        except Exception as e2:
            return {"name": name,
                    "error": "unusable on GPU and CPU: %s" % type(e2).__name__}
    peak_vram = max(0, vram_used() - base_vram)

    rules = core.load_corrections()

    # Gate one in Section 21.3 asks what reaches the DOCUMENT, so the silence
    # clips are put through the same three defences the running app uses. The
    # floor is learned from the speech recordings first, exactly as it is
    # learned from real use, because a floor calibrated on nothing rejects
    # nothing.
    voice = core.VoiceLevel()
    for rec in records:
        if rec.get("category") == "silence":
            continue
        path = C.audio_path(rec)
        if not os.path.exists(path):
            continue
        audio, _d = load_wav(path)
        voice.learn(core.analyse(audio, threshold=None).rms)

    rows = []
    for rec in records:
        path = C.audio_path(rec)
        if not os.path.exists(path):
            continue
        audio, dur = load_wav(path)

        t0 = time.time()
        try:
            segs, _info = model.transcribe(
                audio, language="en", beam_size=1, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=prompt)
            raw = " ".join(s.text.strip() for s in segs).strip()
        except Exception as e:
            # Same contract as production: a GPU failure costs latency, never
            # the recording. The audio is still in hand, so replay it on CPU.
            if device == "cpu":
                raise
            print("    GPU failed on %s (%s), replaying on CPU"
                  % (rec["id"], type(e).__name__))
            del model
            device, compute = "cpu", "int8"
            model = WhisperModel(name, device=device, compute_type=compute)
            segs, _info = model.transcribe(
                audio, language="en", beam_size=1, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=prompt)
            raw = " ".join(s.text.strip() for s in segs).strip()
        seconds = time.time() - t0

        # A silence clip the product would never type is not a false
        # positive. Speech is deliberately not gated here: accuracy and
        # insertion are separate questions, and running speech through the
        # filter would let it hide a weak recogniser.
        if rec.get("category") == "silence":
            look = core.analyse(audio, threshold=None)
            background, _floor = voice.is_background(look.rms)
            if (background or not look.has_speech()
                    or core.looks_hallucinated(raw, look.speech_seconds)):
                raw = ""

        corrected, _fired = core.apply_corrections(raw, rules)
        corrected, _snapped = core.fuzzy_snap(corrected, terms)

        final = corrected
        try:
            import dictate_polish
            polished = dictate_polish.polish(corrected)
            final = polished[0] if isinstance(polished, tuple) else polished
        except Exception:
            pass

        rows.append(S.score_one(rec, raw, corrected, final,
                                seconds=seconds, audio_seconds=dur))

    ram = peak_ram_mb()
    del model

    summary = S.aggregate(rows)
    return {
        "name": name,
        "device": device,
        "compute": compute,
        "cached_before_run": cached,
        "resources": {"vram": peak_vram, "ram": ram, "load_s": load_s},
        "summary": summary,
        "rows": rows,
    }


# --------------------------------------------------------------------------

def print_download_plan(names, allow_download):
    """Report size and headroom before anything is fetched. Stage 4 rule 1."""
    missing = [n for n in names if not is_cached(n)]
    print("\n  model cache: %s" % hf_cache_dir())
    for n in names:
        spec = MODELS.get(n, {})
        state = "cached" if is_cached(n) else "NOT cached"
        print("    %-18s %-11s ~%5d MB   %s"
              % (n, state, spec.get("mb", 0), spec.get("note", "")))
    if not missing:
        print("\n  nothing to download.")
        return True

    need = sum(MODELS.get(n, {}).get("mb", 0) for n in missing)
    disk = free_disk_mb()
    gpu = vram_free()
    print("\n  %d model(s) would be downloaded: %s"
          % (len(missing), ", ".join(missing)))
    print("  estimated download        ~%d MB" % need)
    print("  free disk                  %d MB   %s"
          % (disk, "ok" if disk > need * 2 else "TIGHT"))
    print("  free GPU memory            %d MB   %s"
          % (gpu, "ok" if gpu > 1200 else "TIGHT, expect CPU fallback"))
    if not allow_download:
        print("\n  Not downloading. Re-run with --allow-download to permit it.")
        return False
    if disk < need * 2:
        print("\n  Refusing: less than twice the download size is free.")
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_CANDIDATES))
    ap.add_argument("--allow-download", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="report cache and headroom, then stop")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    names = [m.strip() for m in args.models.split(",") if m.strip()]

    if args.check:
        print_download_plan(names, args.allow_download)
        records, problems = C.load()
        print("\n  corpus: %d usable recording(s)" % len(records))
        if problems:
            print("  %d problem(s), first few:" % len(problems))
            for p in problems[:5]:
                print("    %s" % p)
        if records:
            cov = C.coverage(records)
            empty = [c for c, n in cov.items() if n == 0]
            if empty:
                print("  no recordings yet for: %s" % ", ".join(empty))
        return 0

    records, problems = C.load()
    # A take that is the wrong audio scores as a catastrophic model failure.
    # Two of the first nine recordings were a microphone test and a minute of
    # unrelated talking, and they moved a category average from roughly 8
    # percent word error to 103. Excluding them loudly is honest; including
    # them silently is not.
    flagged = C.suspect_takes(records)
    if flagged:
        bad_ids = {rid for rid, _why in flagged}
        print("\n  EXCLUDED %d take(s) whose audio does not match the phrase:"
              % len(flagged))
        for rid, why in flagged:
            print("    %-18s %s" % (rid, why))
        print("    Re-record with:  python eval/record.py --only <id>")
        records = [r for r in records if r["id"] not in bad_ids]
    if not records:
        print("\n  No recordings found in %s" % C.CORPUS_PATH)
        print("  Record them first:  python eval/record.py\n")
        for p in problems[:3]:
            print("    %s" % p)
        return 1
    if problems:
        print("  note: %d corpus problem(s) skipped" % len(problems))

    if not print_download_plan(names, args.allow_download):
        return 1

    terms = core.load_vocabulary()
    prompt, used, _dropped = core.build_prompt(terms)
    print("\n  %d recordings, %d vocabulary terms in the prompt\n"
          % (len(records), len(used)))

    results = []
    for name in names:
        print("  running %s ..." % name)
        r = run_model(name, records, prompt, terms,
                      allow_download=args.allow_download)
        results.append(r)
        if "error" in r:
            print("    FAILED: %s\n" % r["error"])
        else:
            s = r["summary"]
            print("    WER raw %.1f%%, final %.1f%%, %d MB VRAM, %.1fx realtime\n"
                  % (100 * s["wer_raw"], 100 * s["wer_final"],
                     r["resources"]["vram"], s["rtf"]))

    for r in results:
        if "error" in r:
            continue
        print("")
        print(S.report(r["summary"], name="%s (%s, %s)"
                       % (r["name"], r["device"], r["compute"]),
                       resources=r["resources"]))

    ok = [r for r in results if "error" not in r]
    if len(ok) > 1:
        base = ok[0]
        print("\n\n  against %s, the current production model:" % base["name"])
        print("  %-18s %11s %11s %10s %9s"
              % ("", "WER raw", "WER final", "VRAM", "speed"))
        for r in ok[1:]:
            bs, rs = base["summary"], r["summary"]
            print("  %-18s %+10.1f %+10.1f %+9d MB %8.2fx"
                  % (r["name"],
                     100 * (rs["wer_raw"] - bs["wer_raw"]),
                     100 * (rs["wer_final"] - bs["wer_final"]),
                     r["resources"]["vram"] - base["resources"]["vram"],
                     (rs["rtf"] / bs["rtf"]) if bs["rtf"] else 0.0))
        print("\n  Negative WER is better. A candidate that only improves the")
        print("  final column is being carried by the correction layers, not")
        print("  by better listening.")

    os.makedirs(RESULTS, exist_ok=True)
    out = args.out or os.path.join(
        RESULTS, "bench_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "records": len(records),
                   "results": results}, f, indent=2, ensure_ascii=False)
    print("\n  written: %s" % out)
    print("  (inside eval/private/, which is gitignored)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
