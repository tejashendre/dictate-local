"""Phase 4: losing the window must never mean losing the words.

ARCHITECTURE.md Section 14. The rule that shapes this suite:

    If the original target cannot be restored, do not type into whichever
    window happens to be focused.

That failure is worse than losing the text. A paragraph of dictation typed into
the wrong application can be sent somewhere before the user notices. So a lost
target ends with the text on the clipboard and a visible message, never a
guess at which window was probably meant.

The recovery record is written BEFORE insertion is attempted. Writing it
afterwards would lose exactly the case it exists for.

    python tests/test_recovery.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import io
import json
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_recovery as R      # noqa: E402


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def fresh():
    return os.path.join(tempfile.mkdtemp(), "recovery.jsonl")


def test_a_lost_target_never_types_elsewhere():
    print("\n  1. a lost window means the clipboard, never another window")
    ok = True
    plan = R.plan_insertion(target_hwnd=1234, window_exists=False,
                            text="the dictated paragraph")
    ok &= check("a vanished window goes to the clipboard",
                plan["action"] == "clipboard", str(plan))
    ok &= check("and the user is told", "Target lost" in plan.get("message", ""),
                plan.get("message", ""))

    plan = R.plan_insertion(target_hwnd=None, window_exists=True, text="text")
    ok &= check("no recorded target also goes to the clipboard",
                plan["action"] == "clipboard", str(plan))

    plan = R.plan_insertion(target_hwnd=1234, window_exists=True, text="text")
    ok &= check("a live window is inserted into",
                plan["action"] == "insert" and plan["hwnd"] == 1234, str(plan))

    plan = R.plan_insertion(target_hwnd=1234, window_exists=True, text="   ")
    ok &= check("empty text inserts nothing at all",
                plan["action"] == "none", str(plan))
    return ok


def test_the_record_precedes_the_insertion():
    print("\n  2. the text is saved before insertion is attempted")
    # Section 14 step 1. Writing the record afterwards would lose it in
    # exactly the case it exists for.
    path = fresh()
    item = R.record("s1", "raw words", "final words", "Notepad", path=path)
    ok = check("the record exists immediately", os.path.exists(path))
    ok &= check("it starts as pending", item["state"] == R.PENDING)
    ok &= check("and holds the final text",
                R.load(path)[0]["final"] == "final words")

    ok &= check("it is recoverable while pending",
                R.last_recoverable(path) is not None)
    R.complete("s1", R.INSERTED, path=path)
    ok &= check("and no longer recoverable once inserted",
                R.last_recoverable(path) is None)
    return ok


def test_a_failed_insertion_stays_recoverable():
    print("\n  3. a failed insertion keeps the text")
    ok = True
    for state in (R.TARGET_LOST, R.FAILED):
        path = fresh()
        R.record("s1", "raw", "the words that matter", path=path)
        R.complete("s1", state, path=path)
        item = R.last_recoverable(path)
        ok &= check("%-12s remains recoverable" % state, item is not None)
        ok &= check("%-12s keeps the final text" % state,
                    item and item["final"] == "the words that matter")
    return ok


def test_no_audio_is_ever_stored():
    print("\n  4. only text is kept, never audio")
    # Section 19: audio is memory only and destroyed after the session.
    path = fresh()
    R.record("s1", "raw", "final", "Notepad", path=path)
    body = io.open(path, encoding="utf-8").read()
    item = json.loads(body.strip())
    ok = check("the record has only text fields",
               set(item) == {"session", "at", "raw", "final", "target", "state"},
               str(sorted(item)))
    ok &= check("nothing audio-shaped is present",
                not any(k in body.lower()
                        for k in ("wav", "pcm", "samples", "audio")))
    return ok


def test_history_is_bounded():
    print("\n  5. history is capped by count and by bytes")
    path = fresh()
    for i in range(R.MAX_ITEMS + 20):
        R.record("s%d" % i, "raw %d" % i, "final %d" % i, path=path)
    items = R.load(path)
    ok = check("count is capped", len(items) <= R.MAX_ITEMS,
               "%d items" % len(items))
    ok &= check("the newest survive, not the oldest",
                items[-1]["session"] == "s%d" % (R.MAX_ITEMS + 19),
                items[-1]["session"])

    path2 = fresh()
    big = "x" * 20000
    for i in range(60):
        R.record("s%d" % i, big, big, path=path2)
    size = os.path.getsize(path2)
    ok &= check("bytes are capped", size <= R.MAX_BYTES * 2,
                "%d bytes" % size)
    return ok


def test_history_can_be_cleared():
    print("\n  6. the user can clear it")
    # Section 16 lists Clear local history as a normal setting.
    path = fresh()
    R.record("s1", "raw", "final", path=path)
    ok = check("there is something to clear", R.load(path))
    ok &= check("clearing removes the file", R.clear(path))
    ok &= check("and it reads back empty", R.load(path) == [])
    ok &= check("clearing twice is harmless", R.clear(path) is False)
    return ok


def test_a_corrupt_line_is_survivable():
    print("\n  7. one bad line does not lose the rest")
    path = fresh()
    R.record("s1", "raw one", "final one", path=path)
    with io.open(path, "a", encoding="utf-8") as f:
        f.write("{ this is not json\n")
    R.record("s2", "raw two", "final two", path=path)
    items = R.load(path)
    ok = check("both good records survive", len(items) == 2,
               "%d loaded" % len(items))
    ok &= check("the newest is still reachable",
                R.last_recoverable(path)["final"] == "final two")
    return ok


def main():
    results = [test_a_lost_target_never_types_elsewhere(),
               test_the_record_precedes_the_insertion(),
               test_a_failed_insertion_stays_recoverable(),
               test_no_audio_is_ever_stored(),
               test_history_is_bounded(),
               test_history_can_be_cleared(),
               test_a_corrupt_line_is_survivable()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
