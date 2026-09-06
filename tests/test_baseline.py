"""Phase 0: the baseline must be reproducible, and measuring must not change it.

Two ways this project could lie to itself about its own accuracy, both of which
have already happened once here:

  1. The suite runner claims to run the tests and does not. Run-Tests.cmd
     listed seven of sixteen suites, and two of those seven were unreachable
     because "tests\\test_endtoend.py" had been saved with the backslash-t read
     as a literal tab. Five suites ran, and the window said the suite passed.

  2. A benchmark writes to the live settings file. That is not hypothetical
     either: a test once wrote synthetic audio levels into settings.json as the
     user's voice level and locked him out of his own microphone. The guard
     added afterwards is DICTATE_TESTING, and a guard nothing checks is a guard
     that will be removed by someone who does not know why it exists.

So this suite protects the measuring instruments rather than the product.

    python tests/test_baseline.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import glob
import hashlib
import io
import json
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def digest(path):
    if not os.path.exists(path):
        return None
    return hashlib.sha256(io.open(path, "rb").read()).hexdigest()


def test_runner_invokes_every_suite():
    print("\n  1. the runner actually runs every suite it advertises")
    ok = True
    runner = os.path.join(ROOT, "Run-Tests.cmd")
    body = io.open(runner, encoding="utf-8", errors="ignore").read()

    # The exact corruption that hid two suites: a tab where \t was meant.
    ok &= check("no literal tab inside a test path",
                "tests\t" not in body,
                "a tab here silently renames test_endtoend.py")

    on_disk = sorted(os.path.basename(p)
                     for p in glob.glob(os.path.join(HERE, "test_*.py")))
    # Discovery beats a hand written list, because a list cannot fail to
    # mention a suite added later.
    discovers = "tests\\test_*.py" in body or 'tests\\test_*.py"' in body
    ok &= check("the runner discovers suites rather than listing them",
                discovers, "%d suites exist on disk" % len(on_disk))

    if not discovers:
        named = [n for n in on_disk if n in body]
        missing = [n for n in on_disk if n not in body]
        ok &= check("every suite on disk is named", not missing,
                    "missing: %s" % ", ".join(missing[:6]))
    ok &= check("it reports a pass and fail count",
                "PASS" in body and "FAIL" in body)
    return ok


def test_benchmark_cannot_touch_live_settings():
    print("\n  2. benchmarking never writes the live settings file")
    ok = True
    live = os.path.join(ROOT, "settings.json")
    before = digest(live)

    import dictate_config as dc

    # The guard itself. save() must refuse the live path while testing.
    #
    # The value has to be CHANGED first. An earlier version of this test saved
    # the settings it had just loaded, so the bytes were identical whether or
    # not save() actually wrote, and the test passed even with the guard
    # deleted. Verified by deleting the guard and watching it still pass.
    sample = dc.load()
    sample["pause_s"] = float(sample.get("pause_s", 0.7)) + 0.37
    sample["hotkey"] = "f13"
    try:
        dc.save(sample)
        wrote = digest(live) != before
        ok &= check("save() refuses the live file under DICTATE_TESTING",
                    not wrote,
                    "" if not wrote else "IT WROTE to settings.json")
    except Exception as e:
        ok &= check("save() refuses the live file under DICTATE_TESTING", True,
                    "raised %s, which is also a refusal" % type(e).__name__)

    # And it must still work when given somewhere else to write.
    d = tempfile.mkdtemp()
    other = os.path.join(d, "settings.json")
    dc.save(sample, path=other)
    ok &= check("but save() still works on an explicit path",
                os.path.exists(other))

    ok &= check("the live file is byte for byte unchanged",
                digest(live) == before)
    return ok


def test_eval_modules_arm_the_guard():
    print("\n  3. every eval entry point arms the guard before importing")
    # The order matters. dictate_config reads the environment at import time,
    # so setting DICTATE_TESTING after the import would be too late.
    ok = True
    for name in ("bench_real.py", "record.py", "corpus.py", "score.py"):
        path = os.path.join(ROOT, "eval", name)
        if not os.path.exists(path):
            continue
        body = io.open(path, encoding="utf-8").read()
        if "DICTATE_TESTING" not in body:
            # A module that never imports the app cannot write settings.
            imports_app = ("dictate_core" in body or "dictate_config" in body
                           or "dictate_polish" in body)
            ok &= check("%s needs no guard, it never imports the app" % name,
                        not imports_app,
                        "" if not imports_app
                        else "it IMPORTS the app with no DICTATE_TESTING")
            continue
        guard = body.index("DICTATE_TESTING")
        app = min([body.index(m) for m in
                   ("import dictate_core", "import dictate_config",
                    "import dictate_polish") if m in body] or [len(body)])
        ok &= check("%s arms the guard before importing the app" % name,
                    guard < app)
    return ok


def test_baseline_is_reproducible():
    print("\n  4. the baseline can be reproduced from what is committed")
    ok = True
    bench = os.path.join(ROOT, "eval", "bench_real.py")
    ok &= check("the benchmark command exists", os.path.exists(bench),
                "python eval/bench_real.py --models small.en")

    body = io.open(bench, encoding="utf-8").read()
    ok &= check("it refuses to download without permission",
                "--allow-download" in body and "not cached" in body)
    ok &= check("it writes results outside the repository",
                "C.PRIVATE" in body or "private" in body)

    # The results themselves are private. What must be reproducible is the
    # command and the corpus, not a number copied into a tracked file.
    r = subprocess.run(["git", "check-ignore", "-q", "eval/private/results"],
                       cwd=ROOT, capture_output=True)
    ok &= check("result files stay untracked", r.returncode == 0)
    return ok


def test_nothing_private_is_tracked():
    print("\n  5. no private material is visible to git")
    ok = True
    for rel in ("eval/private/corpus.jsonl",
                "eval/private/audio/ordinary_000.wav",
                "eval/private/results/bench_x.json",
                "settings.json",
                "settings.json.bak2",
                "transcript.log",
                "dictate.log",
                "corrections.json",
                "corrections.json.quarantined",
                "recovery.jsonl"):
        r = subprocess.run(["git", "check-ignore", "-q", rel],
                           cwd=ROOT, capture_output=True)
        ok &= check("ignored: %s" % rel, r.returncode == 0)

    r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                       capture_output=True, text=True)
    tracked = r.stdout.split("\n")
    leaked = [f for f in tracked
              if f.startswith("eval/private") or f.endswith(".wav")
              and not f.startswith("tests/audio")]
    ok &= check("nothing private is tracked", not leaked,
                ", ".join(leaked[:4]))
    return ok


def main():
    results = [test_runner_invokes_every_suite(),
               test_benchmark_cannot_touch_live_settings(),
               test_eval_modules_arm_the_guard(),
               test_baseline_is_reproducible(),
               test_nothing_private_is_tracked()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
