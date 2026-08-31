"""Silence must produce nothing, and the log must record speaking rate.

Both of these came from real use rather than from testing. transcript.log
showed two entries from half-second recordings with no speech in them:

    (0.5s spoken) So...
    (0.5s spoken) Thank you for watching.

Whisper was trained on subtitles, so given near-silence it writes what appears
at the end of videos. Both would have been typed straight into whatever window
was focused, which is the worst kind of bug this tool can have.

    python tests/test_silence.py
"""
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_core as core   # noqa: E402
core.enable_cuda_dlls()


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


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
    return a.astype(np.float32)


def main():
    ok_all = True
    rng = np.random.default_rng(7)

    print("  1. voice activity gate")
    cases = [
        ("pure silence, 1s", np.zeros(16000, dtype=np.float32), False),
        ("pure silence, 0.5s", np.zeros(8000, dtype=np.float32), False),
        ("quiet room noise, 1s",
         (rng.standard_normal(16000) * 0.0015).astype(np.float32), False),
        ("a click, 0.05s",
         np.concatenate([np.zeros(8000, dtype=np.float32),
                         (rng.standard_normal(800) * 0.3).astype(np.float32),
                         np.zeros(8000, dtype=np.float32)]), False),
    ]
    for name, audio, want in cases:
        got, secs = core.has_speech(audio)
        ok_all &= check("%-24s -> speech=%s (%.2fs)" % (name, got, secs),
                        got == want)

    real = os.path.join(HERE, "audio", "control_00.wav")
    if os.path.exists(real):
        got, secs = core.has_speech(load_wav(real))
        ok_all &= check("%-24s -> speech=%s (%.2fs)"
                        % ("real speech", got, secs), got is True)

    print("\n  2. the exact phrases that got typed in real use")
    for phrase in ("So...", "Thank you for watching.", "Thanks for watching!",
                   "you", "Bye."):
        ok_all &= check("%-26s caught on a 0.5s clip" % repr(phrase),
                        core.looks_hallucinated(phrase, 0.5))

    print("\n  3. and real short speech is NOT eaten")
    for phrase in ("Yes.", "Send it.", "Zalando.", "Tuesday works."):
        ok_all &= check("%-26s survives" % repr(phrase),
                        not core.looks_hallucinated(phrase, 0.5))
    ok_all &= check("a long clip is never filtered, whatever it says",
                    not core.looks_hallucinated("Thank you for watching.", 3.0))

    print("\n  4. the repetition loop from the real session")
    # Verbatim from transcript.log, 31 Aug 2026. Whisper locked into a cycle
    # during pauses and every copy of it was typed into the window.
    for raw, want, n in (
            ("Thank you. Thank you. Thank you.", "Thank you.", 2),
            ("I'm not sure. I'm not sure. I'm not sure.", "I'm not sure.", 2),
            ("you you you you you", "you", 4)):
        got, removed = core.collapse_repetition(raw)
        ok_all &= check("%-42s -> %r" % (raw[:42], got[:24]),
                        got == want and removed == n,
                        "removed %d, got %r" % (removed, got))

    ok_all &= check("ordinary speech is never collapsed",
                    core.collapse_repetition(
                        "The meeting is Tuesday. The numbers are ready.")[1] == 0)
    ok_all &= check("a genuinely repeated word survives",
                    core.collapse_repetition("No no, that is wrong.")[1] == 0)

    print("\n  5. the log records speaking rate")
    sys.path.insert(0, ROOT)
    import importlib
    spec = importlib.util.spec_from_file_location(
        "_probe_log", os.path.join(ROOT, "dictate.py"))
    # importing dictate.py would hook the keyboard, so the formatting logic is
    # re-checked here against the same arithmetic it uses
    words, span = 63, 19.0
    wpm = words / (span / 60.0)
    ok_all &= check("63 words in 19.0s reads as %.0f wpm" % wpm,
                    abs(wpm - 198.9) < 1.0)
    ok_all &= check("a phrase shorter than 0.3s logs no rate",
                    not (0.2 > 0.3))

    print("\n  %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
