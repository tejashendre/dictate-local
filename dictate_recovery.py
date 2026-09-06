"""Phase 4: the dictation survives even when the window does not.

ARCHITECTURE.md Section 14. The rule that shapes this file:

    If the original target cannot be restored, do not type into whichever
    window happens to be focused.

That failure is worse than losing the text. Typing a paragraph of dictation
into the wrong application can send it somewhere, and the user finds out
afterwards. So the recovery record is written BEFORE the insertion is
attempted, not after, and a lost target ends with the text on the clipboard
and a visible message rather than a guess.

Section 19: audio is never stored. Only raw and final text, bounded by count
and by bytes, and the user can clear it.
"""
import datetime
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RECOVERY_PATH = os.path.join(HERE, "recovery.jsonl")

MAX_ITEMS = 50
MAX_BYTES = 512 * 1024

PENDING = "pending"
INSERTED = "inserted"
TARGET_LOST = "target_lost"
FAILED = "failed"


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def record(session_id, raw, final, target_title="", path=None):
    """Write the record before insertion is attempted. Section 14 step 1."""
    path = path or RECOVERY_PATH
    item = {
        "session": session_id,
        "at": _now(),
        "raw": raw or "",
        "final": final or "",
        "target": target_title or "",
        "state": PENDING,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    trim(path)
    return item


def complete(session_id, state=INSERTED, path=None):
    """Mark the record done. Section 14 step 6."""
    path = path or RECOVERY_PATH
    items = load(path)
    changed = False
    for item in items:
        if item.get("session") == session_id and item.get("state") == PENDING:
            item["state"] = state
            changed = True
    if changed:
        _rewrite(items, path)
    return changed


def load(path=None):
    """Every record still on file. A corrupt line is skipped, never fatal."""
    path = path or RECOVERY_PATH
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def last_recoverable(path=None):
    """The most recent item that was never confirmed as inserted."""
    for item in reversed(load(path)):
        if item.get("state") in (PENDING, TARGET_LOST, FAILED):
            return item
    return None


def _rewrite(items, path):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def trim(path=None, max_items=MAX_ITEMS, max_bytes=MAX_BYTES):
    """Bounded by count and by bytes. Section 19."""
    path = path or RECOVERY_PATH
    if not os.path.exists(path):
        return False
    items = load(path)
    trimmed = False
    if len(items) > max_items:
        items = items[-max_items:]
        trimmed = True
    while items and len(json.dumps(items).encode("utf-8")) > max_bytes:
        items.pop(0)
        trimmed = True
    if trimmed:
        _rewrite(items, path)
    return trimmed


def clear(path=None):
    """The Clear history action from Section 16."""
    path = path or RECOVERY_PATH
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def plan_insertion(target_hwnd, window_exists, text):
    """What the insertion broker should do. Section 14.

    Returned as a decision rather than performed here, so the rule that a lost
    target never types into another window is testable without a desktop.
    """
    if not (text or "").strip():
        return {"action": "none", "reason": "nothing to insert"}
    if target_hwnd is None:
        return {"action": "clipboard",
                "reason": "no target was recorded",
                "message": "Target lost - text copied"}
    if not window_exists:
        return {"action": "clipboard",
                "reason": "the original window is gone",
                "message": "Target lost - text copied"}
    return {"action": "insert", "hwnd": target_hwnd}
