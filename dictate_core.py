"""dictate_core - the parts of local dictation that do not need a microphone.

Kept separate from dictate.py on purpose: everything here can be tested
headlessly, without hooking the keyboard or opening an audio device.

Contents:
    enable_cuda_dlls()   make the CUDA runtime findable on Windows
    plan_device()        choose cuda or cpu, with the reason why
    Transcriber          a model that degrades to CPU instead of dying
    load_vocabulary()    read the bias terms from vocabulary.txt
    load_corrections()   read the "heard -> wanted" fix-ups
    build_prompt()       turn terms into a Whisper initial_prompt
    apply_corrections()  deterministic fix-up for what biasing misses
    fuzzy_snap()         snap near-misses back to a vocabulary term
"""

import os
import re
import site
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VOCAB_PATH = os.path.join(HERE, "vocabulary.txt")

# initial_prompt shares Whisper's 448-token context and is capped at half of
# it. Staying well under that leaves room for the audio's own context and
# avoids the model echoing the prompt back as output.
PROMPT_TOKEN_BUDGET = 180
TERMS_WARN_AT = 100


# --------------------------------------------------------------------------
# CUDA runtime
# --------------------------------------------------------------------------

def enable_cuda_dlls():
    """Put the pip-installed CUDA DLLs on the search path.

    ctranslate2 ships cudnn64_9.dll but not cublas64_12.dll. When cuBLAS comes
    from the nvidia-cublas-cu12 wheel it lands in site-packages/nvidia/... which
    Windows does not search. ctranslate2 loads it with a plain LoadLibrary, so
    os.add_dll_directory is not enough - it has to be on PATH, and it has to be
    set before ctranslate2 is imported.

    Returns the list of directories added.
    """
    if os.environ.get("DICTATE_SKIP_DLL_FIX") == "1":
        return []          # used by tests/test_fallback.py to recreate the
                           # missing-cuBLAS state on purpose
    added = []
    roots = list(site.getsitepackages())
    user_site = site.getusersitepackages()
    if isinstance(user_site, str):
        roots.append(user_site)
    for base in roots:
        for sub in ("nvidia/cublas/bin", "nvidia/cuda_nvrtc/bin", "nvidia/cudnn/bin"):
            path = os.path.join(base, *sub.split("/"))
            if os.path.isdir(path) and path not in os.environ.get("PATH", ""):
                os.environ["PATH"] = path + os.pathsep + os.environ["PATH"]
                added.append(path)
    return added


# --------------------------------------------------------------------------
# One instance, and only one
# --------------------------------------------------------------------------
#
# Found the hard way. Two copies were running at once, and the symptoms looked
# like the transcription itself had broken:
#
#   - two pills on screen
#   - every F9 press toggled BOTH, so they fell out of step and one kept
#     recording after the other had stopped
#   - both typed into the same window, interleaving their output
#   - it appeared to "listen to the room" because one instance was still
#     recording when the user believed everything had stopped
#
# None of that is a speech problem, and no amount of tuning the model would
# have fixed it. A global hotkey and a global typing path mean a second copy
# is never harmless, so the app now refuses to start twice.
#
# A named kernel mutex is the right primitive: it is released automatically
# when the process dies, so a crash cannot leave a stale lock behind, which is
# exactly the failure a lock *file* would have.

_MUTEX_NAME = "Local.Dictation.SingleInstance"
_mutex_handle = None


def claim_single_instance():
    """Return True if we are the only instance. Keeps the handle alive."""
    global _mutex_handle
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        already = kernel32.GetLastError() == 183      # ERROR_ALREADY_EXISTS
        if already:
            if handle:
                kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True        # never block startup over the lock itself


# --------------------------------------------------------------------------
# Typing anywhere
# --------------------------------------------------------------------------

def foreground_window():
    """Handle and title of the window that currently has focus."""
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        return hwnd, buf.value
    except Exception:
        return None, ""


