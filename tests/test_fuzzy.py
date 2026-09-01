"""Near-miss snapping, and above all its false-positive rate.

Snapping a mangled proper noun back to the right spelling is easy. Doing it
without ever rewriting an ordinary English word is the hard part, and it is
the only reason this is safe to leave switched on.

The false-positive corpus is built from the words most likely to collide: the
ordinary English words that the vocabulary terms are made of. "linked" against
"LinkedIn" and "Orlando" against "Zalando" are the two that actually bite.

    python tests/test_fuzzy.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dictate_core as core   # noqa: E402


# Real mangles seen from small.en on this machine, plus plausible neighbours.
SHOULD_SNAP = [
    ("Arbeitsuegnis", "Arbeitszeugnis"),    # seen in streaming
    ("Arbeitsugnis", "Arbeitszeugnis"),     # seen in batch
    ("Arbeitszugnis", "Arbeitszeugnis"),
    ("Zalanda", "Zalando"),
    ("Zalendo", "Zalando"),
    ("Morningstar", "Morningstar"),         # already right, must stay right
    ("Cloudflair", "Cloudflare"),
    ("Instahire", "Instahyre"),
]

# Ordinary words that must never be rewritten. Heavy on the constituent words
# of the vocabulary terms, because those are the genuine collision risks.
MUST_NOT_SNAP = """
linked linking links link morning mornings star stars starred cloud clouds
cloudy flare flares flared micro microphone strategy strategies strategic
pitch pitched pitches book books booked booking talent talents passport
passports candidate candidates candidacy natural nature knocker knockers
orlando orland zealand zealander wonder wonderful number numbers remember
remembered september december mustard standard standards calendar calendars
instant instance instantly install installed higher hire hired hiring
escape escaped escapes company companies compare compared complete completed
consider considered continue continued country countries course courses
deliver delivered describe described develop developed different difficult
""".split()


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def main():
    terms = core.load_vocabulary()
    if not terms:
        print("  no vocabulary.txt")
        return 1
    print("  %d vocabulary terms\n" % len(terms))
    ok_all = True

    print("  1. mangled terms snap back")
    for heard, want in SHOULD_SNAP:
        got, fired = core.fuzzy_snap("I mentioned %s today." % heard, terms)
        ok_all &= check("%-16s -> %s" % (heard, want), want in got,
                        got if want not in got else "")

    print("\n  2. ordinary English is never rewritten")
    false_pos = []
    for word in MUST_NOT_SNAP:
        got, fired = core.fuzzy_snap(word, terms)
        if fired:
            false_pos.append((word, fired[0][1]))
    ok_all &= check("%d ordinary words, %d rewritten"
                    % (len(MUST_NOT_SNAP), len(false_pos)),
                    not false_pos, str(false_pos[:5]))

    print("\n  3. the two that actually bite")
    got, fired = core.fuzzy_snap("I linked the file and shared it.", terms)
    ok_all &= check("'linked' is not turned into LinkedIn",
                    "LinkedIn" not in got, got)
    got, fired = core.fuzzy_snap("I flew to Orlando last week.", terms)
    ok_all &= check("'Orlando' is not turned into Zalando",
                    "Zalando" not in got, got)

    print("\n  4. sentences from the control corpus are untouched")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from phrases import CONTROL_PHRASES
    changed = []
    for text, _ in CONTROL_PHRASES:
        got, fired = core.fuzzy_snap(text, terms)
        if fired:
            changed.append((text, fired))
    ok_all &= check("%d control sentences unchanged" % len(CONTROL_PHRASES),
                    not changed, str(changed[:2]))

    print("\n  5. thresholds are where the comments claim")
    ok_all &= check("short tokens are skipped",
                    core.fuzzy_snap("ESCP", terms)[1] == [])
    ok_all &= check("a different beginning is not a near miss",
                    core.fuzzy_snap("Balando", terms)[1] == [])

    print("\n  %s" % ("PASS" if ok_all else "FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
