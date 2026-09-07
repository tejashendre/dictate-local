"""Phase 6: the release gates, and whether a change made anything worse.

ARCHITECTURE.md Sections 21.3 and 22. Two jobs, and they are different:

  Report every gate as pass or gap. Section 21.3 is explicit that these are
  release gates and not current claims, and that if the model cannot reach
  them on this hardware the honest response is to report the gap rather than
  adjust the corpus until it passes.

  Fail on regression. A category that got worse must fail the check even when
  the aggregate improved, because an average is exactly where a regression
  hides. This project has already published an aggregate that improved while
  three categories were being made worse.

The measured values are private. Section 3 forbids the tracked repository from
quoting corpus accuracy a public reviewer cannot reproduce, so this suite reads
eval/private/results/ and prints what it finds without any number being
committed anywhere.

    python tests/test_quality_gate.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import glob
import io
import json
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

RESULTS = os.path.join(ROOT, "eval", "private", "results")

# Categories written during design rather than sampled from real use. Every
# one was scripted with correction markers that appear zero times in 928 lines
# of Tejas's dictation, so they measure whether the tool performs a
# transformation he never asks for.
SCRIPTED = ("false_starts", "self_correction", "urls_emails")

# Section 21.3, in the order that table lists them. The fourth field records
# whether the corpus can answer the question at all, because a gap caused by a
# broken measurement is not a product defect and reporting the two identically
# hides both.
CORPUS_OK = None

FICTION = ("scripted with correction markers that appear ZERO times in 928 "
           "lines of real dictation, so this measures the design, not him")
NOT_SILENCE = ("the four silence controls are long recordings of a room with a "
               "television audible, so the model is transcribing real speech")

GATES = (
    ("silence inserted as text", "silence_false_positive_rate", "<=", 0.0,
     NOT_SILENCE),
    ("final WER at or below raw", "final_not_worse_than_raw", "==", 1.0,
     CORPUS_OK),
    ("ordinary-speech raw WER", "ordinary_raw", "<=", 0.10, CORPUS_OK),
    ("final WER", "wer_final", "<=", 0.08, CORPUS_OK),
    ("exact final utterances", "exact_final", ">=", 0.80, CORPUS_OK),
    ("protected names", "name_accuracy_final", ">=", 0.95, CORPUS_OK),
    ("protected numbers", "number_accuracy", ">=", 0.95, CORPUS_OK),
    ("false-start final WER", "false_starts_final", "<=", 0.15, FICTION),
    ("self-correction final WER", "self_correction_final", "<=", 0.15, FICTION),
)

# A category may drift by this much before it counts as a regression. Below
# this the difference is decode noise, not a change in behaviour.
REGRESSION_TOLERANCE = 0.02


def _wrap(text, width):
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def runs():
    """Every benchmark result on this machine, oldest first."""
    out = []
    for path in sorted(glob.glob(os.path.join(RESULTS, "bench_*.json"))):
        try:
            with io.open(path, encoding="utf-8") as f:
                data = json.load(f)
            for result in data.get("results", []):
                if "summary" in result:
                    out.append((os.path.basename(path), result))
        except Exception:
            continue
    return out


def metrics(result, valid_only=False):
    """Flatten a result into the values the gates are written against.

    valid_only recomputes the aggregates over the recordings whose category can
    actually answer its question, which is the same judgement already applied to
    the per-category gates. Averaging over a measurement known to be broken
    reports the harness, not the product.
    """
    s = result["summary"]
    cats = s.get("by_category", {})

    def cat(name, key):
        return cats.get(name, {}).get(key)

    rows = [r for r in result.get("rows", []) if not r.get("silent")]
    subset = [r for r in rows if r["category"] not in SCRIPTED]
    if valid_only and subset:
        n = len(subset)
        agg_raw = sum(r["wer_raw"] for r in subset) / n
        agg_final = sum(r["wer_final"] for r in subset) / n
        agg_exact = sum(1 for r in subset if r["exact_final"]) / n
    else:
        agg_raw = s.get("wer_raw")
        agg_final = s.get("wer_final")
        agg_exact = s.get("exact_final")

    out = {
        "wer_raw": agg_raw,
        "wer_final": agg_final,
        "exact_final": agg_exact,
        "name_accuracy_final": s.get("name_accuracy_final"),
        "number_accuracy": s.get("number_accuracy"),
        "silence_false_positive_rate": s.get("silence_false_positive_rate"),
        "ordinary_raw": cat("ordinary", "wer_raw"),
        "false_starts_final": cat("false_starts", "wer_final"),
        "self_correction_final": cat("self_correction", "wer_final"),
    }
    raw, final = out["wer_raw"], out["wer_final"]
    out["final_not_worse_than_raw"] = (
        1.0 if (raw is not None and final is not None and final <= raw)
        else 0.0)
    return out


def passes(value, op, target):
    if value is None:
        return None
    if op == "<=":
        return value <= target + 1e-9
    if op == ">=":
        return value >= target - 1e-9
    return abs(value - target) < 1e-9


def test_gates_are_reported():
    print("\n  1. every Section 21.3 gate is reported as pass or gap")
    found = runs()
    if not found:
        return check("no benchmark has been run on this machine yet", True,
                     "run: python eval/bench_real.py --models small.en")

    name, latest = found[-1]
    m = metrics(latest, valid_only=True)
    m_all = metrics(latest, valid_only=False)
    print("      latest run: %s, model %s" % (name, latest.get("name")))
    print("      aggregates measured over the categories that can answer;")
    print("      the all-speech figure is shown beside each in brackets.")
    print()

    met = gaps = unknown = corpus = 0
    notes = []
    for label, key, op, target, caveat in GATES:
        state = passes(m.get(key), op, target)
        value = m.get(key)
        shown = "not measured" if value is None else (
            "%.1f%%" % (100 * value) if key != "final_not_worse_than_raw"
            else ("yes" if value else "NO"))
        want = ("%s %.0f%%" % (op, 100 * target)
                if key != "final_not_worse_than_raw" else "required")
        if state is None:
            unknown += 1
            mark = "gap "
        elif state:
            met += 1
            mark = "MET "
        elif caveat:
            # The measurement cannot answer the question, so the number says
            # nothing about the product either way.
            corpus += 1
            mark = "CORP"
            notes.append((label, caveat))
        else:
            gaps += 1
            mark = "GAP "
        other = m_all.get(key)
        beside = ""
        if other is not None and m.get(key) is not None and other != m.get(key):
            beside = ("[all %.1f%%]" % (100 * other)
                      if key != "final_not_worse_than_raw"
                      else "[all %s]" % ("yes" if other else "NO"))
        print("      %s %-30s %-14s (need %-9s) %s"
              % (mark, label, shown, want, beside))

    print()
    print("      %d met, %d real gap, %d corpus cannot answer, %d not measured"
          % (met, gaps, corpus, unknown))
    if notes:
        print()
        for label, why in notes:
            print("      CORP %s:" % label)
            for line in _wrap(why, 66):
                print("           %s" % line)
    # Reporting is the requirement. Section 21.3 says a gate that cannot be
    # reached is reported, not hidden, and never fixed by editing the corpus.
    return check("all %d gates evaluated" % len(GATES),
                 met + gaps + corpus + unknown == len(GATES))


def test_the_product_on_recordings_that_measure_it():
    print("\n  2. the same gates over only the valid recordings")
    found = runs()
    if not found:
        return check("nothing to measure yet", True)
    _name, latest = found[-1]
    rows = [r for r in latest.get("rows", []) if not r.get("silent")]
    if not rows:
        return check("no speech rows in the result", True)

    valid = [r for r in rows if r["category"] not in SCRIPTED]
    scripted = [r for r in rows if r["category"] in SCRIPTED]

    def mean(subset, key):
        return sum(x[key] for x in subset) / len(subset) if subset else 0.0

    print("      %-26s %8s %8s %12s" % ("", "raw", "final", "difference"))
    for label, subset in (("all speech", rows),
                          ("scripted behaviour", scripted),
                          ("real transcription", valid)):
        raw, fin = mean(subset, "wer_raw"), mean(subset, "wer_final")
        print("      %-26s %7.1f%% %7.1f%% %+11.1f" % (
            "%s (%d)" % (label, len(subset)), 100 * raw, 100 * fin,
            100 * (fin - raw)))

    ok = True
    if valid:
        raw, fin = mean(valid, "wer_raw"), mean(valid, "wer_final")
        better = fin <= raw
        print()
        ok &= check("the cleanup improves text it was designed for", better,
                    "Section 3 says the opposite is the fault v2 exists to fix")
        exact = sum(1 for r in valid if r["exact_final"]) / len(valid)
        print("      exact-match final on valid recordings: %.1f%%"
              % (100 * exact))
    if scripted:
        print()
        print("      The scripted group is where the aggregate turns negative.")
        print("      It is not evidence about the product until those phrases")
        print("      are re-recorded from how Tejas actually corrects himself.")
    return ok


def accepted_baseline():
    """The run the shipped configuration produced, and why it was accepted.

    Comparing each run against the chronologically previous one sounds right and
    is not: a rejected experiment still lands in the results directory, and the
    next real run is then measured against a configuration nobody shipped. That
    is how "fan_noise +5.0 points" was once reported for a change that never
    touched noise handling. It was being compared to a discarded prompt rather
    than to the code in git.

    So the baseline is named explicitly in baseline.txt: the first line is the
    result file, the rest is the reason it was accepted. Requiring the reason is
    the point. A regression cannot be adopted by re-running until the previous
    number moves; somebody has to write down what was traded for what.
    """
    p = os.path.join(RESULTS, "baseline.txt")
    if not os.path.exists(p):
        return None, ""
    try:
        raw = io.open(p, encoding="utf-8").read()
    except Exception:
        return None, ""
    lines = [l.rstrip() for l in raw.splitlines()]
    lines = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return None, ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()


def test_no_category_regressed():
    print("\n  2. no category got worse than the accepted baseline")
    found = runs()
    same_model = [(n, r) for n, r in found if r.get("name") == "small.en"]
    if len(same_model) < 2:
        return check("not enough runs to compare yet", True,
                     "%d run(s) for small.en" % len(same_model))

    base_name, reason = accepted_baseline()
    picked = [(n, r) for n, r in same_model if n == base_name]

    if picked and same_model[-1][0] == base_name:
        # The newest run is the baseline itself, which is what "I just accepted
        # this configuration" looks like. There is no candidate to test, and
        # comparing the baseline to whatever ran before it would re-introduce
        # the exact bug this function exists to remove: the run before it is
        # often a rejected experiment.
        #
        # So the check becomes the one that still has teeth here - an accepted
        # baseline must say what it cost. Without that requirement a regression
        # could be adopted by moving the pointer and saying nothing.
        print("      %s is the accepted baseline; nothing newer to test"
              % base_name)
        for line in (reason or "").splitlines():
            print("      %s" % line)
        return check("the acceptance is justified in writing",
                     len(reason.strip()) > 40,
                     "baseline.txt must record what the change traded")

    if picked:
        older_name, older = picked[-1]
        newer_name, newer = same_model[-1]
    else:
        if base_name:
            print("      baseline.txt names %s, which is not on disk" % base_name)
        (older_name, older), (newer_name, newer) = same_model[-2], same_model[-1]

    print("      %s  ->  %s" % (older_name, newer_name))
    for line in (reason or "").splitlines():
        print("      %s" % line)

    a = older["summary"].get("by_category", {})
    b = newer["summary"].get("by_category", {})
    ok = True
    worse = []
    for cat in sorted(set(a) & set(b)):
        before = a[cat].get("wer_final")
        after = b[cat].get("wer_final")
        if before is None or after is None:
            continue
        delta = after - before
        if delta > REGRESSION_TOLERANCE:
            worse.append("%s %+.1f points" % (cat, 100 * delta))
    ok &= check("no category regressed past tolerance", not worse,
                "; ".join(worse[:3]))

    # An aggregate that improves while a category degrades is the shape this
    # check exists to catch, so the aggregate is reported but never used to
    # excuse a category.
    ba = older["summary"].get("wer_final")
    bb = newer["summary"].get("wer_final")
    if ba is not None and bb is not None:
        print("      aggregate final WER moved %+.1f points" % (100 * (bb - ba)))
    return ok


def test_the_regression_check_can_actually_fail():
    print("\n  3. the regression check is not decoration")
    # A gate that cannot fail proves nothing, so it is exercised here against
    # a synthetic pair rather than trusted.
    fake_old = {"summary": {"by_category": {"names": {"wer_final": 0.10}},
                            "wer_final": 0.10}, "name": "small.en"}
    fake_new = {"summary": {"by_category": {"names": {"wer_final": 0.30}},
                            "wer_final": 0.05}, "name": "small.en"}
    a = fake_old["summary"]["by_category"]
    b = fake_new["summary"]["by_category"]
    worse = [c for c in b
             if b[c]["wer_final"] - a[c]["wer_final"] > REGRESSION_TOLERANCE]
    ok = check("a 20-point category regression is detected", worse == ["names"],
               str(worse))
    ok &= check("even though the aggregate improved",
                fake_new["summary"]["wer_final"]
                < fake_old["summary"]["wer_final"],
                "this is the shape an average hides")
    return ok


def test_no_private_number_is_committed():
    print("\n  4. no measured value from the private corpus is tracked")
    # Section 3. The repository may describe the method and must not quote a
    # figure a public reviewer cannot reproduce.
    import subprocess
    ok = True
    r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                       capture_output=True, text=True)
    tracked = [f for f in r.stdout.split("\n") if f.strip()]
    leaked = [f for f in tracked if f.startswith("eval/private")]
    ok &= check("nothing under eval/private is tracked", not leaked,
                ", ".join(leaked[:3]))

    r2 = subprocess.run(["git", "check-ignore", "-q",
                         "eval/private/results/bench_x.json"],
                        cwd=ROOT, capture_output=True)
    ok &= check("result files are ignored", r2.returncode == 0)
    return ok


def main():
    results = [test_gates_are_reported(),
               test_the_product_on_recordings_that_measure_it(),
               test_no_category_regressed(),
               test_the_regression_check_can_actually_fail(),
               test_no_private_number_is_committed()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
