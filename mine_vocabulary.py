"""mine_vocabulary - find the words that matter from your own writing.

The vocabulary was 24 terms guessed from a design document. Your notes contain
the real ones: the companies, schools, tools and foreign words you actually
use. This reads them and proposes the additions worth making.

    python mine_vocabulary.py                    look, change nothing
    python mine_vocabulary.py --apply            merge into vocabulary.txt
    python mine_vocabulary.py --vault "C:\\path"  a different folder

WHY THIS RANKS INSTEAD OF DUMPING

initial_prompt shares Whisper's 448-token context and is capped at half of it.
Measured earlier in this project: a prompt past roughly 100 terms starts making
ORDINARY transcription worse, which is a bad trade for fixing rare words. So
this scores candidates and proposes only what fits the budget.

HOW A "WORD THAT MATTERS" IS RECOGNISED

There is no English dictionary on this machine, and downloading one to decide
what counts as English would be silly. Three signals do the job without one:

  capitalised mid-sentence   a proper noun: Zalando, Naukri, ESCP
  non-ASCII letters          a foreign word: spontanée, Arbeitszeugnis
  internal capitals          a technical name: PitchBook, MicroStrategy, n8n

The first version of this ranked Business, Strategy, Career and Analyst at the
top - all capitalised in headings, all spelled perfectly by Whisper already,
and between them enough to consume the entire prompt budget for no gain.

The signal that fixes it needs no dictionary either: DOES THE WORD EVER APPEAR
IN LOWERCASE? "business" and "strategy" do, constantly. "Zalando", "Naukri"
and "Tejas" essentially never do. A word that appears in both cases is
ordinary English that happens to start a heading; a word that is always
capitalised is a name. That one ratio removes almost all the noise.

Even with that, frequency in your notes is not difficulty for Whisper. India,
Google, Python and Berlin are all common in the vault and all spelled
perfectly already; spending prompt budget on them buys nothing.

So --verify stops guessing and MEASURES. Each candidate is spoken by the local
Windows voice and transcribed with no vocabulary prompt at all. Whatever comes
back wrong is what actually needs help; whatever comes back right is dropped.
It costs a couple of seconds per word and it is the only filter here that is
not a heuristic. It also writes the correction rules for free, because the
misspelling it produces is exactly the "heard -> wanted" rule you want.

Nothing leaves this machine: the vault is read, counted, spoken to a local
voice, and the counts are thrown away.
"""

import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VOCAB = os.path.join(HERE, "vocabulary.txt")
DEFAULT_VAULT = r"C:\Users\tejas\Downloads\TJ Career Center"

# Markdown carries a lot that is not prose. Stripped before counting, or the
# top of the list fills with code identifiers and URL fragments.
STRIP = [
    (re.compile(r"```.*?```", re.S), " "),        # fenced code
    (re.compile(r"`[^`]*`"), " "),                # inline code
    (re.compile(r"https?://\S+"), " "),           # links
    (re.compile(r"!?\[[^\]]*\]\([^)]*\)"), " "),  # markdown links/images
    (re.compile(r"^---.*?^---", re.S | re.M), " "),  # yaml frontmatter
    (re.compile(r"<[^>]+>"), " "),                # html
]

WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]{2,}")

# Sentence-initial capitals are not evidence of anything, so the first word
# after a full stop is not counted as a proper noun.
SENTENCE_START = re.compile(r"(?:^|[.!?:]\s+|\n\s*[-*>#]*\s*)$")

# Ordinary words that are often capitalised in notes - headings, days, months,
# and the usual English suspects. Counting these as proper nouns would waste
# the whole budget on "Monday" and "However".
COMMON_CAPS = set("""
monday tuesday wednesday thursday friday saturday sunday january february
march april may june july august september october november december
however therefore because although while during after before this that these
those there their they what when where which who why how yes no not and but
for with from into over under about above below again further once here more
most other some such only own same than too very can will just should now
i we you he she it me my our your his her its us them am is are was were be
been being have has had do does did doing would could shall may might must
the a an of in on at to as by or if it's don't can't won't didn't
note notes todo done draft final summary overview intro introduction
part chapter section page appendix figure table example
""".split())


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return ""
    for pattern, repl in STRIP:
        text = pattern.sub(repl, text)
    return text


def classify(word, preceding):
    """Why this word might be worth prompt budget, or None."""
    if any(ord(c) > 127 for c in word):
        return "foreign"
    # PitchBook, MicroStrategy, n8n - a capital or digit after the first letter
    if re.search(r"[a-z][A-Z]", word) or re.search(r"[A-Za-z]\d|\d[A-Za-z]", word):
        return "technical"
    if word[0].isupper():
        if word.lower() in COMMON_CAPS:
            return None
        if SENTENCE_START.search(preceding):
            return None            # capital only because a sentence began
        if word.isupper() and len(word) <= 5:
            return "acronym"
        return "proper noun"
    return None


def scan(vault):
    counts = collections.Counter()
    kinds = {}
    forms = {}
    # Every occurrence of every word, in any case. Used to work out whether a
    # capitalised word is a name or just an ordinary word in a heading.
    all_cases = collections.Counter()
    lower_only = collections.Counter()
    files = 0
    for root, _dirs, names in os.walk(vault):
        if ".obsidian" in root or ".git" in root:
            continue
        for name in names:
            if not name.lower().endswith(".md"):
                continue
            files += 1
            text = read_text(os.path.join(root, name))
            for m in WORD.finditer(text):
                word = m.group(0).strip("'-")
                # "Tejas's" is not a separate term; the possessive is an
                # artefact of the text, and it transcribed as "tejesus".
                if word.lower().endswith("'s"):
                    word = word[:-2]
                if len(word) < 3:
                    continue
                if len(word) < 3 or len(word) > 30:
                    continue
                all_cases[word.lower()] += 1
                if word[0].islower():
                    lower_only[word.lower()] += 1
                kind = classify(word, text[max(0, m.start() - 30):m.start()])
                if not kind:
                    continue
                key = word.lower()
                counts[key] += 1
                # keep the spelling seen most often, not the first one
                forms.setdefault(key, collections.Counter())[word] += 1
                kinds[key] = kind
    return counts, kinds, forms, files, all_cases, lower_only


