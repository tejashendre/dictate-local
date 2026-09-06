"""Record the real-voice corpus, one phrase at a time.

    python eval/record.py                 record whatever is not done yet
    python eval/record.py --category names
    python eval/record.py --list          show progress and stop
    python eval/record.py --verify        check the corpus without recording

Resumable by design. It reads what has already been recorded and offers only
what is missing, so this can be done in four sittings of five minutes rather
than one of twenty, and a laptop that goes to sleep halfway through costs
nothing.

Everything it writes goes to eval/private/, which is gitignored. No audio and
no transcript of a real person leaves this machine.
"""
import argparse
import io
import os
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

# Never let a run of this touch the live settings file.
os.environ.setdefault("DICTATE_TESTING", "1")

import corpus as C                                   # noqa: E402
from phrases_english import PHRASES                  # noqa: E402

SAMPLE_RATE = 16000

# Which noise condition each category is recorded under, unless overridden.
DEFAULT_NOISE = {
    "fan_noise": "fan",
    "tv_distant": "tv",
    "quiet": "quiet",
}

CATEGORY_HELP = {
    "fast": "Read this quickly, the way you talk to someone already following you.",
    "long": "One breath if you can. Do not slow down for the tool.",
    "fillers": "Say the um and the uh out loud. They are the point.",
    "false_starts": "Abandon the first clause naturally, do not act it.",
    "self_correction": "Correct yourself mid-sentence, as you actually would.",
    "fan_noise": "Start something heavy first so the fan is audible.",
    "tv_distant": "Have a television or another voice going across the room.",
    "silence": "Say nothing at all. Let the room be the recording.",
    "urls_emails": "Read it as you would say it aloud, not as it is written.",
}


def record_until_enter(device=None):
    """Capture from the default microphone until Enter is pressed again."""
    import numpy as np
    import sounddevice as sd

    frames = []

    def cb(indata, _n, _t, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", callback=cb, blocksize=1024,
                            device=device)
    t0 = time.time()
    with stream:
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    seconds = time.time() - t0
    if not frames:
        return np.zeros(0, dtype="float32"), 0.0
    return np.concatenate(frames, axis=0).flatten().astype("float32"), seconds


def save_wav(path, audio):
    """16 kHz mono 16-bit, the format the model wants and the tests use."""
    import numpy as np
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def level_of(audio):
    """Rough RMS, so a dead microphone is caught at record time not at score time."""
    import numpy as np
    if audio is None or len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))


def phrase_id(index, category):
    return "%s_%03d" % (category, index)


def already_done(path=None):
    """Ids already in the corpus, so a resumed run skips them."""
    records, _problems = C.load(path, strict_audio=False)
    return {r["id"] for r in records}


def default_microphone():
    try:
        import sounddevice as sd
        return str(sd.query_devices(kind="input")["name"]).strip()
    except Exception:
        return "unknown"


