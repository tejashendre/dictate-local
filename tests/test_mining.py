"""The vocabulary miner must never cost you a word you already had.

vocabulary.txt is written by hand. Every line in it is either a term Tejas
decided mattered or a `heard -> wanted` rule he measured, and mine_vocabulary.py
is the only tool in the project that writes to it. It is also the only
application module with no test, which is the combination worth fixing: this
project has twice had a tool damage a file the user owns, and both times the
damage was silent.

The miner appends rather than rewrites, so the property should hold by
construction. That is exactly why it needs a test. A property that holds by
construction holds until somebody changes the construction, and nothing here
would notice: a mining run prints a cheerful summary either way, and the loss
would only surface later as words quietly no longer being biased.

Nothing here touches the real vocabulary.txt or the real vault. VOCAB is
redirected at a temporary file first, and the last check re-reads the real file
to prove it was never opened for writing at all.

    python tests/test_mining.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import hashlib
import io
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import mine_vocabulary as M  # noqa: E402
import dictate_core as core  # noqa: E402

REAL_VOCAB = os.path.join(ROOT, "vocabulary.txt")


def digest_of(path):
    try:
        return hashlib.sha256(io.open(path, "rb").read()).hexdigest()
    except Exception:
        return None


# Taken at import, before any test has run, so the comparison at the end is
# against the file as it was when this process started. Searching the file for a
# mining header instead would be wrong: Tejas has run the miner for real, and
# the header it left is committed.
REAL_VOCAB_AT_START = digest_of(REAL_VOCAB)

# What a hand-written vocabulary file looks like: terms, comments, blank lines,
# a correction rule, and a term that is also a comment-adjacent word. Every one
# of these has to survive byte for byte.
SEED = """# Terms Whisper gets wrong on my voice.
Naukri
Zalando
SpendSignal

# Corrections, measured not guessed.
Mokri -> Naukri
Celero -> Silero

