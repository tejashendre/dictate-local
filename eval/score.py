"""Scoring. What was heard, what was fixed, and what actually landed.

Three separate questions, deliberately never averaged into one:

    raw        what the model returned, before any of this project's layers.
               Scored against `said`, the literal words spoken.
    corrected  after corrections and near-miss snapping. Still scored against
               `said`, because those layers fix spelling and not intent.
    final      after the polish and command layers. Scored against `want`,
               the text that should have ended up in the document.

The reason for the split is that this project has three accuracy layers
stacked on top of the model, and they are very good at hiding a weak model.
A candidate that transcribes "nokri" and gets snapped to "Naukri" scores
identically at the final level to one that heard it correctly, and those are
not the same model. Raw accuracy is what says whether a bigger model is
actually earning its VRAM.

Nothing here loads a model or records audio. Given a reference and a
hypothesis it returns numbers, which is what makes it testable without a GPU.
"""
import re

# Words that a self-correction leaves behind and that no scorer should count
# as a real word. Kept short on purpose: an aggressive list would start
# deleting genuine content.
_PUNCT = re.compile(r"[^a-z0-9@./:+\-\s]")


def words(text):
    """Comparable tokens. Keeps the characters that carry meaning in an
    address or a version number, drops the rest."""
    if not text:
        return []
    low = text.lower().replace("’", "'")
    return _PUNCT.sub(" ", low).split()


def align(ref, hyp):
    """Levenshtein with a backtrace.

    Returns (substitutions, deletions, insertions, hits). Deletions are words
    the model missed, insertions are words it invented, and the two need to be
    reported separately because they fail differently: a missed word is a
    retype, an invented word is a sentence that says something you did not.
    """
    r, h = words(ref), words(hyp)
    n, m = len(r), len(h)
    if n == 0 and m == 0:
        return 0, 0, 0, 0
    if n == 0:
        return 0, 0, m, 0
    if m == 0:
        return 0, n, 0, 0

    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)

    sub = dele = ins = hit = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (0 if r[i - 1] == h[j - 1] else 1):
            if r[i - 1] == h[j - 1]:
                hit += 1
            else:
                sub += 1
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return sub, dele, ins, hit


def wer(ref, hyp):
    """Word error rate against the reference length. 0.0 for two empty strings."""
    r = words(ref)
    if not r:
        return 0.0 if not words(hyp) else 1.0
    sub, dele, ins, _hit = align(ref, hyp)
    return (sub + dele + ins) / len(r)


def exact(ref, hyp):
    """Token-identical after normalisation. The metric a user actually feels,
    because one wrong word still means reaching for the keyboard."""
    return words(ref) == words(hyp)


def protected_names(names, hyp):
    """(found, total). Case-insensitive substring, because a name that arrives
    with different capitalisation has still been recognised."""
    if not names:
        return 0, 0
    low = (hyp or "").lower()
    found = sum(1 for n in names if n.lower() in low)
    return found, len(names)


def protected_numbers(numbers, hyp):
    """(found, total). Compared literally: 1.6 and 16 are different values and
    a scorer that normalised them would stop detecting an expensive error."""
    if not numbers:
        return 0, 0
    got = set(re.findall(r"\d[\d,.]*", hyp or ""))
    # A trailing period from sentence punctuation must not fail a match.
    got |= {g.rstrip(".") for g in got}
    found = sum(1 for n in numbers if n in got or n.rstrip(".") in got)
    return found, len(numbers)


def hallucinated_on_silence(hyp):
    """True when a silent recording produced any word at all.

    This is the check that exists because half a second of near-silence once
    produced a confident "Thank you for watching." and typed it into whatever
    window was focused.
    """
    return len(words(hyp)) > 0


def score_one(rec, raw, corrected, final, seconds=None, audio_seconds=None):
    """Every metric for a single recording, at all three levels."""
    said, want = rec.get("said", ""), rec.get("want", "")
    silent = rec.get("category") == "silence"

    sub_r, del_r, ins_r, _ = align(said, raw)
    n_f, t_f = protected_names(rec.get("protected_names", ()), final)
    n_r, t_r = protected_names(rec.get("protected_names", ()), raw)
    d_f, u_f = protected_numbers(rec.get("protected_numbers", ()), final)

    out = {
        "id": rec.get("id"),
        "category": rec.get("category"),
        "noise": rec.get("noise"),
        "silent": silent,

        "wer_raw": wer(said, raw),
        "wer_corrected": wer(said, corrected),
        "wer_final": wer(want, final),

        "exact_raw": exact(said, raw),
        "exact_final": exact(want, final),

        "missing_words": del_r,
        "added_words": ins_r,
        "substituted_words": sub_r,

        "names_found_raw": n_r, "names_total": t_r,
        "names_found_final": n_f,
        "numbers_found_final": d_f, "numbers_total": u_f,

        "seconds": seconds,
        "audio_seconds": audio_seconds,
        "rtf": (audio_seconds / seconds) if (seconds and audio_seconds) else None,
    }
    if silent:
        out["silence_false_positive"] = hallucinated_on_silence(final)
        out["silence_text"] = (final or "")[:80]
    return out