def focus_window(hwnd):
    """Put focus back on a window we noted earlier. Returns True if it took.

    Why this exists: text is typed wherever focus happens to be at the moment
    transcription finishes, not where dictation started. Start talking in one
    window, glance at another, and the words land in the wrong place - which
    is exactly what a long dictation invites, since there is time to look away.

    Windows restricts SetForegroundWindow to processes that already own the
    foreground, so this is best-effort: it is checked rather than assumed, and
    the caller decides what to do when it fails.
    """
    if not hwnd:
        return False
    try:
        import ctypes
        u32 = ctypes.windll.user32
        if u32.GetForegroundWindow() == hwnd:
            return True
        if not u32.IsWindow(hwnd):
            return False
        u32.SetForegroundWindow(hwnd)
        return u32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def is_elevated():
    """True if this process is running as administrator.

    Windows enforces User Interface Privilege Isolation: a process at normal
    integrity CANNOT send synthetic input to a window owned by an elevated
    process. So dictating into Task Manager, an admin terminal, or an
    installer silently does nothing - no error, no text, which is the worst
    possible failure because it looks like the tool is broken.

    Running elevated is the only fix, and it is worth saying so at startup
    rather than letting it be discovered mid-sentence.
    """
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def on_battery():
    """(is_on_battery, percent) or (None, None) if it cannot be determined.

    Worth reporting. The dGPU still runs on battery, but Windows clocks it
    down hard - measured idling at 210 MHz against a 2100 MHz maximum - so
    transcription being slower on battery is expected rather than a fault.
    Saying so beats leaving it to look like a bug.
    """
    try:
        import ctypes

        class _S(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte),
                        ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte),
                        ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong),
                        ("BatteryFullLifeTime", ctypes.c_ulong)]

        st = _S()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None, None
        if st.ACLineStatus == 255:
            return None, None
        pct = st.BatteryLifePercent
        return st.ACLineStatus == 0, (None if pct == 255 else pct)
    except Exception:
        return None, None


