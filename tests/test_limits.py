"""What happens when input is malformed, or nobody stops the recording.

Three faults found by probing rather than by using the app, because all three
fail quietly. Nothing crashes at the moment they happen, so none of them would
ever show up as a bug report - they just make the tool worse and say nothing.

    1. A recording with no second F9 never ends. The microphone callback
       appends to an unbounded queue at 64 KB/s, so a forgotten hotkey costs
       about 230 MB an hour and finishes with one transcribe over all of it.

    2. settings.json holding valid JSON that is not an object - "null" is the
       easy one - raised TypeError inside load(), before the log file exists.
       The app died at startup with nothing written down.

    3. build_prompt() stopped at the first term too long for the budget. If
       that term was first in the file the prompt came back None and every
       vocabulary term was silently unbiased for the whole session.

    python tests/test_limits.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import io
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def test_recording_is_bounded():
    print("\n  1. a recording nobody stops still ends")
    import dictate
    ok = check("MAX_SECONDS is defined", hasattr(dictate, "MAX_SECONDS"))
    if not ok:
        return False
    cap = dictate.MAX_SECONDS
    ok &= check("the cap is long enough for real dictation",
                cap >= 120, "%gs is %d words at 120 wpm" % (cap, cap / 60 * 120))
    ok &= check("but short enough to bound the damage",
                cap <= 900, "%gs holds %d MB of audio" % (cap, cap * 64 / 1024))

    # The check has to live in the branch that runs when no key was pressed.
    # Putting it after the wait would mean it only fires when the user does
    # the very thing that made it unnecessary.
    src = io.open(os.path.join(ROOT, "dictate.py"), encoding="utf-8").read()
    idx = src.find("if not toggle.wait(")
    window = src[idx:idx + 600]
    ok &= check("checked on the idle tick, not only after a keypress",
                "MAX_SECONDS" in window and "toggle.set()" in window)
    ok &= check("it stops by the normal path, so what was said is typed",
                "toggle.set()" in window and "continue" in window,
                "reusing the stop path means no separate transcribe to go wrong")
    return ok


def test_settings_survive_garbage():
    print("\n  2. a malformed settings.json cannot stop the app starting")
    import dictate_config as dc
    d = tempfile.mkdtemp()
    ok = True
    # "null" is the one that actually raised: json.load returns None, and
    # "key in None" is a TypeError. A bare string was quietly worse - "key in
    # string" is a substring test, so it matched things it should not have.
    cases = [("null", "null"), ("a list", "[1, 2, 3]"),
             ("a bare string", '"hotkey"'), ("a number", "42"),
             ("true", "true"), ("truncated", '{"hotkey": "f9", "stream": tr'),
             ("empty file", ""), ("not json at all", "<<<garbage>>>"),
             ("wrong value types", '{"pause_s": "banana", "stream": "yes"}')]
    for name, blob in cases:
        p = os.path.join(d, "s.json")
        io.open(p, "w", encoding="utf-8").write(blob)
        try:
            cfg = dc.load(p)
            good = cfg["hotkey"] == "f9" and isinstance(cfg["pause_s"], float)
            ok &= check("%-18s falls back to defaults" % name, good,
                        "" if good else repr(cfg)[:70])
        except Exception as e:
            ok &= check("%-18s falls back to defaults" % name, False,
                        "RAISED %s: %s" % (type(e).__name__, e))

    p = os.path.join(d, "adir.json")
    os.mkdir(p)
    try:
        ok &= check("a directory where the file should be", dc.load(p)["hotkey"] == "f9")
    except Exception as e:
        ok &= check("a directory where the file should be", False,
                    type(e).__name__)
    return ok


def test_one_bad_term_cannot_disable_vocabulary():
    print("\n  3. one unusable term does not take the others with it")
    import dictate_core as core
    real = ["Zalando", "Naukri", "SpendSignal", "DoubleTick", "Shubham"]
    junk = "x" * 5000

    prompt, used, dropped = core.build_prompt(real)
    ok = check("a normal list builds a prompt", prompt is not None
               and all(t in prompt for t in real), str(used))

    # This is the case that was broken: the loop hit the oversized term, broke
    # out with nothing collected, and returned None.
    prompt, used, dropped = core.build_prompt([junk] + real)
    ok &= check("an oversized term FIRST is skipped, not fatal",
                prompt is not None, "prompt was None - biasing silently off")
    ok &= check("every real term still survives it",
                prompt is not None and all(t in prompt for t in real),
                str(used)[:70])
    ok &= check("and the bad one is reported as dropped", junk in dropped)

    prompt, used, dropped = core.build_prompt(real[:2] + [junk] + real[2:])
    ok &= check("an oversized term in the middle is skipped too",
                prompt is not None and all(t in prompt for t in real))

    # A runaway mine_vocabulary run must still respect the budget.
    prompt, used, dropped = core.build_prompt(["Term%d" % i for i in range(2000)])
    ok &= check("2000 terms still fit the budget",
                prompt is not None
                and core._estimate_tokens(prompt) <= core.PROMPT_TOKEN_BUDGET,
                "%d tokens" % core._estimate_tokens(prompt))

    ok &= check("an empty list is still None, not a crash",
                core.build_prompt([])[0] is None)

    print("\n  4. and a line that long never reaches the prompt anyway")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "v.txt")
    io.open(p, "w", encoding="utf-8").write(
        "\n".join(["Zalando", junk, "Naukri", "#a comment", "",
                   "heard this -> wanted that"]))
    terms = core.load_vocabulary(p)
    ok &= check("load_vocabulary drops the oversized line",
                terms == ["Zalando", "Naukri"], str(terms)[:60])
    ok &= check("the real file is unaffected by the limit",
                len(core.load_vocabulary()) > 40,
                "%d terms" % len(core.load_vocabulary()))
    longest = max((len(t) for t in core.load_vocabulary()), default=0)
    ok &= check("no real term is anywhere near the limit",
                longest < core.MAX_TERM_CHARS,
                "longest is %d, limit is %d" % (longest, core.MAX_TERM_CHARS))
    return ok


def main():
    results = [test_recording_is_bounded(),
               test_settings_survive_garbage(),
               test_one_bad_term_cannot_disable_vocabulary()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
