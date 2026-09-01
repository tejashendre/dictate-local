"""Streaming, tested by replaying real speech in chunks.

The properties that matter are not "does it produce text" but:

  1. Text appears BEFORE the audio ends. Otherwise it is not streaming.
  2. Nothing is ever retyped or unsaid. Emissions are append-only.
  3. The streamed result says the same thing as a single batch transcription.
  4. Silence produces nothing at all.
  5. Long unbroken speech still emits, via the stable prefix path.

    python tests/test_stream.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_core as core        # noqa: E402
core.enable_cuda_dlls()
import dictate_stream as stream    # noqa: E402

AUDIO = os.path.join(HERE, "audio")
CHUNK_MS = 64          # about what sounddevice delivers at blocksize 1024


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


def synth(text, path, rate=-3):
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.SelectVoice('Microsoft David Desktop'); $s.Rate=%d; "
          "$s.SetOutputToWaveFile('%s'); $s.Speak('%s'); $s.Dispose()"
          % (rate, path, text))
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                   capture_output=True)
    return path


def replay(session, audio, note_progress=True):
    """Feed audio in real-capture-sized chunks, polling as we go.

    Returns (emissions, fraction_of_audio_fed_at_first_emission).
    """
    step = int(SAMPLE := 16000 * CHUNK_MS / 1000)
    emissions = []
    first_at = None
    for i in range(0, len(audio), step):
        session.feed(audio[i:i + step])
        got = session.poll()
        if got:
            if first_at is None and note_progress:
                first_at = (i + step) / len(audio)
            emissions.extend(got)
    emissions.extend(session.finish())
    return emissions, first_at


def words(s):
    return " ".join(s.lower().replace(",", " ").replace(".", " ").split())


def main():
    terms = core.load_vocabulary()
    prompt, _, _ = core.build_prompt(terms)
    t = core.Transcriber("small.en", on_event=lambda m: print("    " + m)).load()
    print("  running on %s\n" % t.device)
    ok_all = True

    # ---- 1. a pause mid-sentence must produce text before the end --------
    print("  1. text appears before the audio ends")
    long_path = os.path.join(AUDIO, "stream_long.wav")
    if not os.path.exists(long_path):
        synth("I applied to Zalando through Naukri last Tuesday. "
              "The recruiter replied the next morning. "
              "They asked for my Arbeitszeugnis and a short cover note. "
              "I sent both before lunch.", long_path)
    audio = load_wav(long_path)
    s = stream.StreamingSession(t, prompt=prompt)
    emissions, first_at = replay(s, audio)
    joined = " ".join(emissions)
    ok = check("emitted more than once (%d chunks)" % len(emissions),
               len(emissions) > 1)
    ok &= check("first text arrived at %.0f%% of the audio"
                % (100 * (first_at or 1.0)),
                first_at is not None and first_at < 0.9)
    ok_all &= ok
    for e in emissions:
        print("       + %r" % e)

    # ---- 2. append-only: nothing retyped --------------------------------
    print("\n  2. emissions are append-only, nothing is ever unsaid")
    ok = check("no emission is empty", all(e.strip() for e in emissions))
    # Each emission must be new text, never a restatement of the previous one.
    dupes = [e for i, e in enumerate(emissions)
             if e.strip().lower() in " ".join(emissions[:i]).lower() and i > 0]
    ok &= check("no emission repeats earlier text", not dupes, str(dupes[:2]))
    ok_all &= ok

    # ---- 3. streamed content matches a single batch pass ----------------
    print("\n  3. streamed text says the same thing as one batch pass")
    batch = t.transcribe(audio, prompt=prompt)
    sw, bw = set(words(joined).split()), set(words(batch).split())
    overlap = len(sw & bw) / max(1, len(bw))
    ok = check("word overlap with batch %.0f%%" % (100 * overlap),
               overlap >= 0.85)
    print("       batch : %s" % batch)
    print("       stream: %s" % joined)
    ok_all &= ok

    # ---- 4. silence emits nothing ---------------------------------------
    print("\n  4. silence produces nothing")
    s2 = stream.StreamingSession(t, prompt=prompt)
    sil = np.zeros(16000 * 4, dtype=np.float32)
    em2, _ = replay(s2, sil, note_progress=False)
    ok = check("no text from 4s of silence", em2 == [], str(em2))
    ok &= check("buffer did not grow unbounded", s2.seconds < 3.0,
                "%.1fs" % s2.seconds)
    ok_all &= ok

    # ---- 5. long unbroken speech uses the stable-prefix path ------------
    print("\n  5. long unbroken speech still emits (stable prefix)")
    s3 = stream.StreamingSession(t, prompt=prompt, pause_s=99.0,
                                 force_after_s=4.0)
    em3, first3 = replay(s3, audio)
    ok = check("emitted despite never seeing a pause", len(em3) > 1,
               "%d chunks" % len(em3))
    ok &= check("first text before the end",
                first3 is not None and first3 < 0.9,
                "at %.0f%%" % (100 * (first3 or 1.0)))
    for e in em3:
        print("       + %r" % e)
    ok_all &= ok

    # ---- 6. stable_prefix unit behaviour --------------------------------
    print("\n  6. stable prefix logic")
    ok = check("identical passes agree fully",
               stream.stable_prefix(["a", "b", "c"], ["a", "b", "c"]) == 3)
    ok &= check("divergence stops the commit",
                stream.stable_prefix(["a", "b", "x"], ["a", "b", "y"]) == 2)
    ok &= check("no previous pass commits nothing",
                stream.stable_prefix([], ["a", "b"]) == 0)
    ok_all &= ok

    print("\n  %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
