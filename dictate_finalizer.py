"""Phase 3: the safe hybrid finalizer.

ARCHITECTURE.md Sections 10 to 13. The measured fault this exists to fix is
that the final text was worse than the raw transcript: the model heard
correctly and the cleanup made it wrong. So every route through this module
ends at text that is either provably safe or is the deterministic candidate
unchanged.

    validated formatter result
        else deterministic corrected result
            else raw ASR transcript
                else recoverable error with no insertion

The centre of it is masking. A text model asked to punctuate a sentence will
also, sooner or later, decide that 1.6 billion reads better as 1.6 million, or
that Naukri was a typo for Naukari. It cannot do that to a value it never sees:

    Raw:      Send EUR 1,250 to Tejas by 14 September.
    Masked:   Send <P0> to <P1> by <P2>.

The formatter edits around the placeholders and the originals are put back
afterwards, so a fact that was recognised correctly cannot be lost to an
editing pass. Masking cannot repair a number the recogniser misheard, which is
why raw accuracy on those categories stays a separate release metric.

Nothing here loads a model or types. Given text it returns text, so all of it
is testable without a GPU.
"""
import difflib
import re

# --------------------------------------------------------------------------
# Section 13: application categories
# --------------------------------------------------------------------------
#
# Only the foreground executable and the focused control type are needed. No
# document content is read. Section 13 is explicit that v2 does not collect
# surrounding text, and this is the whole of what replaces it.

CHAT = "chat"
EMAIL = "email"
DOCUMENT = "document"
SEARCH = "search"
CODE_OR_TERMINAL = "code_or_terminal"
UNKNOWN = "unknown"
SECURE = "secure"

_BY_EXE = {
    "slack.exe": CHAT, "discord.exe": CHAT, "teams.exe": CHAT,
    "whatsapp.exe": CHAT, "telegram.exe": CHAT,
    "outlook.exe": EMAIL, "thunderbird.exe": EMAIL,
    "winword.exe": DOCUMENT, "notepad.exe": DOCUMENT, "wordpad.exe": DOCUMENT,
    "onenote.exe": DOCUMENT, "obsidian.exe": DOCUMENT,
    "code.exe": CODE_OR_TERMINAL, "windowsterminal.exe": CODE_OR_TERMINAL,
    "cmd.exe": CODE_OR_TERMINAL, "powershell.exe": CODE_OR_TERMINAL,
    "pwsh.exe": CODE_OR_TERMINAL, "wt.exe": CODE_OR_TERMINAL,
    "devenv.exe": CODE_OR_TERMINAL, "idea64.exe": CODE_OR_TERMINAL,
    "pycharm64.exe": CODE_OR_TERMINAL,
}

# The formatter never runs for these. Section 11.3.
NO_FORMATTER = frozenset((CODE_OR_TERMINAL, SECURE, SEARCH))


def classify_target(executable="", control_class="", is_password=False):
    """Which of the seven categories is this window?

    A password field is SECURE regardless of the application, because Section
    13 requires that nothing is inserted and no transcript is retained there.
    """
    if is_password:
        return SECURE
    control = (control_class or "").lower()
    if "password" in control:
        return SECURE
    exe = (executable or "").lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if exe in _BY_EXE:
        return _BY_EXE[exe]
    if "edit" in control and "rich" not in control:
        return SEARCH
    return UNKNOWN


# --------------------------------------------------------------------------
# Section 10: protected spans
# --------------------------------------------------------------------------
#
# Order matters. The longest and most structured patterns are matched first so
# that an email address is one span rather than a name, an at sign and a
# domain. A span that is split is a span a formatter can reorder.

_NEGATIONS = (
    "not", "no", "never", "none", "nothing", "nobody", "neither", "nor",
    "cannot", "can't", "won't", "don't", "doesn't", "didn't", "isn't",
    "aren't", "wasn't", "weren't", "shouldn't", "wouldn't", "couldn't",
    "haven't", "hasn't", "hadn't", "without",
)

