"""Phase 3: what the formatter is not allowed to get away with.

ARCHITECTURE.md Section 12. The validator is the reason a local language model
can be allowed anywhere near dictated text at all. It is deterministic, it runs
locally, and it never asks a second model whether the first one behaved.

The failures it must catch are the ones that would be worst in a document the
user then sends to somebody:

    a dropped "not", which reverses the sentence
    a changed number, which is an expensive typo
    an invented clause, which says something never spoken
    an answer to the text instead of an edit of it

Every case below is a rejection that must happen. A validator that accepts one
of these is not a safety layer, it is a formality.

    python tests/test_validator.py
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


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


BASE = "I did not send EUR 1,250 to Zalando by 14 September"
LEX = ["Zalando"]


def setup():
    return BASE, F.protect(BASE, LEX)


def expect(label, edited, should_accept, category=F.DOCUMENT):
    base, p = setup()
    accepted, reasons = F.validate(base, base, edited, p, category)
    ok = accepted == should_accept
    detail = ""
    if not ok:
        detail = "expected %s, got %s. %s" % (should_accept, accepted,
                                              "; ".join(reasons[:2]))
    elif reasons:
        detail = reasons[0][:52]
    return check(label, ok, detail)


def test_a_faithful_edit_is_accepted():
    print("\n  1. a genuine copy edit passes")
    _b, p = setup()
    ok = expect("unchanged masked text", p.masked, True)
    ok &= expect("punctuation added", p.masked + ".", True)
    return ok


def test_placeholder_damage_is_rejected():
    print("\n  2. placeholder damage is rejected")
    _b, p = setup()
    ok = expect("a placeholder deleted", p.masked.replace("<P0>", ""), False)
    ok &= expect("a placeholder duplicated", p.masked + " <P0>", False)
    ok &= expect("a placeholder invented", p.masked + " <P9>", False)
    ok &= expect("a placeholder rewritten",
                 p.masked.replace("<P0>", "<P0x>"), False)
    return ok


def test_negation_cannot_be_dropped():
    print("\n  3. a dropped negation reverses the sentence, so it is rejected")
    _b, p = setup()
    ok = expect("'did not' becomes 'did'",
                p.masked.replace("did not ", "did "), False)
    ok &= expect("a negation added that was never spoken",
                 p.masked.replace("did not", "did not never"), False)
    return ok


def test_answer_like_output_is_rejected():
    print("\n  4. an answer is not an edit")
    # The formatter is a copy editor. Anything that reads as a reply, a
    # refusal, or a heading is a model that misunderstood its job.
    ok = True
    for text in ["Sure! Here is the corrected text.",
                 "Certainly, I can help with that.",
                 "I'm sorry, I cannot assist with this request.",
                 "As an AI language model, I would write it this way.",
                 "Corrected text: something else entirely",
                 "## Heading",
                 "Output: the edited sentence"]:
        ok &= expect("%-44s" % text[:44], text, False)
    return ok


def test_invented_content_is_rejected():
    print("\n  5. a word that was never spoken cannot appear")
    _b, p = setup()
    ok = expect("an adverb inserted",
                p.masked.replace("by", "urgently by"), False)
    ok &= expect("a whole clause appended",
                 p.masked + " and please confirm receipt today", False)
    return ok


def test_unexplained_deletion_is_rejected():
    print("\n  6. deletions must be explainable as filler")
    base = "So um I did not actually send the report you know"
    p = F.protect(base)
    # Removing fillers is the formatter's job and must be allowed.
    accepted, reasons = F.validate(base, base,
                                   "I did not send the report", p, F.DOCUMENT)
    ok = check("removing um, so and you know is allowed", accepted,
               "; ".join(reasons[:2]))
    # Removing content is not.
    accepted, reasons = F.validate(base, base, "I did not send", p, F.DOCUMENT)
    ok &= check("removing 'the report' is rejected", not accepted,
                reasons[0][:52] if reasons else "")
    return ok


def test_length_and_distance_envelopes():
    print("\n  7. a rewrite is not a copy edit")
    _b, p = setup()
    ok = expect("text a third longer is rejected",
                p.masked + " " + p.masked, False)
    ok &= expect("an unrelated sentence is rejected",
                 "The quick brown fox jumps over the lazy dog entirely",
                 False)
    return ok


def test_categories_that_never_format():
    print("\n  8. some categories never use the formatter at all")
    # Section 11.3. A terminal receives exactly what was said.
    _b, p = setup()
    ok = True
    for category in (F.CODE_OR_TERMINAL, F.SECURE, F.SEARCH):
        accepted, reasons = F.validate(BASE, BASE, p.masked, p, category)
        ok &= check("%-16s refuses formatter output" % category, not accepted,
                    reasons[-1][:48] if reasons else "")
    accepted, _r = F.validate(BASE, BASE, p.masked, p, F.DOCUMENT)
    ok &= check("but a document accepts it", accepted)
    return ok


def test_empty_and_missing_output():
    print("\n  9. nothing from the formatter is a rejection, not a crash")
    _b, p = setup()
    ok = True
    for label, value in [("None", None), ("empty string", ""),
                         ("whitespace", "   \n  ")]:
        accepted, reasons = F.validate(BASE, BASE, value, p, F.DOCUMENT)
        ok &= check("%-14s is rejected" % label, not accepted,
                    reasons[0][:44] if reasons else "no reason given")
        ok &= check("%-14s gives a reason" % label, bool(reasons),
                    "a silent rejection is indistinguishable from no formatter")
    return ok


def main():
    results = [test_a_faithful_edit_is_accepted(),
               test_placeholder_damage_is_rejected(),
               test_negation_cannot_be_dropped(),
               test_answer_like_output_is_rejected(),
               test_invented_content_is_rejected(),
               test_unexplained_deletion_is_rejected(),
               test_length_and_distance_envelopes(),
               test_categories_that_never_format(),
               test_empty_and_missing_output()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
