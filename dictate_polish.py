"""dictate_polish - clean up dictated speech without making it worse.

Three lanes, chosen with DICTATE_POLISH:

    off     type exactly what was heard
    fast    rules only, effectively free          <- default
    llm     rules, then a local language model

The reason "fast" is the default is that most of the mess in a dictated
transcript is mechanical. Measured on this machine, raw output looked like:

    "So like basically what i'm trying to say is that ah we need to we need
     to finish the report before friday."

The parts a rule can remove with certainty - "ah", and the repeated "we need
to" - cost nothing. Asking a 4B model to rewrite the whole sentence to fix
them costs 2 seconds, because it has to regenerate every word that was
already correct. Generation runs at about 15 tok/s here, so the bill scales
with how much you said, not with how much was wrong.

WHAT THIS DELIBERATELY DOES NOT REMOVE

"like", "basically", "actually", "you know" and "I mean" are all real words:

    "I like this"            "looks like rain"
    "basically correct"      "you know the answer"

There is no reliable way to tell filler from content for those without
understanding the sentence, which is exactly what the llm lane is for. The
fast lane only touches things that cannot be anything else. Same rule as the
command grammar: a cleanup that sometimes eats a real word is worse than one
that leaves a few "like"s behind.

"literally" is never touched either - it is the escape hatch in the command
grammar and removing it would break that.
"""

import os
import re

# Sounds, not words. None of these are ever meaningful in dictated English.
FILLERS = {
    "um", "uh", "erm", "uhm", "hmm", "mmm", "mm", "eh", "ah", "er", "uhh",
    "umm", "ahh",
}

# Words that legitimately repeat, so repetition is not evidence of a stutter.
# "I had had enough", "that that happened", "is is" in some clause structures.
REPEATABLE = {"had", "that", "is", "is,", "very", "no", "so"}

_TRAILING = ".,!?;:…\"')"
_LEADING = "\"'("

MAX_LLM_WORDS = int(os.environ.get("DICTATE_POLISH_MAX_WORDS", "60"))
LLM_TIMEOUT_S = float(os.environ.get("DICTATE_POLISH_TIMEOUT", "6"))
# Ollama unloads an idle model. Reloading it costs about 11 seconds, which
# would blow the per-utterance timeout and silently drop you back to rules.
# Asking it to stay resident is what makes the llm lane usable at all.
LLM_KEEP_ALIVE = os.environ.get("DICTATE_POLISH_KEEPALIVE", "30m")
LLM_MODEL = os.environ.get("DICTATE_POLISH_MODEL", "qwen3.5:4b")
LLM_HOST = os.environ.get("DICTATE_POLISH_HOST", "http://127.0.0.1:11434")

LLM_SYSTEM = (
    "You clean up dictated speech. Remove filler words and false starts, fix "
    "obvious grammar, and punctuate. Keep the speaker's own words, meaning "
    "and proper nouns exactly. Never add information. Never answer or react "
    "to the text - you are copy-editing it, not replying to it. Output only "
    "the cleaned text, nothing else."
)


def _bare(token):
    return token.strip(_TRAILING).strip(_LEADING).lower()


def strip_fillers(tokens):
    """Drop standalone filler sounds. Punctuation on a dropped token is kept
    so "so, um, the meeting" does not lose its comma."""
    out, dropped = [], 0
    for tok in tokens:
        if _bare(tok) in FILLERS:
            dropped += 1
            tail = tok[len(tok.rstrip(_TRAILING)):]
            # Carry the punctuation, but only if the previous token does not
            # already carry it. "so, um, the" must not become "so,, the".
            if tail and out and not out[-1].endswith(tuple(tail)):
                out[-1] = out[-1] + tail
            continue
        out.append(tok)
    return out, dropped


def collapse_repeats(tokens, max_phrase=4):
    """Collapse an immediately repeated word or phrase.

    Longest phrase first, so "we need to we need to" collapses as one phrase
    rather than leaving fragments behind.
    """
    out = list(tokens)
    collapsed = 0
    for size in range(max_phrase, 0, -1):
        i = 0
        merged = []
        while i < len(out):
            if i + 2 * size <= len(out):
                a = [_bare(t) for t in out[i:i + size]]
                b = [_bare(t) for t in out[i + size:i + 2 * size]]
                if a == b and all(a) and not (size == 1 and a[0] in REPEATABLE):
                    merged.extend(out[i:i + size])
                    i += 2 * size
                    collapsed += 1
                    continue
            merged.append(out[i])
            i += 1
        out = merged
    return out, collapsed