def trim_log(path, max_bytes=2_000_000, keep_bytes=500_000):
    """Keep a log from growing without bound.

    A tool meant to run for months cannot append forever. Keeps the tail,
    since the recent entries are the ones worth having.
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= max_bytes:
            return False
        with open(path, "rb") as f:
            f.seek(-keep_bytes, os.SEEK_END)
            tail = f.read()
        tail = tail.split(b"\n", 1)[-1]        # drop the half line at the cut
        with open(path, "wb") as f:
            f.write(b"--- trimmed, older entries removed ---\n" + tail)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Silence, and what Whisper invents to fill it
# --------------------------------------------------------------------------
#
# Found in real use, not in testing. transcript.log showed these two entries
# from half-second recordings with no real speech in them:
#
#     (0.5s spoken) So...
#     (0.5s spoken) Thank you for watching.
#
# Whisper was trained on subtitles, so given near-silence it produces the
# things that appear at the end of videos. Both would have been typed straight
# into whatever window was focused.
#
# The real guard is voice activity: check that the audio actually CONTAINS
# speech before transcribing it, rather than trusting that the key was held
# long enough. The phrase list below is only a backstop for the short clips
# where VAD is least reliable.

MIN_SPEECH_SECONDS = 0.35

_HALLUCINATIONS = {
    "you", "thank you", "thanks for watching", "thank you for watching",
    "thank you for watching!", "thanks for watching!", "bye", "bye.",
    "so", "so.", "...", ".", "okay", "ok", "hmm", "oh",
    "please subscribe", "subscribe", "the end", "music", "applause",
}


class Analysis:
    """One voice-activity pass, reused by everything that needs it."""

    __slots__ = ("regions", "speech_seconds", "rms")

    def __init__(self, regions, speech_seconds, rms):
        self.regions = regions
        self.speech_seconds = speech_seconds
        self.rms = rms

    def has_speech(self, min_seconds=None):
        return self.speech_seconds >= (min_seconds
                                       if min_seconds is not None
                                       else MIN_SPEECH_SECONDS)


def analyse(audio, sample_rate=16000, threshold=None):
    """Run Silero VAD ONCE and derive everything from that single pass.

    Measured: has_speech() and speech_rms() each ran their own pass over the
    same audio, 170 ms of duplicated work on a 17 s clip - about 15% of the
    whole utterance latency, spent computing the same answer twice.
    """
    import numpy as _np
    if audio is None or len(audio) < int(0.1 * sample_rate):
        return Analysis([], 0.0, 0.0)
    try:
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        regions = get_speech_timestamps(
            audio, VadOptions(
                threshold=threshold if threshold is not None else 0.6,
                min_silence_duration_ms=200, speech_pad_ms=100))
    except Exception:
        rms = float(_np.sqrt(_np.mean(_np.square(audio))))
        return Analysis([], len(audio) / sample_rate, rms)

    seconds = sum(r["end"] - r["start"] for r in regions) / sample_rate
    if not regions:
        return Analysis([], 0.0, 0.0)
    parts = [audio[r["start"]:r["end"]] for r in regions]
    joined = _np.concatenate(parts) if parts else _np.zeros(1)
    return Analysis(regions, seconds,
                    float(_np.sqrt(_np.mean(_np.square(joined)))))


def has_speech(audio, min_seconds=MIN_SPEECH_SECONDS, sample_rate=16000,
               threshold=None):
    """Does this audio actually contain speech? Returns (bool, seconds).

    Uses the same Silero VAD the streaming path uses, so both paths agree on
    what counts as speech.
    """
    if audio is None or len(audio) < int(0.1 * sample_rate):
        return False, 0.0
    try:
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        regions = get_speech_timestamps(
            audio, VadOptions(
                threshold=threshold if threshold is not None else 0.6,
                min_silence_duration_ms=200, speech_pad_ms=100))
        seconds = sum(r["end"] - r["start"] for r in regions) / sample_rate
        return seconds >= min_seconds, seconds
    except Exception:
        return True, len(audio) / sample_rate      # never block on a VAD fault


# --------------------------------------------------------------------------
# Telling your voice from the room
# --------------------------------------------------------------------------
#
# Silero VAD answers "is this speech", not "is this the person at the mic",
# and measured here it passes a television at every level tested:
#
#     background at 100% of your level   -> transcribed
#     background at  35%                 -> transcribed
#     background at   8%                 -> transcribed
#
# Content cannot separate them - a news reader is speaking just as validly as
# you are. Loudness can: a voice across the room arrives far quieter at your
# microphone than your own mouth does.
#
# Overlapping background is already handled - when you are talking over it,
# Whisper locks onto the dominant speaker and the television does not appear.
# The leak is background during a PAUSE, when yours is the only voice missing.
#
# So the gate is relative, not absolute: learn how loud YOU are, and reject
# audio far below it. Relative because an absolute threshold would depend on
# the microphone, the gain and how close you sit - none of which are knowable
# in advance, and all of which this learns by watching.

GATE_RATIO = 0.30          # below this fraction of your voice, treat as room
GATE_MIN_SAMPLES = 4       # do not gate until your level is actually known

# If the gate rejects this many phrases in a row, the learned level is wrong
# and it switches itself off.
#
# This is not defensive padding, it is a bug that already happened. A test run
# fed the loud synthetic corpus (RMS 0.1164) through the real transcribe path,
# which learned and SAVED that as the speaking level. The real microphone
# measures 0.0087-0.0123 - roughly ten times quieter - so the floor sat above
# every real phrase and dictation stopped working entirely, with the reason
# visible only in a log file.
#
# A gate that can silently lock the user out is worse than no gate. It must be
# able to notice it is wrong.
#
# The counter resets on every accepted phrase, so this only trips when NOTHING
# is getting through - the signature of a wrong level. A television talking
# through several of your pauses does not trip it, because your own phrases in
# between keep resetting it. That distinction matters: an earlier version gave
# up after three correct rejections and switched off exactly when it was
# working.
GATE_GIVE_UP_AFTER = 5


def speech_rms(audio, sample_rate=16000, threshold=None):
    """Loudness of the SPEECH in this audio, ignoring the silence around it.

    Plain RMS over the whole buffer would score a short loud phrase the same
    as a long quiet one, because the silence drags the average down.
    """
    if audio is None or len(audio) == 0:
        return 0.0
    try:
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        regions = get_speech_timestamps(
            audio, VadOptions(
                threshold=threshold if threshold is not None else 0.6,
                min_silence_duration_ms=200, speech_pad_ms=100))
        if not regions:
            return 0.0
        import numpy as _np
        parts = [audio[r["start"]:r["end"]] for r in regions]
        joined = _np.concatenate(parts) if parts else _np.zeros(1)
        return float(_np.sqrt(_np.mean(_np.square(joined))))
    except Exception:
        import numpy as _np
        return float(_np.sqrt(_np.mean(_np.square(audio))))


class VoiceLevel:
    """Learns how loud you are, so the room can be told apart from you.

    Keeps a median of recent accepted phrases rather than a mean: one shout
    or one whisper should not move the gate, and a median ignores both.
    """

    def __init__(self, samples=None, ratio=GATE_RATIO):
        self.samples = list(samples or [])
        self.ratio = ratio
        self.consecutive_rejects = 0
        self.disabled = False
        self.last_reason = ""

    @property
    def level(self):
        if not self.samples:
            return None
        ordered = sorted(self.samples)
        return ordered[len(ordered) // 2]

    def learn(self, rms):
        """Record a phrase that was accepted as yours."""
        if rms and rms > 0:
            self.samples.append(float(rms))
            del self.samples[:-25]          # recent history only
            self.consecutive_rejects = 0

    def is_background(self, rms):
        """True if this is too quiet to be you. Returns (verdict, threshold).

        Refuses to judge until it has heard you enough times, and gives up
        entirely if it starts rejecting everything - see GATE_GIVE_UP_AFTER.
        """
        if self.disabled or len(self.samples) < GATE_MIN_SAMPLES or not rms:
            return False, None
        floor = self.level * self.ratio
        if rms >= floor:
            self.consecutive_rejects = 0
            return False, floor

        self.consecutive_rejects += 1
        if self.consecutive_rejects >= GATE_GIVE_UP_AFTER:
            # Everything is being rejected, so the learned level is wrong, not
            # the speaker. Forget it and let the words through.
            self.disabled = True
            self.samples = []
            self.consecutive_rejects = 0
            self.last_reason = (
                "rejected %d phrases in a row, so the learned voice level was "
                "wrong. Noise filtering is off for this session and will "
                "relearn." % GATE_GIVE_UP_AFTER)
            return False, floor
        return True, floor


def collapse_repetition(text):
    """Collapse Whisper's repetition loop.

    Given an ambiguous stretch the decoder can lock into a cycle and emit the
    same sentence over and over: "Thank you. Thank you. Thank you." It is not
    a transcription of anything, it is the model stuck.

    Returns (text, how_many_removed).
    """
    if not text:
        return text, 0
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out, removed = [], 0
    for part in parts:
        key = part.strip().lower()
        if out and key and key == out[-1].strip().lower():
            removed += 1
            continue
        out.append(part)
    # also the degenerate case with no punctuation at all: "you you you you"
    words = " ".join(out).split()
    if len(words) >= 4 and len(set(w.lower() for w in words)) == 1:
        removed += len(words) - 1
        words = words[:1]
        return " ".join(words), removed
    return " ".join(out), removed


def looks_hallucinated(text, speech_seconds):
    """A known filler phrase from a clip too short to have contained it.

    Deliberately narrow: only fires under a second of speech, so it can never
    eat a real short answer like "yes" in the middle of normal dictation.
    """
    if not text or speech_seconds >= 1.0:
        return False
    return text.strip().strip(".!?").lower() in _HALLUCINATIONS


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

def load_vocabulary(path=VOCAB_PATH):
    """Read vocabulary.txt into a list of terms, preserving file order.

    Missing file is not an error - it just means no biasing.
    """
    if not os.path.exists(path):
        return []
    terms, seen = [], set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            term = line.strip()
            if not term or term.startswith("#"):
                continue
            if "->" in term:            # a correction rule, not a bias term
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


def _estimate_tokens(text):
    """Rough token count. Whisper's BPE averages a little under 4 chars per
    token for ordinary English; proper nouns split harder, so this deliberately
    over-estimates rather than under."""
    return max(1, int(len(text) / 3.2) + text.count(",") + 1)


def build_prompt(terms, budget=PROMPT_TOKEN_BUDGET):
    """Build an initial_prompt from terms, trimmed to fit the token budget.

    A comma-separated list of proper nouns is the shape that biases decoding
    without the model trying to continue a sentence. Terms are kept in file
    order, so the most important ones belong at the top of vocabulary.txt.

    Returns (prompt_or_None, terms_used, terms_dropped).
    """
    if not terms:
        return None, [], []
    used = []
    for term in terms:
        candidate = ", ".join(used + [term])
        if _estimate_tokens(candidate) > budget:
            break
        used.append(term)
    dropped = terms[len(used):]
    if not used:
        return None, [], terms
    return ", ".join(used) + ".", used, dropped


def vocabulary_report(terms, used, dropped):
    """Human-readable one-liner plus any warnings, for startup output."""
    lines = ["  vocab  : %d terms" % len(terms)]
    if len(terms) > TERMS_WARN_AT:
        lines.append("  warning: %d terms is past the %d-term comfort zone; "
                     "a long prompt can hurt ordinary transcription"
                     % (len(terms), TERMS_WARN_AT))
    if dropped:
        lines.append("  warning: %d terms dropped, prompt budget full: %s"
                     % (len(dropped), ", ".join(dropped[:5])
                        + (" ..." if len(dropped) > 5 else "")))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------
#
# initial_prompt is a soft bias. It moves recall a long way but it does not
# guarantee a spelling, and a few terms lose every time - long German compounds
# and acronyms the model wants to title-case. Rather than pretend otherwise,
# vocabulary.txt can carry explicit rules:
#
#     Arbeitsugnis -> Arbeitszeugnis
#
# These are applied after transcription, on whole words only, so they cannot
# corrupt the inside of an unrelated word.

def load_corrections(path=VOCAB_PATH):
    """Read 'heard -> wanted' rules. Returns a list of (pattern, replacement)."""
    if not os.path.exists(path):
        return []
    rules = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#") or "->" not in line:
                continue
            heard, _, wanted = line.partition("->")
            heard, wanted = heard.strip(), wanted.strip()
            if not heard or not wanted:
                print("  vocabulary.txt line %d ignored, malformed rule: %s"
                      % (lineno, line))
                continue
            rules.append((heard, wanted))
    return rules


def _compile(heard):
    r"""Whole-word, case-insensitive matcher tolerant of spacing inside a phrase.

    \b is wrong at a non-word edge (n8n, C++), so guard with lookarounds that
    only assert a boundary when the adjacent character in the pattern is one.
    """
    parts = [re.escape(w) for w in heard.split()]
    body = r"\s+".join(parts)
    left = r"(?<![\w])" if re.match(r"\w", heard[0]) else ""
    right = r"(?![\w])" if re.match(r"\w", heard[-1]) else ""
    return re.compile(left + body + right, re.IGNORECASE)


def apply_corrections(text, rules):
    """Apply correction rules to transcribed text.

    Returns (text, [(heard, wanted), ...]) so callers can log what fired.
    """
    if not text or not rules:
        return text, []
    fired = []
    for heard, wanted in rules:
        new, n = _compile(heard).subn(lambda _m, w=wanted: w, text)
        if n:
            fired.append((heard, wanted))
            text = new
    return text, fired


# --------------------------------------------------------------------------
# Near-miss snapping
# --------------------------------------------------------------------------
#
# Exact "heard -> wanted" rules are whack-a-mole. Streaming proved it: the same
# word came out "Arbeitsugnis" in one chunking and "Arbeitsuegnis" in another,
# and no list of hand-written variants converges on a German compound noun.
#
# So near-misses are snapped back to the vocabulary term instead. The whole
# risk here is a false positive - silently rewriting an ordinary English word
# into a proper noun would be far worse than leaving one term misspelt - so
# the thresholds are deliberately mean, and all three must pass:
#
#   1. The token is at least MIN_LEN characters. Short words collide too easily.
#   2. It already shares the first MIN_PREFIX characters with the term. Whisper
#      mangles the middle and end of an unfamiliar word; it rarely invents a
#      new beginning. This is a cheap pre-filter, not the real guard.
#   3. The edit distance is within MAX_RATIO of the term's length. This is the
#      real guard.
#
# Worked example of why the ratio is 0.2 and not 0.25: "linked" is two edits
# from "LinkedIn", which is 0.25. At 0.25 the sentence "I linked the file"
# becomes "I LinkedIn the file". At 0.2 it does not. tests/test_fuzzy.py holds
# that line.
#
# The prefix is 3 rather than 4 because it was measured, not guessed: against
# the collision corpus in tests/test_fuzzy.py, 4 and 3 both give zero false
# positives, but 3 also recovers "Zalendo" -> "Zalando", which 4 rejects.
# Dropping to 2 recovers nothing further, so 3 is where it sits.

FUZZY_MIN_LEN = 6
FUZZY_MIN_PREFIX = 3
FUZZY_MAX_RATIO = 0.2


def _edit_distance(a, b, cap):
    """Levenshtein distance, abandoned early once it exceeds cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
            best = min(best, cur[j])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def fuzzy_snap(text, terms, min_len=FUZZY_MIN_LEN, min_prefix=FUZZY_MIN_PREFIX,
               max_ratio=FUZZY_MAX_RATIO):
    """Snap near-misses of single-word vocabulary terms back to the term.

    Returns (text, [(heard, wanted), ...]).
    """
    if not text or not terms:
        return text, []

    candidates = [t for t in terms if " " not in t and len(t) >= min_len]
    if not candidates:
        return text, []
    exact = {t.lower() for t in candidates}

    fired = []
    out = []
    for token in text.split():
        core_word = token.strip(_TRAILING).strip(_LEADING)
        low = core_word.lower()
        if len(core_word) < min_len or low in exact:
            out.append(token)
            continue

        best, best_dist = None, None
        for term in candidates:
            tl = term.lower()
            if low[:min_prefix] != tl[:min_prefix]:
                continue
            cap = int(len(term) * max_ratio)
            if cap < 1:
                continue
            d = _edit_distance(low, tl, cap)
            if d <= cap and (best_dist is None or d < best_dist):
                best, best_dist = term, d

        if best is None:
            out.append(token)
            continue
        out.append(token.replace(core_word, best, 1))
        fired.append((core_word, best))

    return " ".join(out), fired


