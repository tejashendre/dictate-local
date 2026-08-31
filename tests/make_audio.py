"""Generate test WAVs with the local Windows SAPI voices. Fully offline.

Synthetic speech is not human speech, so these numbers are a regression signal,
not an accuracy guarantee. The final check is always the user's own voice.

    python tests/make_audio.py            build the corpus
    python tests/make_audio.py --calibrate    show SAPI rate -> WPM mapping
"""
import os
import sys
import wave
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "audio")
PS = r"powershell.exe"

# SAPI rate that lands nearest the measured speaking rate (120 WPM average,
# 147 peak, over 16,989 words). Calibrated by --calibrate below.
DEFAULT_RATE = -3          # measured at 141 WPM, inside the 120-147 band
DEFAULT_VOICE = "Microsoft David Desktop"


def synth(text, path, rate=DEFAULT_RATE, voice=DEFAULT_VOICE):
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SelectVoice('{voice}'); "
        f"$s.Rate = {rate}; "
        f"$s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak('{text}'); "
        "$s.Dispose()"
    )
    r = subprocess.run([PS, "-NoProfile", "-Command", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("SAPI failed: %s" % r.stderr.strip())
    return path


def wav_seconds(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def wpm(text, path):
    return len(text.split()) / (wav_seconds(path) / 60.0)


def calibrate():
    sample = (
        "This is a calibration sentence used to measure how quickly the "
        "synthetic voice speaks so that the test corpus matches a real "
        "speaking rate of roughly one hundred and twenty words per minute "
        "on average with peaks near one hundred and forty seven."
    )
    os.makedirs(OUT, exist_ok=True)
    print("  words in sample: %d" % len(sample.split()))
    print("  rate   seconds   WPM")
    for rate in (-3, -2, -1, 0, 1, 2):
        p = os.path.join(OUT, "_cal.wav")
        synth(sample, p, rate=rate)
        print("  %4d   %6.2f   %6.1f" % (rate, wav_seconds(p), wpm(sample, p)))
    os.remove(os.path.join(OUT, "_cal.wav"))


def build():
    sys.path.insert(0, HERE)
    from phrases import VOCAB_PHRASES, CONTROL_PHRASES
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for tag, items in (("vocab", VOCAB_PHRASES), ("control", CONTROL_PHRASES)):
        for i, (text, terms) in enumerate(items):
            path = os.path.join(OUT, "%s_%02d.wav" % (tag, i))
            synth(text, path)
            rows.append((os.path.basename(path), wpm(text, path), text))
            print("  %-14s %5.1f WPM  %s" % (os.path.basename(path), rows[-1][1], text[:52]))
    avg = sum(r[1] for r in rows) / len(rows)
    print("\n  %d files, average %.1f WPM" % (len(rows), avg))


if __name__ == "__main__":
    if "--calibrate" in sys.argv:
        calibrate()
    else:
        build()
