"""The evaluation harness, checked before it is trusted to judge a model.

A scorer nobody has tested is worse than no scorer: it produces confident
numbers, and a wrong number is how a worse model gets shipped. So this suite
does to the scorer what the rest of the suite does to the application, which is
give it inputs whose correct answer is known by hand.

The case that matters most is the three-level split. This project stacks three
correction layers on top of the model, and they are very good at hiding a weak
one. A candidate that hears "nokri" and gets snapped to "Naukri" must not score
the same as one that heard it correctly, or the benchmark will recommend the
cheaper model forever.

    python tests/test_eval.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

import corpus as C          # noqa: E402
import score as S           # noqa: E402
import phrases_english      # noqa: E402


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def test_scorer_arithmetic():
    print("\n  1. the scorer agrees with hand-counted answers")
    ok = True

    ok &= check("identical text is 0% WER", S.wer("the report is done",
                                                  "the report is done") == 0.0)
    ok &= check("one wrong word in four is 25%",
                abs(S.wer("the report is done", "the report is gone") - 0.25) < 1e-9)
    ok &= check("a missed word counts once",
                abs(S.wer("the report is done", "the report done") - 0.25) < 1e-9)
    ok &= check("an invented word counts once",
                abs(S.wer("the report is done",
                          "the report is really done") - 0.25) < 1e-9)
    ok &= check("two empty strings are 0%, not an error",
                S.wer("", "") == 0.0)
    ok &= check("words from nothing is a total miss",
                S.wer("", "thank you for watching") == 1.0)

    print("\n  2. missed and invented words are counted separately")
    # They fail differently. A missed word is a retype; an invented word is a
    # sentence that says something the speaker never said.
    sub, dele, ins, hit = S.align("send it to Naukri today",
                                  "send it to Naukri")
    ok &= check("a dropped word is a deletion, not an insertion",
                dele == 1 and ins == 0, "sub=%d del=%d ins=%d" % (sub, dele, ins))
    sub, dele, ins, hit = S.align("send it to Naukri",
                                  "send it to Naukri today please")
    ok &= check("added words are insertions", ins == 2 and dele == 0,
                "sub=%d del=%d ins=%d" % (sub, dele, ins))
    sub, dele, ins, hit = S.align("the meeting is at four",
                                  "the meeting is at five")
    ok &= check("a swapped word is a substitution", sub == 1 and ins == 0 and dele == 0)
    return ok


def test_protected_values():
    print("\n  3. protected names and numbers")
    ok = True
    found, total = S.protected_names(["Naukri", "Zalando"],
                                     "I applied to zalando through Naukri")
    ok &= check("case does not matter for a name", found == 2 and total == 2)
    found, _ = S.protected_names(["Instahyre"], "I applied through Naukri")
    ok &= check("a missing name is not credited", found == 0)

    found, total = S.protected_numbers(["1.6"], "projected 1.6 billion euros")
    ok &= check("a decimal is matched exactly", found == 1)
    found, _ = S.protected_numbers(["1.6"], "projected 16 billion euros")
    ok &= check("16 does not satisfy 1.6", found == 0,
                "this is the expensive-typo case")
    found, _ = S.protected_numbers(["2026"], "the deadline is November 2026.")
    ok &= check("trailing sentence punctuation still matches", found == 1)
    return ok


def test_silence_is_caught():
    print("\n  4. silence that produces words is a failure, not a pass")
    # Real failure from this project's own history: half a second of
    # near-silence produced a confident "Thank you for watching." and typed it
    # into whatever window had focus.
    ok = True
    ok &= check("a silent recording transcribed as text is flagged",
                S.hallucinated_on_silence("Thank you for watching."))
    ok &= check("genuine silence is not flagged",
                not S.hallucinated_on_silence(""))
    ok &= check("whitespace only is not flagged",
                not S.hallucinated_on_silence("   \n "))

    rec = {"id": "silence_000", "category": "silence", "said": "", "want": "",
           "protected_names": [], "protected_numbers": []}
    row = S.score_one(rec, "Thank you for watching.", "Thank you for watching.",
                      "Thank you for watching.")
    ok &= check("score_one marks the false positive",
                row.get("silence_false_positive") is True)

    summary = S.aggregate([row])
    ok &= check("it reaches the summary as a rate",
                summary["silence_false_positive_rate"] == 1.0)
    return ok


def test_layers_do_not_hide_a_weak_model():
    print("\n  5. correction layers cannot hide a weak model")
    # The whole reason raw is scored separately. Both candidates below produce
    # the same final text; only one of them actually heard the word.
    rec = {"id": "names_000", "category": "names",
           "said": "I applied through Naukri",
           "want": "I applied through Naukri",
           "protected_names": ["Naukri"], "protected_numbers": []}

    heard = S.score_one(rec, "I applied through Naukri",
                        "I applied through Naukri", "I applied through Naukri")
    snapped = S.score_one(rec, "I applied through nokri",
                          "I applied through Naukri", "I applied through Naukri")

    ok = True
    ok &= check("both end at the same final text",
                heard["wer_final"] == snapped["wer_final"] == 0.0)
    ok &= check("but raw WER separates them",
                snapped["wer_raw"] > heard["wer_raw"],
                "raw %.2f vs %.2f" % (snapped["wer_raw"], heard["wer_raw"]))
    ok &= check("raw name accuracy separates them too",
                heard["names_found_raw"] == 1 and snapped["names_found_raw"] == 0)
    ok &= check("final name accuracy is identical, as it should be",
                heard["names_found_final"] == snapped["names_found_final"] == 1)
    return ok


def test_said_versus_want():
    print("\n  6. self-corrections are scored against the right reference")
    # said and want differ. Raw output is judged on what was spoken; the final
    # product is judged on what was meant.
    rec = {"id": "self_correction_000", "category": "self_correction",
           "said": "Send it to Naukri, no wait, send it to Instahyre",
           "want": "Send it to Instahyre",
           "protected_names": ["Instahyre"], "protected_numbers": []}

    perfect_raw = "Send it to Naukri, no wait, send it to Instahyre"
    row = S.score_one(rec, perfect_raw, perfect_raw, "Send it to Instahyre")
    ok = True
    ok &= check("a perfect verbatim transcription scores 0% raw",
                row["wer_raw"] == 0.0,
                "it must not be punished for transcribing the correction")
    ok &= check("and the resolved final text scores 0% final",
                row["wer_final"] == 0.0)

    unresolved = S.score_one(rec, perfect_raw, perfect_raw, perfect_raw)
    ok &= check("leaving the correction in the output is a final-level failure",
                unresolved["wer_final"] > 0.0,
                "%.2f" % unresolved["wer_final"])
    return ok


def test_corpus_validation():
    print("\n  7. the corpus format rejects records that would score wrongly")
    ok = True
    good = C.new_record("names_000", "audio/x.wav", "I applied to Zalando",
                        "I applied to Zalando", "names", "mic",
                        protected_names=["Zalando"])
    ok &= check("a well-formed record validates",
                C.validate(good, strict_audio=False) == [])

    bad = C.new_record("names_001", "audio/x.wav", "I applied to Zalando",
                       "I applied to Zalando", "names", "mic",
                       protected_names=["Instahyre"])
    problems = C.validate(bad, strict_audio=False)
    ok &= check("a protected name absent from 'want' is rejected",
                any("does not appear" in p for p in problems),
                "otherwise every model scores as failing forever")

    bad2 = C.new_record("numbers_money_000", "audio/x.wav", "it was 48 hours",
                        "it was 48 hours", "numbers_money", "mic",
                        protected_numbers=["24"])
    ok &= check("a protected number absent from 'want' is rejected",
                any("does not appear" in p
                    for p in C.validate(bad2, strict_audio=False)))

    empty = C.new_record("ordinary_000", "audio/x.wav", "", "",
                         "ordinary", "mic")
    ok &= check("an empty non-silence record is rejected",
                any("empty" in p for p in C.validate(empty, strict_audio=False)))

    silent = C.new_record("silence_000", "audio/x.wav", "", "", "silence", "mic")
    ok &= check("but an empty silence record is valid",
                C.validate(silent, strict_audio=False) == [])

    noisy = C.new_record("silence_001", "audio/x.wav", "hello", "hello",
                         "silence", "mic")
    ok &= check("a silence record with words is rejected",
                any("empty" in p for p in C.validate(noisy, strict_audio=False)))

    unknown = C.new_record("x_000", "audio/x.wav", "a", "a", "not_a_category",
                           "mic")
    ok &= check("an unknown category is rejected",
                any("category" in p for p in C.validate(unknown, strict_audio=False)))
    return ok


def test_corpus_round_trip():
    print("\n  8. the corpus survives a write and read")
    ok = True
    d = tempfile.mkdtemp()
    path = os.path.join(d, "corpus.jsonl")
    rec = C.new_record("ordinary_000", "audio/a.wav", "hello there",
                       "hello there", "ordinary", "Microphone Array")
    C.append(rec, path=path, strict_audio=False)
    C.append(C.new_record("silence_000", "audio/b.wav", "", "", "silence",
                          "Microphone Array"), path=path, strict_audio=False)
    records, problems = C.load(path, strict_audio=False)
    ok &= check("both records read back", len(records) == 2, str(problems[:2]))
    ok &= check("fields survive the round trip",
                records[0]["said"] == "hello there"
                and records[0]["microphone"] == "Microphone Array")

    with open(path, "a", encoding="utf-8") as f:
        f.write("{not json\n")
    records, problems = C.load(path, strict_audio=False)
    ok &= check("a corrupt line is reported, not fatal",
                len(records) == 2 and len(problems) == 1,
                "%d records, %d problems" % (len(records), len(problems)))
    return ok


def test_phrase_coverage():
    print("\n  9. the phrase list covers every category it claims to")
    ok = True
    counts = phrases_english.summary()
    missing = [c for c in C.CATEGORIES if counts.get(c, 0) == 0]
    ok &= check("every category has at least one phrase", not missing,
                str(missing))
    ok &= check("silence controls exist", counts.get("silence", 0) >= 3,
                "%d" % counts.get("silence", 0))

    # A phrase whose protected values are not in its own `want` would poison
    # the corpus at record time, before any model is involved.
    bad = []
    for cat, said, want, names, numbers in phrases_english.PHRASES:
        rec = C.new_record("x", "audio/x.wav", said, want, cat, "mic",
                           protected_names=names, protected_numbers=numbers)
        problems = C.validate(rec, strict_audio=False)
        if problems:
            bad.append("%s: %s" % (cat, problems[0]))
    ok &= check("every phrase in the list is itself a valid record",
                not bad, "; ".join(bad[:3]))

    differ = [p for p in phrases_english.PHRASES if p[1] != p[2] and p[0] != "silence"]
    ok &= check("some phrases have said != want, or cleanup is untested",
                len(differ) >= 8, "%d such phrases" % len(differ))
    return ok


def test_duration_guard():
    print("\n12. a take that is the wrong audio is caught before scoring")
    # Found in real use, not in design. Two of the first nine recordings were
    # a microphone test and a minute of unrelated talking. Both were perfectly
    # valid records, and they moved the ordinary category from roughly 8
    # percent word error to 103, which reads as a catastrophic model failure
    # rather than as bad data.
    ok = True
    said = "I need to finish the report before the meeting tomorrow morning."
    rec = C.new_record("ordinary_000", "audio/x.wav", said, said,
                       "ordinary", "mic")

    good = C.expected_seconds(said)
    ok &= check("a phrase has a plausible expected duration",
                2.0 < good < 8.0, "%.1fs for %d words" % (good, len(said.split())))
    ok &= check("a take at the expected length passes",
                C.duration_problem(rec, good) is None)
    ok &= check("a take at 1.2x still passes",
                C.duration_problem(rec, good * 1.2) is None,
                "reading pace varies, the guard must not be twitchy")
    ok &= check("a 12x take is flagged",
                C.duration_problem(rec, good * 12) is not None,
                "this is the 49-second microphone test")
    ok &= check("a 3x take is flagged",
                C.duration_problem(rec, good * 3) is not None,
                "this is the minute of unrelated talking")
    ok &= check("a take cut to a fifth is flagged",
                C.duration_problem(rec, good * 0.2) is not None)

    silent = C.new_record("silence_000", "audio/x.wav", "", "", "silence", "mic")
    ok &= check("a short silence take is not flagged",
                C.duration_problem(silent, 3.0) is None,
                "an empty phrase has no expected duration")
    ok &= check("but a very long silence take is",
                C.duration_problem(silent, 60.0) is not None)
    return ok


def test_drop_replaces_a_take():
    print("\n13. re-recording replaces a take instead of duplicating it")
    ok = True
    d = tempfile.mkdtemp()
    path = os.path.join(d, "corpus.jsonl")
    for text in ("first take", "second take"):
        C.append(C.new_record("ordinary_000", "audio/a.wav", text, text,
                              "ordinary", "mic"), path=path, strict_audio=False)
    records, _ = C.load(path, strict_audio=False)
    ok &= check("two appends of one id give two records", len(records) == 2,
                "which would score the bad take and the good one separately")

    removed = C.drop("ordinary_000", path=path)
    ok &= check("drop removes both", removed == 2)
    records, _ = C.load(path, strict_audio=False)
    ok &= check("the corpus is empty afterwards", len(records) == 0)

    C.append(C.new_record("ordinary_000", "audio/a.wav", "good take",
                          "good take", "ordinary", "mic"),
             path=path, strict_audio=False)
    records, _ = C.load(path, strict_audio=False)
    ok &= check("and the replacement stands alone",
                len(records) == 1 and records[0]["said"] == "good take")
    ok &= check("dropping an absent id is harmless",
                C.drop("does_not_exist", path=path) == 0)
    return ok


def test_private_data_is_ignored():
    print("\n  10. no real audio or transcript can be committed")
    ok = True
    probes = ["eval/private/audio/ordinary_000.wav",
              "eval/private/corpus.jsonl",
              "eval/private/results/bench_20260906_120000.json"]
    for rel in probes:
        r = subprocess.run(["git", "check-ignore", "-q", rel],
                           cwd=ROOT, capture_output=True)
        ok &= check("ignored: %s" % rel, r.returncode == 0)

    r = subprocess.run(["git", "ls-files", "eval/private"],
                       cwd=ROOT, capture_output=True, text=True)
    ok &= check("nothing under eval/private is tracked",
                not r.stdout.strip(), r.stdout.strip()[:60])

    # The harness itself must be committed, or none of this is reproducible.
    r = subprocess.run(["git", "check-ignore", "-q", "eval/score.py"],
                       cwd=ROOT, capture_output=True)
    ok &= check("but the harness itself is NOT ignored", r.returncode != 0)
    return ok


def test_report_renders():
    print("\n  11. the report renders without a model present")
    rows = []
    for i in range(3):
        rows.append(S.score_one(
            {"id": "ordinary_%03d" % i, "category": "ordinary",
             "said": "the report is done", "want": "the report is done",
             "protected_names": [], "protected_numbers": []},
            "the report is done", "the report is done", "the report is done",
            seconds=0.2, audio_seconds=2.4))
    rows.append(S.score_one(
        {"id": "silence_000", "category": "silence", "said": "", "want": "",
         "protected_names": [], "protected_numbers": []}, "", "", ""))
    summary = S.aggregate(rows)
    text = S.report(summary, name="probe",
                    resources={"vram": 433, "ram": 900, "load_s": 2.5})
    ok = check("a report is produced", len(text) > 200)
    ok &= check("it separates raw from final",
                "WER raw" in text and "WER final" in text)
    ok &= check("it reports by category", "by category" in text)
    ok &= check("realtime factor is computed", "12.0x realtime" in text,
                "2.4s audio in 0.2s")
    return ok


def main():
    results = [test_scorer_arithmetic(), test_protected_values(),
               test_silence_is_caught(), test_layers_do_not_hide_a_weak_model(),
               test_said_versus_want(), test_corpus_validation(),
               test_corpus_round_trip(), test_phrase_coverage(),
               test_duration_guard(), test_drop_replaces_a_take(),
               test_private_data_is_ignored(), test_report_renders()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