# --------------------------------------------------------------------------
# Spoken punctuation and commands
# --------------------------------------------------------------------------
#
# Measured against small.en before any of this was written:
#
#   "Add milk comma eggs comma and bread"  ->  "Add milk, eggs, and bread"
#
# Whisper already converts a spoken "comma" on its own, and already places
# terminal punctuation. So "comma" is deliberately NOT a command here: adding
# it would buy nothing and would start mangling "the comma in that sentence".
# What Whisper does not convert, and what is therefore worth handling:
#
#   "full stop"      -> .
#   "new line"       -> one line break
#   "new paragraph"  -> two line breaks
#
# The hard part is that the command use and the ordinary use are the same
# words. "I finished the report full stop send it" and "the car came to a
# full stop at the light" are identical to a text matcher. The discriminator
# that actually works is the determiner in front: nobody dictating punctuation
# says "a full stop", and everybody using it as a noun does.

PUNCT_COMMANDS = {
    ("full", "stop"): ".",
    ("period",): ".",
    ("question", "mark"): "?",
    ("exclamation", "mark"): "!",
    ("new", "line"): "\n",
    ("new", "paragraph"): "\n\n",
}

# A determiner in front means the phrase is a noun, not an instruction.
DETERMINERS = {
    "a", "an", "the", "this", "that", "these", "those", "any", "each",
    "every", "one", "no", "some", "another", "my", "your", "his", "her",
    "its", "our", "their", "which", "what",
}

