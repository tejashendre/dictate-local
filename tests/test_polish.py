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
