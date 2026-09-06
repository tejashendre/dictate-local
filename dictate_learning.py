"""Phase 4: the tool stops making the same mistake twice.

ARCHITECTURE.md Section 15. The product goal is not a lower first-pass word
error rate. It is that a personal mistake, once corrected, does not come back.

Two rules govern everything here, and both are refusals:

  Section 5.4: the application learns only when the user chooses "Correct last
  dictation". It never assumes subsequent keyboard editing was a correction,
  and it never reads document history to infer one. A tool that watched your
  edits would learn from you deleting a sentence you simply changed your mind
  about, and there would be no way to tell the two apart.

  Section 15.2: a global mapping is a bounded word or phrase substitution.
  A long sentence rewrite is stored as an example for the formatter to
  retrieve, never applied as a global search and replace, because a rule that
  rewrites whole sentences will eventually rewrite the wrong one.

Every rule is reversible. It carries where it came from, when, how often it
fired, and whether it is enabled, so a correction that causes a regression can
be switched off from the settings window rather than by editing a file.
"""
import datetime
import io
import json
import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "corrections.json")

VERSION = 1

# A global rule replaces at most this many words. Past it, the correction is
# kept as an example instead. Section 15.2.
MAX_RULE_WORDS = 4
MAX_EXAMPLES_RETRIEVED = 3          # Section 15.3

SOURCE_USER = "explicit_user_correction"

_WORD = re.compile(r"[\w'@./+-]+")


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _tokens(text):
    return _WORD.findall((text or "").lower())


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def empty_store():
    return {"version": VERSION, "rules": [], "examples": []}


def load(path=None):
    """Read the store. A malformed file is quarantined, never fatal.

    Section 20: a malformed correction file must leave dictation working. The
    alternative is that one bad edit to a JSON file stops the user speaking.
    """
    path = path or RULES_PATH
    if not os.path.exists(path):
        return empty_store(), None
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "rules" not in data:
            raise ValueError("not a correction store")
        data.setdefault("version", VERSION)
        data.setdefault("examples", [])
        if not isinstance(data["rules"], list):
            raise ValueError("rules is not a list")
        return data, None
    except Exception as e:
        quarantine = path + ".quarantined"
        try:
            os.replace(path, quarantine)
        except Exception:
            quarantine = None
        return empty_store(), "corrections file was %s: %s" % (
            "quarantined" if quarantine else "unreadable", e)


def save(store, path=None):
    """Write atomically, so a crash cannot leave half a file."""
    path = path or RULES_PATH
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(store, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# Section 15.1: proposing a rule from one correction
# --------------------------------------------------------------------------

def propose(heard, wanted):
    """Infer a reusable mapping from one correction, or explain why not.

    Returns (proposal_or_None, reason). The proposal is never stored here.
    Section 15.1 requires explicit confirmation, so this only suggests.
    """
    heard = (heard or "").strip()
    wanted = (wanted or "").strip()
    if not heard or not wanted:
        return None, "nothing to compare"
    if heard == wanted:
        return None, "the text was already correct"

    a, b = heard.split(), wanted.split()

    # Trim the identical head and tail. What is left is the actual change,
    # and a rule wider than the change would fire in places it should not.
    start = 0
    while start < len(a) and start < len(b) and a[start] == b[start]:
        start += 1
    end = 0
    while (end < len(a) - start and end < len(b) - start
           and a[len(a) - 1 - end] == b[len(b) - 1 - end]):
        end += 1

    from_words = a[start:len(a) - end]
    to_words = b[start:len(b) - end]

    if not from_words and not to_words:
        return None, "the difference is only whitespace"
    if not from_words:
        return None, ("this only adds words, which cannot be a substitution "
                      "rule")
    if len(from_words) > MAX_RULE_WORDS or len(to_words) > MAX_RULE_WORDS:
        return None, ("the change spans %d words, so it is kept as an example "
                      "rather than a global rule" % max(len(from_words),
                                                        len(to_words)))

    return {"heard": " ".join(from_words),
            "wanted": " ".join(to_words)}, "bounded substitution"


def add_rule(store, heard, wanted, scope="global"):
    """Store an explicitly confirmed rule. Section 15.2 shape."""
    rule = {
        "id": uuid.uuid4().hex[:12],
        "heard": heard,
        "wanted": wanted,
        "scope": scope,
        "source": SOURCE_USER,
        "created_at": _now(),
        "use_count": 0,
        "enabled": True,
    }
    store["rules"].append(rule)
    return rule


def add_example(store, heard, wanted, app_category="unknown"):
    """Keep a long correction as a retrievable example, not a global rule."""
    example = {
        "id": uuid.uuid4().hex[:12],
        "heard": heard,
        "wanted": wanted,
        "app_category": app_category,
        "source": SOURCE_USER,
        "created_at": _now(),
    }
    store["examples"].append(example)
    return example


def set_enabled(store, rule_id, enabled):
    for rule in store["rules"]:
        if rule["id"] == rule_id:
            rule["enabled"] = bool(enabled)
            return rule
    return None


def delete_rule(store, rule_id):
    before = len(store["rules"])
    store["rules"] = [r for r in store["rules"] if r["id"] != rule_id]
    return before - len(store["rules"])


def edit_rule(store, rule_id, heard=None, wanted=None):
    for rule in store["rules"]:
        if rule["id"] == rule_id:
            if heard is not None:
                rule["heard"] = heard
            if wanted is not None:
                rule["wanted"] = wanted
            return rule
    return None


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------

def apply_rules(text, store):
    """Apply every enabled rule. Returns (text, fired).

    Longest first, so a two-word rule wins over a one-word rule that is part
    of it. Matching is whole-word and case-insensitive; the replacement keeps
    the case the user asked for, because that is the point of the rule.
    """
    if not text:
        return text, []
    rules = [r for r in store.get("rules", []) if r.get("enabled", True)]
    rules.sort(key=lambda r: -len(r.get("heard", "")))
    fired = []
    out = text
    for rule in rules:
        heard = rule.get("heard", "")
        if not heard:
            continue
        pattern = re.compile(r"(?<!\w)%s(?!\w)" % re.escape(heard), re.I)
        new = pattern.sub(lambda _m: rule["wanted"], out)
        if new != out:
            out = new
            rule["use_count"] = rule.get("use_count", 0) + 1
            fired.append((heard, rule["wanted"]))
    return out, fired


# --------------------------------------------------------------------------
# Section 15.3: retrieval, without a vector database
# --------------------------------------------------------------------------

def retrieve(text, store, limit=MAX_EXAMPLES_RETRIEVED):
    """At most three relevant approved examples, by token overlap.

    Deterministic on purpose. Section 15.3 rules out an embedding service, and
    a scoring function that cannot be reproduced by hand cannot be debugged
    when it retrieves something absurd.
    """
    want = set(_tokens(text))
    if not want:
        return []
    scored = []
    for example in store.get("examples", []):
        have = set(_tokens(example.get("heard", "")))
        if not have:
            continue
        overlap = len(want & have)
        if not overlap:
            continue
        # Jaccard, so a long example cannot win merely by being long.
        score = overlap / float(len(want | have))
        scored.append((score, example))
    scored.sort(key=lambda pair: -pair[0])
    # If nothing is clearly relevant, send none rather than noise.
    return [e for score, e in scored[:limit] if score >= 0.15]