# "a new line of work" - a following "of" also marks it as a noun phrase.
NOUN_FOLLOWERS = {"of", "in"}

CONTROL_COMMANDS = {("scratch", "that"), ("cap", "that")}

_TRAILING = ".,!?;:\u2026\"')"
_LEADING = "\"'("


def _bare(token):
    """Lowercased token with surrounding punctuation removed, for matching."""
    return token.strip(_TRAILING).strip(_LEADING).lower()


def _render(pieces):
    """Join emitted pieces into text with sane spacing and capitalisation."""
    out = ""
    capitalise_next = False
    for piece in pieces:
        if piece in (".", ",", "?", "!"):
            out = out.rstrip() + piece
            capitalise_next = piece != ","
            continue
        if piece in ("\n", "\n\n"):
            out = out.rstrip() + piece
            capitalise_next = True
            continue
        if out and not out.endswith(("\n", " ")):
            out += " "
        if capitalise_next and piece:
            piece = piece[0].upper() + piece[1:]
            capitalise_next = False
        out += piece
    return out


def plan(text, last_typed=""):
    """Turn one raw transcription into keyboard actions.

    Returns (actions, notes) where actions is a list of
    ("backspace", n) or ("type", s), and notes lists the commands that fired.

    Keeping this a pure function is the point: the whole command grammar is
    testable without a microphone or a keyboard hook.
    """
    if not text:
        return [], []

    tokens = text.split()
    pieces = []
    notes = []
    actions = []
    i = 0
    n = len(tokens)

    while i < n:
        bare = _bare(tokens[i])

        # Escape hatch. "literally comma" types the word comma.
        if bare == "literally" and i + 1 < n:
            pieces.append(tokens[i + 1])
            notes.append("literally %s" % _bare(tokens[i + 1]))
            i += 2
            continue

        prev_bare = _bare(tokens[i - 1]) if i > 0 else ""
        matched = False

        for size in (2, 1):
            if i + size > n:
                continue
            key = tuple(_bare(t) for t in tokens[i:i + size])

            if key in CONTROL_COMMANDS:
                if key == ("scratch", "that"):
                    if pieces:
                        pieces = []            # discard this utterance so far
                    elif last_typed:
                        actions.append(("backspace", len(last_typed)))
                    notes.append("scratch that")
                elif key == ("cap", "that"):
                    if pieces:
                        for j in range(len(pieces) - 1, -1, -1):
                            w = pieces[j]
                            if w not in (".", ",", "?", "!", "\n", "\n\n"):
                                pieces[j] = w[0].upper() + w[1:] if w else w
                                break
                    notes.append("cap that")
                i += size
                matched = True
                break

            if key in PUNCT_COMMANDS:
                nxt = _bare(tokens[i + size]) if i + size < n else ""
                if prev_bare in DETERMINERS or nxt in NOUN_FOLLOWERS:
                    break                      # a noun, not an instruction
                pieces.append(PUNCT_COMMANDS[key])
                notes.append(" ".join(key))
                i += size
                matched = True
                break

        if matched:
            continue

        pieces.append(tokens[i])
        i += 1

    typed = _render(pieces)
    if typed:
        actions.append(("type", typed))
    return actions, notes