DoubleTick
"""


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def make_vault(d):
    """A vault whose obvious candidates are not already in SEED."""
    os.makedirs(d)
    # Repeated well above --min-count, capitalised every time, so
    # is_ordinary_english cannot filter them out as normal words.
    body = ("Kavipriya joined the Kavipriya review. Kavipriya and Bengaluru "
            "met in Bengaluru near Bengaluru. Shubham asked Shubham about "
            "Shubham and Kavipriya in Bengaluru with Shubham.\n") * 3
    io.open(os.path.join(d, "notes.md"), "w", encoding="utf-8").write(body)
    io.open(os.path.join(d, "more.md"), "w", encoding="utf-8").write(
        "Bengaluru Shubham Kavipriya Bengaluru Shubham Kavipriya\n")
    return d


def run_miner(vocab_path, vault, apply_it):
    """Call main() the way the command line does, with VOCAB redirected."""
    argv = sys.argv
    saved = M.VOCAB
    M.VOCAB = vocab_path
    sys.argv = ["mine_vocabulary.py", "--vault", vault, "--min-count", "3",
                "--top", "10"] + (["--apply"] if apply_it else [])
    out = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = out
    try:
        code = M.main()
    finally:
        sys.stdout = real_stdout
        sys.argv = argv
        M.VOCAB = saved
    return code, out.getvalue()


def test_nothing_is_lost():
    print("\n  1. a mining run cannot remove or alter a word you had")
    d = tempfile.mkdtemp()
    vocab = os.path.join(d, "vocabulary.txt")
    io.open(vocab, "w", encoding="utf-8", newline="\n").write(SEED)
    vault = make_vault(os.path.join(d, "vault"))

    before = io.open(vocab, encoding="utf-8").read()
    code, out = run_miner(vocab, vault, apply_it=True)
    after = io.open(vocab, encoding="utf-8").read()

    ok = check("the run succeeded", code == 0, "exit %s" % code)
    ok &= check("the original content is still there, byte for byte",
                after.startswith(before),
                "append-only is the whole safety property")
    for line in [l for l in SEED.splitlines() if l.strip()]:
        if not check("kept: %s" % line[:34], line in after.splitlines()):
            ok = False
    ok &= check("the file only grew", len(after) > len(before),
                "%d -> %d bytes" % (len(before), len(after)))
    return ok


def test_nothing_is_duplicated():
    print("\n  2. a term you already have is not proposed again")
    d = tempfile.mkdtemp()
    vocab = os.path.join(d, "vocabulary.txt")
    # Seed it with the very words the vault is full of, so a miner that failed
    # to check would duplicate all three.
    io.open(vocab, "w", encoding="utf-8", newline="\n").write(
        SEED + "Kavipriya\nBengaluru\nShubham\n")
    vault = make_vault(os.path.join(d, "vault"))

    before = io.open(vocab, encoding="utf-8").read()
    _c, out = run_miner(vocab, vault, apply_it=True)
    after = io.open(vocab, encoding="utf-8").read()

    ok = check("the original content survives", after.startswith(before))
    body = [l.strip() for l in after.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    dupes = {t for t in body if body.count(t) > 1}
    ok &= check("no term appears twice", not dupes, str(sorted(dupes))[:60])
    return ok


def test_a_second_run_adds_nothing():
    print("\n  3. running it twice is the same as running it once")
    d = tempfile.mkdtemp()
    vocab = os.path.join(d, "vocabulary.txt")
    io.open(vocab, "w", encoding="utf-8", newline="\n").write(SEED)
    vault = make_vault(os.path.join(d, "vault"))

    run_miner(vocab, vault, apply_it=True)
    once = io.open(vocab, encoding="utf-8").read()
    terms_once = set(core.load_vocabulary(vocab))
    run_miner(vocab, vault, apply_it=True)
    twice = io.open(vocab, encoding="utf-8").read()
    terms_twice = set(core.load_vocabulary(vocab))

    ok = check("the first run's content is untouched", twice.startswith(once))
    ok &= check("and no new term appeared", terms_once == terms_twice,
                str(sorted(terms_twice - terms_once))[:60])
    return ok


def test_dry_run_writes_nothing():
    print("\n  4. without --apply the file is not opened for writing")
    d = tempfile.mkdtemp()
    vocab = os.path.join(d, "vocabulary.txt")
    io.open(vocab, "w", encoding="utf-8", newline="\n").write(SEED)
    vault = make_vault(os.path.join(d, "vault"))

    before = io.open(vocab, "rb").read()
    mtime = os.path.getmtime(vocab)
    code, out = run_miner(vocab, vault, apply_it=False)
    after = io.open(vocab, "rb").read()

    ok = check("the run succeeded", code == 0)
    ok &= check("the bytes are identical", before == after)
    ok &= check("the file was not even rewritten identically",
                os.path.getmtime(vocab) == mtime)
    ok &= check("and it says so", "Nothing written" in out,
                "the user has to be able to tell")
    return ok


def test_what_it_appends_is_usable():
    print("\n  5. every appended line is a term the app can actually load")
    d = tempfile.mkdtemp()
    vocab = os.path.join(d, "vocabulary.txt")
    io.open(vocab, "w", encoding="utf-8", newline="\n").write(SEED)
    vault = make_vault(os.path.join(d, "vault"))
    run_miner(vocab, vault, apply_it=True)

    added = [l.strip() for l in io.open(vocab, encoding="utf-8")
             .read()[len(SEED):].splitlines()
             if l.strip() and not l.strip().startswith("#")]
    ok = check("it appended something", bool(added), str(added)[:60])
    ok &= check("nothing is longer than the term limit",
                all(len(t) <= core.MAX_TERM_CHARS for t in added),
                "a long line is dropped at load and the slot is wasted")
    # A line containing -> is parsed as a correction rule, not a term. The miner
    # emitting one would silently turn a word into a rewrite rule.
    ok &= check("no appended line looks like a correction rule",
                not any("->" in t for t in added))
    loaded = core.load_vocabulary(vocab)
    ok &= check("load_vocabulary picks all of them up",
                all(t in loaded for t in added),
                str([t for t in added if t not in loaded])[:60])
    return ok


def test_the_real_file_was_never_touched():
    print("\n  6. and the real vocabulary.txt was never opened for writing")
    # The reason this test file exists at all. If any check above leaked the
    # real path, this is what says so, and it says so in the same run rather
    # than three sessions later.
    ok = check("the real file is still there", os.path.exists(REAL_VOCAB))
    if ok:
        now = digest_of(REAL_VOCAB)
        ok &= check("byte for byte what it was when this run started",
                    now == REAL_VOCAB_AT_START,
                    "sha %s, was %s" % ((now or "?")[:12],
                                        (REAL_VOCAB_AT_START or "?")[:12]))
        ok &= check("and it still holds Tejas's own terms",
                    len(core.load_vocabulary()) > 40,
                    "%d terms" % len(core.load_vocabulary()))
    return ok


def test_that_guard_is_not_decoration():
    print("\n  7. and that check can actually fail")
    # Check 6 compares a hash taken at import against one taken at the end, so
    # it only sees damage done DURING the run - which is the leak worth
    # catching. Proving that by appending to the real vocabulary.txt and
    # restoring it afterwards would work, and is exactly the pattern that has
    # already destroyed a user file twice on this project: a crash between the
    # damage and the restore leaves the damage. So the mechanism is proved on a
    # copy, and the real file is never opened for writing by any code here.
    d = tempfile.mkdtemp()
    p = os.path.join(d, "vocabulary.txt")
    io.open(p, "w", encoding="utf-8", newline="\n").write(SEED)
    before = digest_of(p)
    ok = check("a hash of an untouched file is stable", digest_of(p) == before)
    with io.open(p, "a", encoding="utf-8") as f:
        f.write("\nLeakedTermFromATest\n")
    ok &= check("and one appended line changes it", digest_of(p) != before,
                "which is what check 6 compares")
    ok &= check("a missing file reads as None, not a crash",
                digest_of(os.path.join(d, "gone.txt")) is None)
    shutil.rmtree(d, ignore_errors=True)
    return ok


def main():
    print("\n  mine_vocabulary is the only tool that writes vocabulary.txt")
    results = [test_nothing_is_lost(),
               test_nothing_is_duplicated(),
               test_a_second_run_adds_nothing(),
               test_dry_run_writes_nothing(),
               test_what_it_appends_is_usable(),
               test_the_real_file_was_never_touched(),
               test_that_guard_is_not_decoration()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
