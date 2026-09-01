"""Telling your voice from the room.

Silero VAD answers "is this speech", not "is this the person at the mic".
Measured before this guard existed, it passed a television at every level:

    background at 100% of your level  ->  transcribed
    background at  35%                ->  transcribed
    background at   8%                ->  transcribed

Content cannot separate them; a news reader is speaking just as validly as you
are. Loudness can, because a voice across the room arrives far quieter at your
microphone than your own mouth.

The two things this has to get right, and the second matters more:

  1. Background during a pause is rejected.
  2. YOU are never rejected - including when you speak softly, and including
     the first few phrases before anything has been learned.

    python tests/test_noise.py
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
core.enable_cuda_dlls()

AUDIO = os.path.join(HERE, "audio")
NEAR = ("I applied to Zalando through Naukri last Tuesday and the recruiter "
        "replied the next morning.")
FAR = ("Tonight on the news, markets closed higher after a long session of "
       "trading and analysts expect more gains tomorrow.")


def check(name, ok, detail=""):
    """Detail is the FAILURE explanation, so it must not print on success -
    "ok ... not rejected" reads as the opposite of what happened."""
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if (detail and not ok) else ""))
    return bool(ok)


def synth(text, path, voice="Microsoft David Desktop"):
    if os.path.exists(path):
        return path
    text = text.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{voice}'); $s.Rate=-3; "
          f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                   capture_output=True)
    return path


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
    return a.astype(np.float32)


def main():
    os.makedirs(AUDIO, exist_ok=True)
    near = load(synth(NEAR, os.path.join(AUDIO, "voice_near.wav")))
    far = load(synth(FAR, os.path.join(AUDIO, "voice_far.wav"),
                     "Microsoft Zira Desktop"))
    ok_all = True

    print("\n  1. speech loudness is measured over speech, not silence")
    padded = np.concatenate([np.zeros(16000 * 3, dtype=np.float32), near])
    bare, with_gap = core.speech_rms(near), core.speech_rms(padded)
    ok_all &= check("3s of added silence barely moves the reading",
                    abs(bare - with_gap) / max(bare, 1e-9) < 0.15,
                    "%.4f vs %.4f" % (bare, with_gap))

    print("\n  2. it refuses to judge before it knows you")
    fresh = core.VoiceLevel()
    ok_all &= check("nothing is rejected on the first phrase",
                    fresh.is_background(0.0001)[0] is False)
    for _ in range(core.GATE_MIN_SAMPLES):
        fresh.learn(core.speech_rms(near))
    ok_all &= check("it starts judging once it has heard you %d times"
                    % core.GATE_MIN_SAMPLES,
                    fresh.level is not None,
                    "your level %.4f" % (fresh.level or 0))

    print("\n  3. background during a pause is rejected")
    for atten in (0.35, 0.25, 0.15, 0.08):
        r = core.speech_rms(far * atten)
        bg, floor = fresh.is_background(r)
        ok_all &= check("television at %3.0f%% of you (RMS %.4f) rejected, "
                        "floor %.4f" % (atten * 100, r, floor or 0), bg,
                        "NOT rejected")

    print("\n  4. YOU are never rejected")
    ok_all &= check("your normal voice", not fresh.is_background(
        core.speech_rms(near))[0])
    for soft in (0.7, 0.55, 0.45):
        r = core.speech_rms(near * soft)
        ok_all &= check("you at %2.0f%% of usual (RMS %.4f) accepted"
                        % (soft * 100, r), not fresh.is_background(r)[0],
                        "REJECTED you")

    print("\n  5. BOTH paths are gated, not just one")
    # Three separate guards have now been added to the batch path and missed
    # in streaming, which is the DEFAULT path. This asserts they share one
    # helper rather than each growing their own copy.
    import dictate_stream
    app = open(os.path.join(ROOT, "dictate.py"), encoding="utf-8").read()
    strm = open(os.path.join(ROOT, "dictate_stream.py"), encoding="utf-8").read()
    ok_all &= check("batch path calls the shared gate",
                    "_gate_phrase(audio)" in app)
    ok_all &= check("streaming path is given the shared gate",
                    "gate=_gate_phrase" in app)
    ok_all &= check("StreamingSession actually uses it", "self.gate" in strm)

    calls = {"n": 0}

    class _Fake:
        device = "cpu"

        def transcribe(self, audio, prompt=None):
            calls["n"] += 1
            return "should not have been transcribed"

    sess = dictate_stream.StreamingSession(_Fake(), gate=lambda a: True)
    sess.feed(near)
    out = sess.finish()
    ok_all &= check("a gated phrase is never transcribed at all",
                    out == [] and calls["n"] == 0,
                    "emitted %s, transcribed %d times" % (out, calls["n"]))
    ok_all &= check("and it is counted", sess.dropped_background == 1)

    print("\n  6. the median ignores one-off shouts and whispers")
    v = core.VoiceLevel()
    for x in (0.08, 0.09, 0.085, 0.095, 0.09):
        v.learn(x)
    steady = v.level
    v.learn(0.9)        # one shout
    v.learn(0.001)      # one whisper
    ok_all &= check("level barely moves after both",
                    abs(v.level - steady) / steady < 0.2,
                    "%.4f -> %.4f" % (steady, v.level))

    print("\n  %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
