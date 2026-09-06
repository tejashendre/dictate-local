"""The private real-voice corpus: what one record holds, and what makes it valid.

The synthetic corpus in tests/audio ranks models against each other honestly,
but it is Windows SAPI speech. It is not Tejas's voice, his microphone, or his
room, and every number this project has published so far carries that caveat.
This module defines the format for the corpus that removes it.

Two fields exist that a plain speech corpus does not have, and they are the
point of the whole file:

    said     the literal words that left the speaker's mouth, including the
             false starts, the "um", and the sentence he abandoned halfway
    want     the text he wanted to end up in the document

For ordinary dictation these are nearly the same. For a self-correction they
are not:

    said : "send it to Naukri, no wait, to Instahyre"
    want : "send it to Instahyre"

Scoring raw ASR against `want` would punish a perfect transcription. Scoring
the final product against `said` would reward one that ignored the correction.
So the scorer measures raw output against `said` and final output against
`want`, and those are different questions that must not be averaged together.

Everything here is data definition and validation. Nothing in this module
records audio, loads a model, or touches the network.
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "private")
AUDIO_DIR = os.path.join(PRIVATE, "audio")
CORPUS_PATH = os.path.join(PRIVATE, "corpus.jsonl")

# Categories. Each one exists because it is a distinct way dictation fails,
# not because it makes a tidy list. The scorer reports every metric sliced by
# these, so a model that is better overall but worse on names is visible as
# exactly that rather than hidden inside an average.
CATEGORIES = (
    "ordinary",         # plain English at a normal pace, the bulk of real use
    "fast",             # at the measured 147 wpm peak, where words run together
    "long",             # one sentence past 30 words, where context drifts
    "names",            # people and companies a general model cannot know
    "dates_times",      # "the 3rd of March at half past four"
    "numbers_money",    # "one lakh twenty thousand", "EUR 1.6 billion"
    "urls_emails",      # character-level accuracy, no room for a near miss
    "technical",        # int8_float16, WS_EX_NOACTIVATE, ctranslate2
    "fillers",          # um, uh, you know, I mean
    "false_starts",     # an abandoned clause, no explicit correction
    "self_correction",  # "no wait", "sorry, I meant", "scratch that"
    "quiet",            # the room as it normally is
    "fan_noise",        # laptop fan under load, the common real condition
    "tv_distant",       # a television or another person talking across the room
    "silence",          # nothing said at all, the control that must stay empty
)

# Conditions the recorder asks about, kept separate from category because a
# names phrase can be recorded in any of them.
NOISE_CONDITIONS = ("quiet", "fan", "tv", "distant_speech", "unknown")

REQUIRED = ("id", "audio", "said", "want", "category", "microphone",
            "noise", "protected_names", "protected_numbers")


class CorpusError(ValueError):
    """A record that would produce a meaningless score if it were used."""


def new_record(rec_id, audio, said, want, category, microphone,
               noise="quiet", protected_names=(), protected_numbers=(),
               notes=""):
    """Build one record. Does not write anything."""
    return {
        "id": rec_id,
        "audio": audio,
        "said": said,
        "want": want,
        "category": category,
        "microphone": microphone,
        "noise": noise,
        "protected_names": list(protected_names),
        "protected_numbers": list(protected_numbers),
        "notes": notes,
    }


def _numbers_in(text):
    """Digit runs and the currency or unit attached to them.

    Deliberately literal. "1.6" and "16" are different protected values, and a
    scorer that normalised them would stop being able to tell a correct
    transcription from an expensive typo.

    A trailing period is stripped because it belongs to the sentence, not to
    the number: "August 2024." must satisfy a protected value of "2024", or
    every number ending a sentence is scored as wrong forever.
    """
    return [n.rstrip(".,") for n in re.findall(r"\d[\d,.]*", text or "")]


def validate(rec, strict_audio=True):
    """Return a list of problems. Empty list means the record is usable.

    Silence records are the one place where an empty `want` is correct, so the
    emptiness check is category-aware rather than blanket.
    """
    problems = []
    for field in REQUIRED:
        if field not in rec:
            problems.append("missing field: %s" % field)
    if problems:
        return problems

    if rec["category"] not in CATEGORIES:
        problems.append("unknown category: %r" % rec["category"])
    if rec["noise"] not in NOISE_CONDITIONS:
        problems.append("unknown noise condition: %r" % rec["noise"])

    silent = rec["category"] == "silence"
    if silent:
        if (rec["said"] or "").strip():
            problems.append("a silence record must have an empty 'said'")
        if (rec["want"] or "").strip():
            problems.append("a silence record must have an empty 'want'")
    else:
        if not (rec["said"] or "").strip():
            problems.append("'said' is empty and the category is not silence")
        if not (rec["want"] or "").strip():
            problems.append("'want' is empty and the category is not silence")

    # A protected value that is not actually in the desired text is a typo in
    # the corpus, and it would score every model as failing forever.
    low_want = (rec["want"] or "").lower()
    for name in rec["protected_names"]:
        if name.lower() not in low_want:
            problems.append("protected name %r does not appear in 'want'" % name)
    want_numbers = set(_numbers_in(rec["want"]))
    for num in rec["protected_numbers"]:
        if num not in want_numbers:
            problems.append("protected number %r does not appear in 'want'" % num)

    if strict_audio:
        path = rec["audio"]
        if not os.path.isabs(path):
            path = os.path.join(PRIVATE, path)
        if not os.path.exists(path):
            problems.append("audio file not found: %s" % rec["audio"])
    return problems


def append(rec, path=None, strict_audio=True):
    """Append one validated record to the private corpus.

    strict_audio stays on for the recorder, which writes the wav before it
    calls this and would otherwise be able to log a recording that does not
    exist. It is turned off only by tests, which have no audio to point at.
    """
    problems = validate(rec, strict_audio=strict_audio)
    if problems:
        raise CorpusError("; ".join(problems))
    path = path or CORPUS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load(path=None, strict_audio=True):
    """Read the corpus. Returns (records, problems).

    A bad line never stops the load: a corpus of 200 recordings with one typo
    should still be scoreable on the other 199, and the caller decides whether
    the problems matter.
    """
    path = path or CORPUS_PATH
    records, problems = [], []
    if not os.path.exists(path):
        return records, ["corpus not found: %s" % path]
    with io.open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except Exception as e:
                problems.append("line %d is not JSON: %s" % (n, e))
                continue
            bad = validate(rec, strict_audio=strict_audio)
            if bad:
                problems.append("line %d (%s): %s"
                                % (n, rec.get("id", "?"), "; ".join(bad)))
                continue
            records.append(rec)
    return records, problems


def coverage(records):
    """How many recordings exist per category, so gaps are visible.

    A corpus that is 90 percent ordinary dictation cannot answer whether a
    model is better at names, and the summary should say so before anyone
    spends an afternoon on a benchmark that cannot conclude anything.
    """
    counts = {c: 0 for c in CATEGORIES}
    for rec in records:
        if rec["category"] in counts:
            counts[rec["category"]] += 1
    return counts


def audio_path(rec):
    """Absolute path to a record's audio."""
    path = rec["audio"]
    return path if os.path.isabs(path) else os.path.join(PRIVATE, path)
