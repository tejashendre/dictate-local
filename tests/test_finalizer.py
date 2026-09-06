"""Phase 3: the fallback order, and the promise that nothing is ever lost.

ARCHITECTURE.md Section 12. The hierarchy is fixed:

    validated formatter result
        else deterministic corrected result
            else raw ASR transcript
                else recoverable error with no insertion

Section 11.4 states the rule this suite exists to enforce: the formatter can
never cause loss of dictation. Timeout, malformed output, model unavailability,
placeholder damage, or a failed validation all return the deterministic
candidate immediately.

That is easy to write and easy to break. A formatter that raises inside a
worker thread, or hangs, or returns JSON when text was expected, must all end
at the same place: the user's words, typed once.

    python tests/test_finalizer.py
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


RAW = "um so i did not send the report to Zalando yesterday"
DET = "So I did not send the report to Zalando yesterday"
LEX = ["Zalando"]


def test_target_classification():
    print("\n  1. the window is classified from the executable alone")
    # Section 13: only the foreground executable and control type. No document
    # content is read, and v2 collects no surrounding context at all.
    ok = True
    for exe, control, secure, want in [
        ("slack.exe", "", False, F.CHAT),
        ("outlook.exe", "", False, F.EMAIL),
        ("WINWORD.EXE", "", False, F.DOCUMENT),
        ("C:\\apps\\Code.exe", "", False, F.CODE_OR_TERMINAL),
        ("WindowsTerminal.exe", "", False, F.CODE_OR_TERMINAL),
        ("chrome.exe", "Edit", False, F.SEARCH),
        ("chrome.exe", "", False, F.UNKNOWN),
        ("anything.exe", "PasswordBox", False, F.SECURE),
        ("slack.exe", "", True, F.SECURE),
    ]:
        got = F.classify_target(exe, control, secure)
        ok &= check("%-22s -> %s" % (exe[:22], want), got == want,
                    "" if got == want else "got %s" % got)
    return ok


def test_no_formatter_means_deterministic():
    print("\n  2. with no formatter, the deterministic text is used")
    out = F.finalize(RAW, DET, formatter=None, lexicon=LEX,
                     category=F.DOCUMENT)
    ok = check("route is deterministic", out.route == "deterministic")
    ok &= check("text is the deterministic candidate", out.text == DET, out.text)
    ok &= check("and it says why", bool(out.reasons), str(out.reasons))
    return ok


def test_a_good_formatter_is_used():
    print("\n  3. a faithful formatter result is accepted")
    def good(masked, _category):
        return masked.rstrip() + "."

    out = F.finalize(RAW, DET, formatter=good, lexicon=LEX,
                     category=F.DOCUMENT)
    ok = check("route is formatted", out.route == "formatted", str(out.reasons))
    ok &= check("the protected name survived", "Zalando" in out.text, out.text)
    ok &= check("the negation survived", "not" in out.text, out.text)
    ok &= check("no placeholder leaked into the document",
                "<P" not in out.text, out.text)
    return ok


def test_every_formatter_failure_falls_back():
    print("\n  4. no formatter failure can lose the dictation")
    # Section 11.4, one case per failure mode named there.
    def raises(_m, _c):
        raise RuntimeError("model died")

    def hangs(_m, _c):
        raise TimeoutError("timed out")

    def returns_none(_m, _c):
        return None

    def returns_json(_m, _c):
        return '{"text": "something else"}'

    def answers(_m, _c):
        return "Sure! Here is your text."

    def damages(masked, _c):
        return masked.replace("<P0>", "")

    def drops_negation(masked, _c):
        return masked.replace("did not ", "did ")

    def invents(masked, _c):
        return masked + " and please confirm urgently today"

    ok = True
    for label, fn in [("raises an exception", raises),
                      ("times out", hangs),
                      ("returns None", returns_none),
                      ("returns JSON", returns_json),
                      ("answers instead of editing", answers),
                      ("damages a placeholder", damages),
                      ("drops a negation", drops_negation),
                      ("invents content", invents)]:
        out = F.finalize(RAW, DET, formatter=fn, lexicon=LEX,
                         category=F.DOCUMENT)
        good = out.route == "deterministic" and out.text == DET
        ok &= check("%-28s -> deterministic" % label, good,
                    "" if good else "route=%s text=%r" % (out.route,
                                                          out.text[:36]))
        ok &= check("%-28s    gives a reason" % label, bool(out.reasons),
                    "" if out.reasons else "silent fallback is invisible")
    return ok


def test_structured_targets_skip_the_formatter():
    print("\n  5. terminals and secure fields never see the formatter")
    called = []

    def spy(masked, _c):
        called.append(masked)
        return masked

    ok = True
    for category in (F.CODE_OR_TERMINAL, F.SECURE, F.SEARCH):
        called[:] = []
        out = F.finalize(RAW, DET, formatter=spy, lexicon=LEX,
                         category=category)
        ok &= check("%-16s never calls it" % category, not called)
        ok &= check("%-16s uses deterministic text" % category,
                    out.text == DET and out.route == "deterministic")

    called[:] = []
    F.finalize(RAW, DET, formatter=spy, lexicon=LEX, category=F.DOCUMENT)
    ok &= check("but a document does call it", len(called) == 1)
    return ok


def test_the_last_resort():
    print("\n  6. with no deterministic text, the raw transcript is used")
    out = F.finalize(RAW, "", formatter=None, lexicon=LEX)
    ok = check("falls back to raw", out.text == RAW, out.text)

    out = F.finalize("", "", formatter=None)
    ok &= check("with nothing at all, nothing is inserted",
                out.text == "" and out.route == "empty", out.route)
    ok &= check("and it is reported rather than silent", bool(out.reasons))
    return ok


def test_the_formatter_never_sees_the_values():
    print("\n  7. the formatter is given placeholders, never the facts")
    seen = []

    def spy(masked, _c):
        seen.append(masked)
        return masked

    raw = "Send EUR 1,250 to Zalando by 14 September"
    F.finalize(raw, raw, formatter=spy, lexicon=LEX, category=F.DOCUMENT)
    ok = check("it was called", len(seen) == 1)
    if seen:
        text = seen[0]
        ok &= check("the money value was hidden", "1,250" not in text, text)
        ok &= check("the date was hidden", "September" not in text, text)
        ok &= check("the name was hidden", "Zalando" not in text, text)
        ok &= check("placeholders were sent instead", "<P0>" in text, text)
    return ok


def main():
    results = [test_target_classification(),
               test_no_formatter_means_deterministic(),
               test_a_good_formatter_is_used(),
               test_every_formatter_failure_falls_back(),
               test_structured_targets_skip_the_formatter(),
               test_the_last_resort(),
               test_the_formatter_never_sees_the_values()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
