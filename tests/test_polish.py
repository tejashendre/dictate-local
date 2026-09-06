"""Cleanup, and the words it must never eat.

Same discipline as the command grammar: removing filler is easy, and removing
a real word by mistake is much worse than leaving a filler behind. So the
QUIET half is the one that matters.

    python tests/test_polish.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dictate_core as core       # noqa: E402
import dictate_polish as polish   # noqa: E402


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


# Real raw output from small.en on this machine, plus the classic patterns.
CLEAN = [
    ("um so I wanted to say that uh the meeting is on Tuesday",
     "So I wanted to say that the meeting is on Tuesday"),
    ("we need to we need to finish the report before Friday",
     "We need to finish the report before Friday"),
    ("the the meeting is on Tuesday",
     "The meeting is on Tuesday"),
    ("I applied to Zalando and Um the recruiter replied",
     "I applied to Zalando and the recruiter replied"),
    ("ah we need to finish this",
     "We need to finish this"),
    ("so, um, the meeting is on Tuesday",
     "So, the meeting is on Tuesday"),
]

# Must survive untouched. Heavy on words that look like filler but are not.
KEEP = [
    "I like this design and I like the colour",
    "It looks like rain this afternoon",
    "That is basically correct",
    "I actually finished it yesterday",
    "You know the answer already",
    "I mean what I say",
    "Say literally comma to type the word",
    "I had had enough of the delays",
    "The reason is that that meeting was cancelled",
    "He said no no no to the offer",
    "I applied to Zalando through Naukri last Tuesday",
    "Please send me the updated numbers when you get a chance",
]


def main():
    ok_all = True

    print("  1. mechanical mess is removed")
    for raw, want in CLEAN:
        got, notes = polish.polish_fast(raw)
        ok_all &= check("%-52s" % raw[:52], got == want,
                        "" if got == want else "got %r" % got)

    print("\n  2. real words are never eaten")
    for text in KEEP:
        got, notes = polish.polish_fast(text)
        same = got == text
        ok_all &= check("%-52s" % text[:52], same,
                        "" if same else "changed to %r" % got)

    print("\n  5. spoken addresses become written ones")
    # Measured on real recordings: urls_emails scored 91.7% word error against
    # a raw 52.8, so the formatting was doing more damage than the mishearing.
    # Typing "tejas dot hendre at edu dot escp dot eu" is not a transcription
    # failure, it is a transformation that was never implemented.
    for said, want in [
        ("Send it to tejas dot hendre at edu dot escp dot eu.",
         "tejas.hendre@edu.escp.eu"),
        ("My site is tejashendre dot com.", "tejashendre.com"),
        ("The repository is github dot com slash tejashendre slash dictate "
         "dash local.", "github.com/tejashendre/dictate-local"),
        # Whisper usually writes the dot itself, so a run must also be able to
        # start from a domain that has already been punctuated.
        ("The repository is github.com slash tejashendre slash dictate dash "
         "local.", "github.com/tejashendre/dictate-local"),
        ("Send it to tejas.hendre at edu.escp.eu.", "tejas.hendre@edu.escp.eu"),
    ]:
        got, _n = polish.polish_fast(said)
        ok_all &= check("%-52s" % want[:52], want in got, got[:56])

    print("\n  6. but 'at' stays a word everywhere else")
    # This is what makes the feature safe to ship. "at" is one of the most
    # common words in English and a naive conversion would corrupt ordinary
    # dictation in every sentence containing it. Conversion happens only inside
    # a run carrying a dot AND a real top-level domain.
    for text in [
        "The meeting is at four.",
        "We will meet at the office at nine.",
        "I am at home and the report is at the printer.",
        "Let us move the call to Tuesday the 15th at 9:30 in the morning.",
        "I worked at Zalando and studied at ESCP.",
        "I pushed it to github.com yesterday.",
        "The site github.com is down.",
    ]:
        got, _n = polish.polish_fast(text)
        same = got.rstrip(".") == text.rstrip(".")
        ok_all &= check("%-52s" % text[:52], same,
                        "" if same else "changed to %r" % got)

    print("\n  7. underscore joins an identifier")
    # Unlike "at", "underscore" is never an English word in dictated prose, so
    # this one needs no address test to be safe.
    got, _n = polish.polish_fast(
        "The pill uses WS underscore EX underscore NOACTIVATE so it never "
        "takes focus.")
    ok_all &= check("WS_EX_NOACTIVATE", "WS_EX_NOACTIVATE" in got, got[:56])

    print("\n  8. a self-correction types only what was meant")
    # Measured at 81.2% word error against a raw 11.4: the model heard the
    # correction perfectly and the product typed both halves of it.
    got, _n = polish.polish_fast("Send it to Naukri, no wait, send it to "
                                 "Instahyre.")
    ok_all &= check("a repeated opening drops the whole first attempt",
                    got == "Send it to Instahyre.", got)
    got, _n = polish.polish_fast("I worked at Zalando in Berlin, sorry, in "
                                 "Berlin from January.")
    ok_all &= check("a repeated ending keeps the words before it",
                    got == "I worked at Zalando in Berlin from January.", got)

    print("\n  9. and it never guesses at a correction it cannot prove")
    # The governing rule of this module. A retry that repeats nothing gives no
    # evidence of where the first attempt began, so deleting back to the
    # previous comma would be a guess: "It came to 24 hours, actually 48 hours"
    # would silently lose "It came to".
    for text in [
        "It came to 24 hours, actually 48 hours before the change.",
        "The meeting is at four, sorry, I meant half past four.",
        "Sorry, I will be late.",
        "I am sorry, that was my mistake.",
        "He said no and that was the end of it.",
    ]:
        got, _n = polish.polish_fast(text)
        same = got.rstrip(".") == text.rstrip(".")
        ok_all &= check("%-52s" % text[:52], same,
                        "" if same else "changed to %r" % got)

    print("\n  3. it is actually free")
    long_text = " ".join(CLEAN[0][0] for _ in range(30))
    t0 = time.time()
    for _ in range(200):
        polish.polish_fast(long_text)
    per = (time.time() - t0) / 200 * 1000
    ok_all &= check("%d words in %.2f ms per call" % (len(long_text.split()), per),
                    per < 5.0)

    print("\n  4. the llm lane degrades safely")
    avail, names = polish.llm_available()
    print("       local model present: %s %s" % (avail, names or ""))
    got, notes = polish.polish_llm("um the meeting is on Tuesday",
                                   host="http://127.0.0.1:9")   # dead port
    ok_all &= check("unreachable model falls back to rules, keeps the words",
                    "meeting is on Tuesday" in got and
                    any("unavailable" in n for n in notes), got)

    over = " ".join("w%d" % i for i in range(polish.MAX_LLM_WORDS + 5))
    got, notes = polish.polish_llm(over)
    ok_all &= check("over-long text skips the llm rather than stalling",
                    any("too long" in n for n in notes), str(notes))

    if avail:
        ok, secs = polish.preload()
        print("       preload: %s in %.1fs" % (ok, secs))
        t0 = time.time()
        got, notes = polish.polish_llm(
            "um so i wanted to say that uh the the meeting is on tuesday and "
            "i think we should probably move it to wednesday you know",
            terms=core.load_vocabulary())
        el = time.time() - t0
        print("       llm lane: %.2fs -> %s" % (el, got[:70]))
        ok_all &= check("llm returned usable text", len(got.split()) > 5, got[:50])

    print("\n  %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
