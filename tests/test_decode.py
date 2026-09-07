"""The decode policy, and why it is not the default one.

ARCHITECTURE.md Section 8.3 lists beam size 3 to 5 as an experiment input and
requires a recorded comparison before a default changes. Production ran
beam_size=1 for the whole of v1, and the numbers category was blamed on the
model without that experiment ever being run.

Measured on 51 of Tejas's real recordings, identical audio for each beam:

    beam   raw WER   valid WER   numbers   ms/phrase
    1        19.6%       15.2%     66.7%         415
    3        17.3%       12.2%     70.8%         482
    5        18.5%       12.9%     70.8%         527

Beam 3 is the point, and beam 5 being worse AND slower is the usual shape: past
some width the search starts preferring a fluent continuation over a faithful
one. The 67 ms buys the cases where greedy decoding commits early and gets a
number wrong.

This suite exists so the value cannot quietly drift back to 1, and so that
every path decodes the same way. A benchmark that decodes differently from
production measures a configuration nobody runs.

    python tests/test_decode.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import io
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_core as core      # noqa: E402


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def test_the_measured_value_is_the_one_used():
    print("\n  1. the decode policy matches what was measured")
    ok = check("BEAM_SIZE exists", hasattr(core, "BEAM_SIZE"))
    if not ok:
        return False
    ok &= check("it is 3, the measured optimum", core.BEAM_SIZE == 3,
                "beam 5 was worse on word error and slower")
    ok &= check("it is inside the range Section 8.3 names",
                3 <= core.BEAM_SIZE <= 5, str(core.BEAM_SIZE))
    return ok


def test_every_real_transcribe_uses_it():
    print("\n  2. no transcribe path silently decodes differently")
    ok = True
    body = io.open(os.path.join(ROOT, "dictate_core.py"),
                   encoding="utf-8").read()

    # Warmups may stay greedy: they exist to touch cuBLAS before anything is
    # timed, and a wider beam there only slows startup.
    real = re.findall(r"self\.model\.transcribe\(\s*\n?\s*audio,[^)]*?\)",
                      body, re.S)
    ok &= check("both real transcribe calls were found", len(real) == 2,
                "%d found" % len(real))
    for i, call in enumerate(real):
        ok &= check("real transcribe %d uses BEAM_SIZE" % (i + 1),
                    "beam_size=BEAM_SIZE" in call.replace(" ", "")
                    or "beam_size=BEAM_SIZE" in call,
                    call[:60].replace("\n", " "))
        ok &= check("real transcribe %d pins temperature to 0" % (i + 1),
                    "temperature=0" in call.replace(" ", ""),
                    "Section 8.3, so the output is deterministic")

    warmups = re.findall(r"transcribe\(\s*silence[^)]*\)", body)
    for call in warmups:
        ok &= check("a warmup stays greedy", "beam_size=1" in call,
                    "it only needs to touch cuBLAS")
    return ok


def test_the_benchmark_decodes_like_production():
    print("\n  3. the benchmark measures the configuration that ships")
    # A harness that decodes differently from the app produces confident
    # numbers about something nobody runs, which is how beam_size=1 survived
    # a whole evaluation round unnoticed.
    body = io.open(os.path.join(ROOT, "eval", "bench_real.py"),
                   encoding="utf-8").read()
    ok = check("it references the shared constant",
               "core.BEAM_SIZE" in body,
               "not a copy of the number")
    scoring = [c for c in re.findall(r"model\.transcribe\([^)]*\)", body, re.S)
               if "initial_prompt" in c]
    ok &= check("both scoring calls were found", len(scoring) == 2,
                "%d found" % len(scoring))
    for call in scoring:
        ok &= check("a scoring call uses the shared constant",
                    "core.BEAM_SIZE" in call)
    return ok


def test_the_constant_carries_its_evidence():
    print("\n  4. the value is documented with the measurement behind it")
    # A tuning constant with no recorded comparison is the thing Section 8.3
    # exists to prevent, and the next person to see 3 will want to know why.
    body = io.open(os.path.join(ROOT, "dictate_core.py"),
                   encoding="utf-8").read()
    where = body.index("BEAM_SIZE = 3")
    context = body[max(0, where - 1200):where]
    ok = check("the comparison table is beside it",
               "beam" in context.lower() and "19.6" in context
               and "17.3" in context,
               "so nobody has to rerun the experiment to trust it")
    ok &= check("it says why beam 5 lost",
                "5" in context and "slower" in context.lower())
    ok &= check("it cites the section that required the comparison",
                "8.3" in context)
    return ok


def main():
    results = [test_the_measured_value_is_the_one_used(),
               test_every_real_transcribe_uses_it(),
               test_the_benchmark_decodes_like_production(),
               test_the_constant_carries_its_evidence()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
