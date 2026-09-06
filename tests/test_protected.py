"""Phase 3: a fact the recogniser heard correctly must survive the editor.

ARCHITECTURE.md Section 10. A text model asked to punctuate a sentence will
also, sooner or later, decide that 1.6 billion reads better as 1.6 million, or
that Naukri was a typo. It cannot do that to a value it never sees, so every
protected span is replaced by an opaque placeholder before the formatter runs
and put back afterwards.

This suite proves the round trip is lossless for every category Section 10
lists. Masking that silently drops a value would be worse than no masking,
because the damage would be invisible.

    python tests/test_protected.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_finalizer as F      # noqa: E402
import dictate_core as core        # noqa: E402


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def roundtrip(text, lexicon=()):
    p = F.protect(text, lexicon)
    return F.restore(p.masked, p) == text, p


def test_every_category_round_trips():
    print("\n  1. every protected category survives masking")
    ok = True
    lex = ["Zalando", "Naukri", "PitchBook", "Tejas"]
    cases = [
        ("names", "I applied to Zalando through Naukri.", "name"),
        ("numbers", "We covered 3000 companies.", "number"),
        ("ranges", "We validated 8 to 14 metrics on each.", "range"),
        ("money", "The case projected EUR 1,250 in benefit.", "money"),
        ("percent", "It held at 99 percent accuracy.", "percent"),
        ("dates", "The deadline is the 30th of November 2026.", "date"),
        ("times", "Move the call to 9:30 in the morning.", "time"),
        ("email", "Send it to tejas.hendre@edu.escp.eu today.", "email"),
        ("url", "The repo is github.com/tejashendre/dictate-local.", "url"),
        ("domain", "My site is tejashendre.com and it is live.", "domain"),
        ("identifier", "The pill uses WS_EX_NOACTIVATE for focus.", "identifier"),
        ("path", "It lives in C:\\Users\\tejas\\dictate.log now.", "path"),
    ]
    for label, text, want_kind in cases:
        good, p = roundtrip(text, lex)
        kinds = set(p.kinds.values())
        ok &= check("%-11s round-trips" % label, good, text[:44])
        ok &= check("%-11s is masked as %s" % (label, want_kind),
                    want_kind in kinds, "got %s" % (sorted(kinds) or "nothing"))
    return ok


def test_the_section_10_example():
    print("\n  2. the worked example from Section 10")
    text = "Send EUR 1,250 to Tejas by 14 September."
    p = F.protect(text, ["Tejas"])
    ok = check("three spans are found", len(p.spans) == 3, p.masked)
    ok &= check("the masked form matches the document",
                p.masked == "Send <P0> to <P1> by <P2>.", p.masked)
    ok &= check("each placeholder appears exactly once",
                all(p.masked.count(k) == 1 for k in p.spans))
    ok &= check("restoration is exact", F.restore(p.masked, p) == text)
    return ok


def test_negations_are_counted():
    print("\n  3. negation is counted, because dropping one reverses meaning")
    ok = True
    for text, want in [("I did not send it.", 1),
                       ("I sent it.", 0),
                       ("Nobody replied and nothing was fixed.", 2),
                       ("It cannot work without the key.", 2),
                       ("I don't think it isn't working.", 2)]:
        got = F.count_negations(text)
        ok &= check("%-38s -> %d" % (text[:38], want), got == want,
                    "" if got == want else "got %d" % got)
    return ok


def test_longest_span_wins():
    print("\n  4. an email is one span, not a name plus a domain")
    # A span that gets split is a span a formatter can reorder.
    p = F.protect("Write to tejas.hendre@edu.escp.eu about it.", ["Tejas"])
    ok = check("the address is a single span", len(p.spans) == 1, p.masked)
    ok &= check("and it is typed as an email",
                "email" in p.kinds.values(), str(p.kinds))

    p2 = F.protect("See github.com/tejashendre/dictate-local for the code.")
    ok &= check("a URL with a path is one span", len(p2.spans) == 1, p2.masked)
    return ok


def test_ordinary_prose_is_left_alone():
    print("\n  5. a sentence with nothing protected is untouched")
    ok = True
    for text in ["I need to finish the report before the meeting tomorrow.",
                 "Please send me the updated numbers when you get a chance.",
                 "There are three things I want to cover in this call."]:
        p = F.protect(text)
        ok &= check("%-46s" % text[:46], p.masked == text,
                    "" if p.masked == text else p.masked[:44])
    return ok


def test_real_vocabulary_is_protected():
    print("\n  6. the personal lexicon is protected, and only when known")
    lex = core.load_vocabulary()
    ok = check("the vocabulary loaded", len(lex) > 10, "%d terms" % len(lex))

    p = F.protect("I applied to Zalando through Naukri last week.", lex)
    ok &= check("known names are masked", len(p.spans) >= 2, p.masked)
    ok &= check("and restore exactly",
                F.restore(p.masked, p)
                == "I applied to Zalando through Naukri last week.")

    # Guessing at unknown proper nouns would mask ordinary words and leave the
    # formatter nothing to edit, so only known terms are protected.
    p2 = F.protect("I met Aurelien at the conference.", lex)
    ok &= check("an unknown name is not guessed at",
                "Aurelien" in p2.masked, p2.masked)
    return ok


def main():
    results = [test_every_category_round_trips(),
               test_the_section_10_example(),
               test_negations_are_counted(),
               test_longest_span_wins(),
               test_ordinary_prose_is_left_alone(),
               test_real_vocabulary_is_protected()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
