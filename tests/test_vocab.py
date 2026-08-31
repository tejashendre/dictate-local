"""Measure what the vocabulary prompt actually buys.

Transcribes the corpus twice - once with no initial_prompt, once with the one
built from vocabulary.txt - and reports term recall on the vocabulary set plus
word error rate on the control set.

The control set is the one that matters for regressions: a prompt that fixes
proper nouns but degrades ordinary speech is a bad trade.

    python tests/make_audio.py     (once, to build the corpus)
    python tests/test_vocab.py
"""
import os
import re
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import dictate_core as core           # noqa: E402
core.enable_cuda_dlls()               # must run before faster_whisper imports
from faster_whisper import WhisperModel   # noqa: E402
from phrases import VOCAB_PHRASES, CONTROL_PHRASES  # noqa: E402

AUDIO = os.path.join(HERE, "audio")
MODEL_NAME = os.environ.get("DICTATE_MODEL", "small.en")


def load_wav(path):
    with wave.open(path, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return a, n / sr


def normalise(s):
    return re.sub(r"[^a-z0-9\s]", " ", s.lower()).split()


def wer(ref, hyp):
    """Levenshtein distance over words, divided by reference length."""
    r, h = normalise(ref), normalise(hyp)
    if not r:
        return 0.0
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=np.int32)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + cost)
    return d[len(r), len(h)] / len(r)


def run(model, path, prompt, rules=()):
    audio, dur = load_wav(path)
    t0 = time.time()
    segs, _ = model.transcribe(audio, language="en", beam_size=1,
                               vad_filter=True, condition_on_previous_text=False,
                               initial_prompt=prompt)
    text = " ".join(s.text.strip() for s in segs).strip()
    if rules:
        text, _fired = core.apply_corrections(text, rules)
        text, _snapped = core.fuzzy_snap(text, core.load_vocabulary())
    return text, time.time() - t0, dur


def main():
    if not os.path.isdir(AUDIO) or not os.listdir(AUDIO):
        print("  no corpus. run:  python tests/make_audio.py")
        return 1

    terms = core.load_vocabulary()
    rules = core.load_corrections()
    prompt, used, dropped = core.build_prompt(terms)
    device, compute = core.plan_device()[:2]
    print("  model  : %s on %s (%s)" % (MODEL_NAME, device, compute))
    print("  prompt : %d terms -> %s\n" % (len(used), (prompt or "")[:60] + "..."))

    model = WhisperModel(MODEL_NAME, device=device, compute_type=compute)

    results = {}
    for label, use_prompt, use_rules in (("OFF", None, ()),
                                        ("ON", prompt, ()),
                                        ("ON+FIX", prompt, rules)):
        hits = misses = 0
        missed_terms = []
        wers, times, durs = [], [], []

        for i, (ref, want) in enumerate(VOCAB_PHRASES):
            p = os.path.join(AUDIO, "vocab_%02d.wav" % i)
            if not os.path.exists(p):
                continue
            hyp, took, dur = run(model, p, use_prompt, use_rules)
            times.append(took); durs.append(dur)
            for term in want:
                if term.lower() in hyp.lower():
                    hits += 1
                else:
                    misses += 1
                    missed_terms.append((term, hyp))

        for i, (ref, _) in enumerate(CONTROL_PHRASES):
            p = os.path.join(AUDIO, "control_%02d.wav" % i)
            if not os.path.exists(p):
                continue
            hyp, took, dur = run(model, p, use_prompt, use_rules)
            times.append(took); durs.append(dur)
            wers.append(wer(ref, hyp))

        total = hits + misses
        results[label] = dict(
            recall=hits / total if total else 0.0,
            hits=hits, total=total, missed=missed_terms,
            control_wer=sum(wers) / len(wers) if wers else 0.0,
            rtf=sum(durs) / sum(times) if times else 0.0,
        )

        print("  prompt %-7s  vocabulary terms correct %2d/%2d (%.0f%%)   "
              "control WER %.1f%%   %.1fx realtime"
              % (label, hits, total, 100 * results[label]["recall"],
                 100 * results[label]["control_wer"], results[label]["rtf"]))

    off, on = results["OFF"], results["ON+FIX"]
    print("\n  " + "-" * 68)
    print("  term recall   %.0f%%  ->  %.0f%%   (%+d terms)   [OFF -> prompt+corrections]"
          % (100 * off["recall"], 100 * on["recall"], on["hits"] - off["hits"]))
    print("  control WER   %.1f%%  ->  %.1f%%   %s"
          % (100 * off["control_wer"], 100 * on["control_wer"],
             "no regression" if on["control_wer"] <= off["control_wer"] + 1e-9
             else "REGRESSION - the prompt is hurting plain speech"))

    if on["missed"]:
        print("\n  still wrong with the prompt on:")
        for term, hyp in on["missed"]:
            print("    %-24s heard: %s" % (term, hyp))

    ok = (on["hits"] >= off["hits"]) and (on["control_wer"] <= off["control_wer"] + 0.02)
    print("\n  %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