# --------------------------------------------------------------------------
# GPU contention
# --------------------------------------------------------------------------
#
# The card is 4 GB and it is shared with Ollama. qwen3.5:4b alone is 3.4 GB,
# so whichever process loads first wins and the other has to cope.
#
# The trap, found the hard way on this machine: a CUDA failure does NOT
# surface when WhisperModel() is constructed. The constructor succeeds and the
# process only falls over later, inside transcribe(), when the encoder first
# touches cuBLAS. A try/except around the constructor therefore catches
# nothing. Two things follow, and both are implemented below:
#
#   1. Warm up at startup with a slice of silence, so a broken GPU path fails
#      in the first two seconds rather than on the first thing you dictate.
#   2. Wrap transcribe as well, and on failure retry the SAME audio on CPU.
#      Falling back must never cost the user the words they just spoke.

MODEL_VRAM_MB = {
    "tiny.en": 150,
    "base.en": 250,
    "small.en": 600,
    "distil-medium.en": 900,
    "medium.en": 1600,
}

# cuBLAS workspace and activations sit on top of the weights.
VRAM_HEADROOM_MB = 400


def free_vram_mb():
    """Free VRAM in MB, or None if it cannot be determined.

    Local subprocess call to nvidia-smi. No network.
    """
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return None


def plan_device(model_name=None, free_mb=None):
    """Decide where to run. Returns (device, compute_type, reason).

    DICTATE_DEVICE always wins, so the choice can be forced when debugging.
    """
    model_name = model_name or os.environ.get("DICTATE_MODEL", "small.en")
    pref = os.environ.get("DICTATE_DEVICE", "auto").lower()
    if pref == "cpu":
        return "cpu", "int8", "forced by DICTATE_DEVICE=cpu"
    if pref == "cuda":
        return "cuda", "int8_float16", "forced by DICTATE_DEVICE=cuda"

    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return "cpu", "int8", "no CUDA device"
    except Exception:
        return "cpu", "int8", "ctranslate2 has no CUDA support"

    if free_mb is None:
        free_mb = free_vram_mb()
    if free_mb is None:
        return "cuda", "int8_float16", "GPU, free VRAM unknown"

    need = MODEL_VRAM_MB.get(model_name, 600) + VRAM_HEADROOM_MB
    if free_mb < need:
        return ("cpu", "int8",
                "only %d MB VRAM free, %s needs about %d MB - something else "
                "holds the card" % (free_mb, model_name, need))
    return "cuda", "int8_float16", "GPU, %d MB free" % free_mb