def aggregate(rows):
    """Roll per-recording scores up, overall and by category."""
    def mean(key, subset):
        vals = [r[key] for r in subset if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    speech = [r for r in rows if not r["silent"]]
    silent = [r for r in rows if r["silent"]]

    names_found = sum(r["names_found_final"] for r in speech)
    names_total = sum(r["names_total"] for r in speech)
    nums_found = sum(r["numbers_found_final"] for r in speech)
    nums_total = sum(r["numbers_total"] for r in speech)
    raw_names_found = sum(r["names_found_raw"] for r in speech)

    summary = {
        "recordings": len(rows),
        "speech": len(speech),
        "silence": len(silent),

        "wer_raw": mean("wer_raw", speech),
        "wer_corrected": mean("wer_corrected", speech),
        "wer_final": mean("wer_final", speech),

        "exact_raw": (sum(1 for r in speech if r["exact_raw"]) / len(speech)) if speech else 0.0,
        "exact_final": (sum(1 for r in speech if r["exact_final"]) / len(speech)) if speech else 0.0,

        "name_accuracy_raw": (raw_names_found / names_total) if names_total else None,
        "name_accuracy_final": (names_found / names_total) if names_total else None,
        "names_total": names_total,
        "number_accuracy": (nums_found / nums_total) if nums_total else None,
        "numbers_total": nums_total,

        "missing_words": sum(r["missing_words"] for r in speech),
        "added_words": sum(r["added_words"] for r in speech),

        "silence_false_positive_rate":
            (sum(1 for r in silent if r.get("silence_false_positive")) / len(silent))
            if silent else None,

        "rtf": mean("rtf", speech),
        "avg_ms": 1000 * mean("seconds", speech),
    }

    by_cat = {}
    for row in rows:
        cat = row["category"]
        by_cat.setdefault(cat, []).append(row)
    summary["by_category"] = {
        cat: {
            "n": len(subset),
            "wer_raw": mean("wer_raw", [r for r in subset if not r["silent"]]),
            "wer_final": mean("wer_final", [r for r in subset if not r["silent"]]),
            "exact_final": (sum(1 for r in subset if not r["silent"] and r["exact_final"])
                            / max(1, len([r for r in subset if not r["silent"]]))),
            "added_words": sum(r["added_words"] for r in subset),
            "missing_words": sum(r["missing_words"] for r in subset),
        }
        for cat, subset in by_cat.items()
    }
    return summary


def report(summary, name="", resources=None):
    """A short human-readable block. The JSON is the record; this is the read."""
    L = []
    L.append("  %s" % (name or "results"))
    L.append("  " + "-" * 66)
    L.append("  %-26s %d speech, %d silence"
             % ("recordings", summary["speech"], summary["silence"]))
    L.append("")
    L.append("  %-26s %6.1f%%   (model alone, vs what was said)"
             % ("WER raw", 100 * summary["wer_raw"]))
    L.append("  %-26s %6.1f%%   (after corrections and snapping)"
             % ("WER corrected", 100 * summary["wer_corrected"]))
    L.append("  %-26s %6.1f%%   (final text, vs what was wanted)"
             % ("WER final", 100 * summary["wer_final"]))
    L.append("")
    L.append("  %-26s %6.1f%%" % ("exact match, raw", 100 * summary["exact_raw"]))
    L.append("  %-26s %6.1f%%" % ("exact match, final", 100 * summary["exact_final"]))

    if summary.get("names_total"):
        raw_acc = summary.get("name_accuracy_raw")
        L.append("  %-26s %6.1f%%   (raw %.1f%%, of %d names)"
                 % ("protected names", 100 * summary["name_accuracy_final"],
                    100 * (raw_acc or 0.0), summary["names_total"]))
    if summary.get("numbers_total"):
        L.append("  %-26s %6.1f%%   (of %d numbers)"
                 % ("protected numbers", 100 * summary["number_accuracy"],
                    summary["numbers_total"]))
    L.append("")
    L.append("  %-26s %6d words" % ("missed", summary["missing_words"]))
    L.append("  %-26s %6d words   (invented, never spoken)"
             % ("added", summary["added_words"]))
    fp = summary.get("silence_false_positive_rate")
    if fp is not None:
        L.append("  %-26s %6.1f%%   (text produced from silence)"
                 % ("silence false positives", 100 * fp))
    L.append("")
    L.append("  %-26s %6.1fx realtime, %.0f ms per phrase"
             % ("speed", summary["rtf"], summary["avg_ms"]))
    if resources:
        L.append("  %-26s %6d MB VRAM, %d MB RAM, %.1fs to load"
                 % ("resources", resources.get("vram", 0),
                    resources.get("ram", 0), resources.get("load_s", 0.0)))

    L.append("")
    L.append("  by category")
    L.append("  %-18s %4s %10s %10s %8s %8s"
             % ("", "n", "WER raw", "WER final", "added", "missed"))
    for cat, c in sorted(summary["by_category"].items()):
        L.append("  %-18s %4d %9.1f%% %9.1f%% %8d %8d"
                 % (cat, c["n"], 100 * c["wer_raw"], 100 * c["wer_final"],
                    c["added_words"], c["missing_words"]))
    return "\n".join(L)