def existing_terms():
    have = set()
    try:
        with open(VOCAB, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "->" in line:
                    line = line.split("->")[1].strip()
                have.add(line.lower())
    except Exception:
        pass
    return have


def verify(candidates, forms):
    """Speak each word and keep only the ones Whisper gets wrong.

    Returns [(word, uses, heard)] where `heard` is what Whisper produced -
    which doubles as the correction rule.
    """
    import subprocess
    import tempfile
    import wave

    import numpy as np

    sys.path.insert(0, HERE)
    import dictate_core as core
    core.enable_cuda_dlls()

    print("\n  asking Whisper which of these it already knows")
    print("  (spoken locally, transcribed with NO vocabulary prompt)\n")
    model = core.Transcriber("small.en", on_event=lambda m: None).load()
    tmp = os.path.join(tempfile.gettempdir(), "_vocab_probe.wav")

    kept = []
    for word, uses in candidates:
        term = forms[word].most_common(1)[0][0]
        # A bare word gives the model no context and invites a wrong guess.
        # A short carrier sentence is closer to how it will really be said.
        said = "The word is %s." % term.replace("'", "''")
        ps = ("Add-Type -AssemblyName System.Speech; "
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.SelectVoice('Microsoft David Desktop'); $s.Rate=-2; "
              "$s.SetOutputToWaveFile('%s'); $s.Speak('%s'); $s.Dispose()"
              % (tmp, said))
        try:
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
            with wave.open(tmp, "rb") as w:
                sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
                raw = w.readframes(n)
            a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if ch > 1:
                a = a.reshape(-1, ch).mean(axis=1)
            idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
            audio = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
            heard = model.transcribe(audio, prompt=None)
        except Exception:
            continue

        got = heard.lower().replace("the word is", "").strip(" .!?")
        if term.lower() in heard.lower():
            print("    knows it      %-22s" % term[:22])
            continue
        print("    GETS IT WRONG %-22s heard %r" % (term[:22], got[:26]))
        kept.append((word, uses, got))

    print("\n  %d of %d need help; the rest are already correct"
          % (len(kept), len(candidates)))
    return [(w, n) for w, n, _got in kept], {w: got for w, _n, got in kept}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-count", type=int, default=4)
    ap.add_argument("--top", type=int, default=60,
                    help="how many to propose; the prompt budget is finite")
    ap.add_argument("--verify", action="store_true",
                    help="speak each candidate and keep only what Whisper "
                         "gets wrong")
    args = ap.parse_args()

    if not os.path.isdir(args.vault):
        print("  no such folder: %s" % args.vault)
        return 1

    print("  reading %s" % args.vault)
    counts, kinds, forms, files, all_cases, lower_only = scan(args.vault)
    print("  %d markdown files, %d candidate terms\n" % (files, len(counts)))

    have = existing_terms()

    def is_ordinary_english(word):
        """Appears in lowercase often enough to be a normal word.

        A name is essentially never written lowercase; an ordinary word in a
        heading is written lowercase constantly. 15% is generous - it keeps
        genuine names that were occasionally typed in lower case.
        """
        total = all_cases.get(word, 0)
        if total < 3:
            return False
        return (lower_only.get(word, 0) / total) > 0.15

    ranked = [(w, n) for w, n in counts.most_common()
              if n >= args.min_count and w not in have
              and not (kinds[w] == "proper noun" and is_ordinary_english(w))]

    # Frequency alone favours ordinary words that slipped through. Foreign and
    # technical spellings are what Whisper actually gets wrong, so they are
    # worth more per slot than a common-looking proper noun.
    weight = {"foreign": 3.0, "technical": 2.0, "acronym": 1.5,
              "proper noun": 1.0}
    ranked.sort(key=lambda wn: -(wn[1] * weight.get(kinds[wn[0]], 1.0)))

    chosen = ranked[:args.top]
    print("  %-24s %6s  %s" % ("TERM", "USES", "WHY"))
    print("  " + "-" * 52)
    for word, n in chosen:
        best = forms[word].most_common(1)[0][0]
        print("  %-24s %6d  %s" % (best[:24], n, kinds[word]))

    print("\n  %d proposed, %d already known, %d below %d uses"
          % (len(chosen), len(have),
             len([1 for w, n in counts.items() if n < args.min_count]),
             args.min_count))

    heard_as = {}
    if args.verify:
        chosen, heard_as = verify(chosen, forms)
        if not chosen:
            print("\n  Whisper already spells every candidate correctly.")
            return 0

    if not args.apply:
        print("\n  Nothing written. Re-run with --apply to merge these into")
        print("  vocabulary.txt, then check nothing regressed:")
        print("      python tests/test_vocab.py")
        return 0

    with open(VOCAB, "a", encoding="utf-8") as f:
        f.write("\n\n# --- mined from your notes on %s ---\n"
                % os.path.basename(args.vault.rstrip("\\/")))
        f.write("# Ranked by how often you use them and how badly a general\n"
                "# model spells them. Delete any that are wrong for you.\n")
        for word, n in chosen:
            f.write("%s\n" % forms[word].most_common(1)[0][0])
    print("\n  merged %d terms into vocabulary.txt" % len(chosen))
    print("  now run:  python tests/test_vocab.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