_PATTERNS = (
    # email before url before bare domain, longest structure first
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("url", re.compile(r"\b(?:https?://|www\.)[^\s,]+|"
                       r"\b[\w-]+(?:\.[\w-]+)+/[^\s,]*")),
    ("domain", re.compile(r"\b[\w-]+\.(?:com|org|net|edu|gov|io|ai|dev|app|"
                          r"co|uk|eu|de|fr|es|in|me|info|biz)\b")),
    ("path", re.compile(r"\b[A-Za-z]:\\[^\s,]+|(?:\./|/)[\w./-]{3,}")),
    ("identifier", re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")),
    ("money", re.compile(r"(?:EUR|USD|GBP|INR|Rs\.?|[$€£₹])\s?\d[\d,.]*"
                         r"(?:\s?(?:million|billion|lakh|crore|k|m|bn))?",
                         re.I)),
    ("percent", re.compile(r"\b\d[\d,.]*\s?(?:percent|%)")),
    ("time", re.compile(r"\b\d{1,2}:\d{2}(?:\s?[ap]\.?m\.?)?\b", re.I)),
    ("date", re.compile(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:January|February|March|"
        r"April|May|June|July|August|September|October|November|December)"
        r"(?:\s+\d{4})?\b|"
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?"
        r"(?:,?\s+\d{4})?\b|"
        r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b", re.I)),
    ("range", re.compile(r"\b\d[\d,.]*\s*(?:to|-|–)\s*\d[\d,.]*\b")),
    ("number", re.compile(r"\b\d[\d,.]*\b")),
)


class Protected:
    """A masked transcript and the map needed to put the values back."""

    __slots__ = ("masked", "spans", "kinds", "negations")

    def __init__(self, masked, spans, kinds, negations):
        self.masked = masked
        self.spans = spans          # placeholder -> original text
        self.kinds = kinds          # placeholder -> category name
        self.negations = negations  # count of negation words in the source

    def placeholders(self):
        return list(self.spans)


def _placeholder(i):
    return "<P%d>" % i


def count_negations(text):
    """Negation words present. Section 12 requires the count and identity to
    survive formatting, because dropping a "not" reverses a sentence."""
    words = re.findall(r"[A-Za-z']+", (text or "").lower())
    return sum(1 for w in words if w in _NEGATIONS)


def protect(text, lexicon=()):
    """Replace protected values with opaque placeholders. Section 10.

    lexicon is the personal vocabulary. Names are protected only when they are
    already known, because guessing at proper nouns would mask ordinary words
    and leave the formatter nothing to edit.
    """
    if not text:
        return Protected("", {}, {}, 0)

    taken = []          # (start, end, kind)
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e, _k in taken):
                continue        # already inside a longer, more structured span
            taken.append((m.start(), m.end(), kind))

    for term in sorted(lexicon, key=len, reverse=True):
        if not term or len(term) < 2:
            continue
        for m in re.finditer(r"\b%s\b" % re.escape(term), text, re.I):
            if any(m.start() < e and s < m.end() for s, e, _k in taken):
                continue
            taken.append((m.start(), m.end(), "name"))

    taken.sort()
    out, spans, kinds, last = [], {}, {}, 0
    for i, (start, end, kind) in enumerate(taken):
        key = _placeholder(i)
        out.append(text[last:start])
        out.append(key)
        spans[key] = text[start:end]
        kinds[key] = kind
        last = end
    out.append(text[last:])
    return Protected("".join(out), spans, kinds, count_negations(text))


def restore(masked, protected):
    """Put the original values back. Deterministic and total."""
    out = masked or ""
    for key, original in protected.spans.items():
        out = out.replace(key, original)
    return out


# --------------------------------------------------------------------------
# Section 12: the safety validator
# --------------------------------------------------------------------------

# Text that is an answer rather than an edit. The formatter is a copy editor;
# anything that reads as a reply, a refusal or a heading is a model that
# misunderstood its job, and its output must not reach the document.
_ANSWER_LIKE = re.compile(
    r"^\s*(?:sure|certainly|of course|here(?:'s| is)|i\s+(?:can|cannot|can't|"
    r"am unable|apologize|apologise)|as an ai|i'm sorry|note:|answer:|"
    r"output:|result:|corrected text:|#{1,6}\s)", re.I)

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")

# Words the formatter is allowed to remove: fillers and hedges it was asked to
# clean. Anything else disappearing is unexplained deletion.
_REMOVABLE = frozenset((
    "um", "uh", "erm", "uhm", "hmm", "mmm", "mm", "eh", "ah", "er", "uhh",
    "umm", "ahh", "like", "basically", "actually", "literally", "sort",
    "kind", "you", "know", "i", "mean", "so", "well", "right", "okay",
))

MAX_GROWTH = 1.35        # a copy edit does not make text a third longer
MAX_EDIT_RATIO = 0.45    # normalized edit distance ceiling


def validate(raw, deterministic, formatted, protected, category=UNKNOWN):
    """Every Section 12 check. Returns (accepted, reasons).

    reasons is always populated on rejection, because a formatter that is
    silently ignored is indistinguishable from one that is not running, and
    this project has already shipped one silent guard too many.
    """
    reasons = []
    if formatted is None:
        return False, ["no formatter output"]
    text = formatted.strip()
    if not text:
        return False, ["formatter returned nothing"]

    if _ANSWER_LIKE.search(text):
        reasons.append("output reads as an answer or heading, not an edit")

    # Placeholders: exactly once each, none invented.
    for key in protected.spans:
        n = text.count(key)
        if n != 1:
            reasons.append("placeholder %s occurs %d times, expected 1"
                           % (key, n))
    for found in re.findall(r"<P\d+>", text):
        if found not in protected.spans:
            reasons.append("invented placeholder %s" % found)

    restored = restore(text, protected)

    # Protected values must round-trip exactly.
    for key, original in protected.spans.items():
        if original not in restored:
            reasons.append("protected value %r did not survive" % original[:24])

    # Negation must not be added or lost. Dropping a "not" reverses meaning.
    after = count_negations(restored)
    if after != protected.negations:
        reasons.append("negation count changed from %d to %d"
                       % (protected.negations, after))

    base = deterministic or raw or ""
    base_words = [w.lower() for w in _WORD.findall(base)]
    new_words = [w.lower() for w in _WORD.findall(restored)]

    # No new content words. A formatter may punctuate; it may not add facts.
    added = [w for w in new_words if w not in base_words]
    real_additions = [w for w in added if w not in _REMOVABLE and len(w) > 2]
    if real_additions:
        reasons.append("added words not present in the transcript: %s"
                       % ", ".join(sorted(set(real_additions))[:4]))

    # Deletions must be explainable as filler or exact repetition.
    removed = []
    counts = {}
    for w in new_words:
        counts[w] = counts.get(w, 0) + 1
    seen = {}
    for w in base_words:
        seen[w] = seen.get(w, 0) + 1
        if seen[w] > counts.get(w, 0):
            removed.append(w)
    unexplained = [w for w in removed if w not in _REMOVABLE]
    # A repeated word removed once is a collapse, which is allowed.
    unexplained = [w for w in unexplained if base_words.count(w) < 2]
    if unexplained:
        reasons.append("deleted words that are not filler: %s"
                       % ", ".join(sorted(set(unexplained))[:4]))

    # Length envelope and edit distance.
    if base and len(restored) > len(base) * MAX_GROWTH:
        reasons.append("output is %.0f%% longer than the transcript"
                       % (100.0 * len(restored) / max(1, len(base)) - 100))
    if base:
        ratio = 1.0 - difflib.SequenceMatcher(None, base.lower(),
                                              restored.lower()).ratio()
        if ratio > MAX_EDIT_RATIO:
            reasons.append("edit distance %.2f exceeds %.2f"
                           % (ratio, MAX_EDIT_RATIO))

    if category in NO_FORMATTER:
        reasons.append("category %s never uses the formatter" % category)

    return (not reasons), reasons


# --------------------------------------------------------------------------
# The fallback hierarchy, Section 12
# --------------------------------------------------------------------------

class Outcome:
    """What was inserted, which route produced it, and why."""

    __slots__ = ("text", "route", "reasons", "category")

    def __init__(self, text, route, reasons=(), category=UNKNOWN):
        self.text = text
        self.route = route
        self.reasons = list(reasons)
        self.category = category

    def __repr__(self):
        return "<Outcome %s %r>" % (self.route, (self.text or "")[:40])


def finalize(raw, deterministic, formatter=None, lexicon=(),
             category=UNKNOWN):
    """Produce the text to insert, never worse than the deterministic one.

    formatter is a callable taking the masked text and returning edited masked
    text, or None. Any exception, timeout or refusal from it is a fallback,
    never a failure of the dictation.
    """
    if not (raw or "").strip() and not (deterministic or "").strip():
        return Outcome("", "empty", ["no transcript"], category)

    fallback = deterministic if (deterministic or "").strip() else raw

    if formatter is None or category in NO_FORMATTER:
        why = ("no formatter" if formatter is None
               else "category %s is deterministic only" % category)
        return Outcome(fallback, "deterministic", [why], category)

    protected = protect(fallback, lexicon)
    try:
        edited = formatter(protected.masked, category)
    except Exception as e:
        return Outcome(fallback, "deterministic",
                       ["formatter raised %s" % type(e).__name__], category)

    accepted, reasons = validate(raw, fallback, edited, protected, category)
    if not accepted:
        return Outcome(fallback, "deterministic", reasons, category)
    return Outcome(restore(edited.strip(), protected), "formatted", [],
                   category)