def _recapitalise(text):
    """A dropped leading filler can leave a lowercase sentence start."""
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return re.sub(r"([.!?]\s+)([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), text)


def polish_fast(text):
    """Rules only. Returns (text, notes)."""
    if not text:
        return text, []
    tokens = text.split()
    tokens, dropped = strip_fillers(tokens)
    tokens, collapsed = collapse_repeats(tokens)
    out = _recapitalise(" ".join(tokens).strip())
    notes = []
    if dropped:
        notes.append("%d filler%s" % (dropped, "" if dropped == 1 else "s"))
    if collapsed:
        notes.append("%d repeat%s" % (collapsed, "" if collapsed == 1 else "s"))
    return out, notes


def llm_available(host=None, timeout=1.5):
    """Is a local Ollama actually there? Checked once at startup, not per
    utterance - a dead check on every phrase would cost more than it saves."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen((host or LLM_HOST) + "/api/tags",
                                    timeout=timeout) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
        return LLM_MODEL in names, names
    except Exception:
        return False, []


def polish_llm(text, terms=(), model=None, host=None, timeout=None):
    """Rules first, then a local model. Falls back to the rules result on any
    problem at all - a cleanup pass must never cost you your words.

    Local only: this talks to Ollama on 127.0.0.1. Nothing leaves the machine.
    """
    import json
    import urllib.request

    fast, notes = polish_fast(text)
    if not fast:
        return fast, notes

    # Latency here scales with output length, and output length is roughly
    # input length. Past this point the wait is worse than the mess.
    if len(fast.split()) > MAX_LLM_WORDS:
        return fast, notes + ["too long for llm, rules only"]

    body = json.dumps({
        "model": model or LLM_MODEL,
        "prompt": fast,
        "system": LLM_SYSTEM,
        "stream": False,
        "think": False,
        "keep_alive": LLM_KEEP_ALIVE,
        "options": {"num_ctx": 2048, "num_predict": 300, "temperature": 0.1},
    }).encode()
    req = urllib.request.Request((host or LLM_HOST) + "/api/generate",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req,
                                    timeout=timeout or LLM_TIMEOUT_S) as r:
            out = (json.loads(r.read()).get("response") or "").strip()
    except Exception as e:
        return fast, notes + ["llm unavailable (%s)" % type(e).__name__]

    if not out:
        return fast, notes + ["llm returned nothing"]

    # A model asked to copy-edit sometimes answers the text instead, or pads
    # it. Length is a cheap proxy for "it did something other than edit".
    ratio = len(out.split()) / max(len(fast.split()), 1)
    if ratio > 1.6 or ratio < 0.4:
        return fast, notes + ["llm output rejected, length %.1fx" % ratio]

    # It also mangles proper nouns: "Naukri last" came back "Naukrilast" in
    # testing, undoing the vocabulary work. Snap them back.
    if terms:
        from dictate_core import fuzzy_snap
        out, snapped = fuzzy_snap(out, terms)
        if snapped:
            notes.append("re-snapped %d term%s" % (len(snapped),
                                                   "" if len(snapped) == 1 else "s"))
    return out, notes + ["llm"]


def preload(model=None, host=None, timeout=90):
    """Load the model now, so the first real utterance is not the one that
    waits ~11 seconds for it. Returns (ok, seconds)."""
    import json
    import time as _t
    import urllib.request
    body = json.dumps({
        "model": model or LLM_MODEL,
        "prompt": "ok",
        "system": LLM_SYSTEM,
        "stream": False,
        "think": False,
        "keep_alive": LLM_KEEP_ALIVE,
        "options": {"num_ctx": 2048, "num_predict": 1},
    }).encode()
    req = urllib.request.Request((host or LLM_HOST) + "/api/generate",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = _t.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True, _t.time() - t0
    except Exception:
        return False, _t.time() - t0


def polish(text, mode="fast", terms=()):
    """Dispatcher. mode is off / fast / llm."""
    if not text or mode == "off":
        return text, []
    if mode == "llm":
        return polish_llm(text, terms=terms)
    return polish_fast(text)