def show_progress(done):
    counts = {}
    for cat, *_ in PHRASES:
        counts.setdefault(cat, [0, 0])
        counts[cat][1] += 1
    for i, (cat, *_rest) in enumerate(PHRASES):
        if phrase_id(i, cat) in done:
            counts[cat][0] += 1
    print("\n  corpus progress")
    total_done = total_all = 0
    for cat in C.CATEGORIES:
        if cat not in counts:
            continue
        got, want = counts[cat]
        total_done += got
        total_all += want
        bar = "#" * got + "." * (want - got)
        print("    %-16s %2d/%-2d  %s" % (cat, got, want, bar))
    print("    %-16s %2d/%-2d" % ("TOTAL", total_done, total_all))
    return total_done, total_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", help="record only this category")
    ap.add_argument("--list", action="store_true", help="show progress, do not record")
    ap.add_argument("--verify", action="store_true", help="validate the corpus")
    ap.add_argument("--device", help="input device name or index")
    ap.add_argument("--redo", action="store_true", help="re-record items already done")
    ap.add_argument("--only", help="re-record just this id, replacing it")
    args = ap.parse_args()

    if args.verify:
        records, problems = C.load()
        print("\n  %d usable records" % len(records))
        if problems:
            print("  %d problems:" % len(problems))
            for p in problems[:20]:
                print("    %s" % p)
        else:
            print("  no problems found")

        # A record can be perfectly well formed and still be the wrong audio.
        # Two takes in the first nine were a microphone test and a minute of
        # ordinary talking, and they moved a category average from about 8
        # percent word error to 103, which reads as a model failure.
        suspect = C.suspect_takes(records)
        slow = C.slow_takes(records)
        if slow:
            print("\n  %d take(s) are valid but long, so scoring is slower:"
                  % len(slow))
            for rid, secs in slow:
                print("    %-18s %.0fs" % (rid, secs))
            print("    Long silence is a harder test, not a worse one.")
        if suspect:
            print("\n  %d take(s) look like the wrong audio:" % len(suspect))
            for rid, why in suspect:
                print("    %-18s %s" % (rid, why))
            print("\n  Replace one with:  python eval/record.py --only %s"
                  % suspect[0][0])

        cov = C.coverage(records)
        thin = [c for c, n in cov.items() if n == 0]
        if thin:
            print("\n  categories with nothing recorded yet:")
            print("    %s" % ", ".join(thin))
        return 0 if not (problems or suspect) else 1

    done = already_done()
    if args.list:
        show_progress(done)
        return 0

    device = args.device
    if device is not None and device.isdigit():
        device = int(device)
    mic = default_microphone()

    todo = []
    for i, (cat, said, want, names, numbers) in enumerate(PHRASES):
        if args.category and cat != args.category:
            continue
        pid = phrase_id(i, cat)
        if args.only:
            if pid != args.only:
                continue
        elif pid in done and not args.redo:
            continue
        todo.append((i, cat, said, want, names, numbers))

    show_progress(done)
    if not todo:
        print("\n  nothing left to record."
              " Use --redo to replace, or --verify to check.\n")
        return 0

    print("\n  microphone : %s" % mic)
    print("  %d phrase(s) to record" % len(todo))
    print("\n  Enter starts recording, Enter again stops it.")
    print("  Then: Enter accepts, r re-records, s skips, q quits and saves.\n")
    print("  Speak normally. Do not perform. If you stumble, that is real")
    print("  data, so keep it unless the take is genuinely unusable.\n")

    saved = 0
    for i, cat, said, want, names, numbers in todo:
        pid = phrase_id(i, cat)
        print("  " + "-" * 66)
        print("  [%s]  %s" % (pid, cat))
        if cat in CATEGORY_HELP:
            print("  note: %s" % CATEGORY_HELP[cat])
        if cat == "silence":
            print("\n  SAY NOTHING.\n")
        else:
            print("\n  say:  %s\n" % said)
            if want != said:
                print("  (the tool should end up writing: %s)\n" % want)

        while True:
            try:
                input("  Enter to start ... ")
            except (EOFError, KeyboardInterrupt):
                print("\n  stopped.")
                return 0
            print("  RECORDING. Enter to stop ... ", end="", flush=True)
            audio, seconds = record_until_enter(device)
            rms = level_of(audio)
            print("  %.1fs, level %.4f" % (seconds, rms))

            if cat != "silence" and rms < 0.001:
                print("  WARNING: that was almost silent. Check the microphone.")
            if cat == "silence" and rms > 0.02:
                print("  WARNING: that is louder than a silent room should be.")

            try:
                choice = input("  [Enter] keep   [r] again   [s] skip   [q] quit: ")
            except (EOFError, KeyboardInterrupt):
                print("\n  stopped.")
                return 0
            choice = (choice or "").strip().lower()
            if choice == "r":
                continue
            if choice == "s":
                break
            if choice == "q":
                print("\n  saved %d recording(s) this session.\n" % saved)
                return 0

            rel = os.path.join("audio", "%s.wav" % pid)
            save_wav(os.path.join(C.PRIVATE, rel), audio)
            rec = C.new_record(
                rec_id=pid,
                audio=rel.replace("\\", "/"),
                said=said,
                want=want,
                category=cat,
                microphone=mic,
                noise=DEFAULT_NOISE.get(cat, "quiet"),
                protected_names=names,
                protected_numbers=numbers,
                notes="",
            )
            if pid in done:
                C.drop(pid)
            try:
                C.append(rec)
                saved += 1
                print("  saved.\n")
            except C.CorpusError as e:
                print("  NOT SAVED, the record is invalid: %s\n" % e)
            break

    print("\n  done. %d recording(s) saved this session." % saved)
    print("  Check it with:  python eval/record.py --verify\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