class Transcriber:
    """A model that degrades to CPU instead of dying.

    Owns the fallback so that dictate.py's audio loop does not have to think
    about it, and so it can be tested without a microphone.
    """

    def __init__(self, model_name="small.en", on_event=None):
        self.model_name = model_name
        self.on_event = on_event or (lambda msg: None)
        self.device = None
        self.compute = None
        self.model = None
        self.degraded = False

    # -- loading ----------------------------------------------------------

    def load(self):
        from faster_whisper import WhisperModel
        device, compute, reason = plan_device(self.model_name)
        self.on_event("device : %s (%s)" % (device, reason))
        try:
            self.model = WhisperModel(self.model_name, device=device,
                                      compute_type=compute)
            self.device, self.compute = device, compute
        except Exception as e:
            if device == "cpu":
                raise
            self.on_event("GPU load failed (%s: %s), falling back to CPU"
                          % (type(e).__name__, str(e)[:80]))
            return self._to_cpu()

        if device == "cuda" and not self._warmup():
            return self._to_cpu()
        return self

    def _warmup(self):
        """Force the encoder to touch cuBLAS now, not mid-dictation."""
        import numpy as _np
        try:
            silence = _np.zeros(16000 // 2, dtype=_np.float32)
            list(self.model.transcribe(silence, language="en", beam_size=1)[0])
            return True
        except Exception as e:
            self.on_event("GPU warmup failed (%s: %s)"
                          % (type(e).__name__, str(e)[:90]))
            return False

    def _to_cpu(self):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(self.model_name, device="cpu",
                                  compute_type="int8")
        self.device, self.compute, self.degraded = "cpu", "int8", True
        self.on_event("running on CPU. Free the GPU and restart to get it back")
        return self

    # -- use --------------------------------------------------------------

    def transcribe(self, audio, prompt=None):
        """Transcribe, retrying on CPU if the GPU path fails.

        The audio is kept and replayed on the fallback path, so a mid-session
        GPU failure costs latency rather than the words that were just spoken.
        """
        try:
            segs, _ = self.model.transcribe(
                audio, language="en", beam_size=1, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=prompt)
            return " ".join(s.text.strip() for s in segs).strip()
        except Exception as e:
            if self.device == "cpu":
                raise
            self.on_event("GPU failed mid-transcribe (%s), retrying on CPU"
                          % type(e).__name__)
            self._to_cpu()
            segs, _ = self.model.transcribe(
                audio, language="en", beam_size=1, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=prompt)
            return " ".join(s.text.strip() for s in segs).strip()
