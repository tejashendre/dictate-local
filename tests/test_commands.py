"""The command grammar, tested as a pure function.

Two halves, and the second is the important one:

  FIRE    - the command must fire
  QUIET   - the words appear in ordinary speech and must NOT fire

A command that fires when it should is table stakes. A command that stays
quiet when the same words are used as a noun is what makes the feature safe
to leave switched on.
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dictate_core as core   # noqa: E402


def typed(text, last=""):
    actions, notes = core.plan(text, last)
    return "".join(a[1] for a in actions if a[0] == "type"), actions, notes


FIRE = [
    ("I finished the report full stop send it tomorrow",
     "I finished the report. Send it tomorrow"),
    ("Dear Ravi new line thanks for the update",
     "Dear Ravi\nThanks for the update"),
    ("That is the summary new paragraph now for the numbers",
     "That is the summary\n\nNow for the numbers"),
    ("Are you coming question mark I need to know",
     "Are you coming? I need to know"),
    ("That is amazing exclamation mark",
     "That is amazing!"),
    ("I work at zalando cap that",
     "I work at Zalando"),
    ("Just type literally comma please",
     "Just type comma please"),
    # Escaping the first token breaks the command match; the rest is then
    # just ordinary words, so the whole phrase types through.
    ("literally scratch that",
     "scratch that"),
    ("literally full stop",
     "full stop"),
    ("literally new paragraph",
     "new paragraph"),
]

QUIET = [
    ("The car came to a full stop at the traffic light",
     "The car came to a full stop at the traffic light"),
    ("The comma in that sentence is in the wrong place",
     "The comma in that sentence is in the wrong place"),
    ("I started a new line of work last year",
     "I started a new line of work last year"),
    ("She asked me the question mark my words",
     "She asked me the question mark my words"),
    ("Put a period at the end",
     "Put a period at the end"),
    ("Every new paragraph in the document was indented",
     "Every new paragraph in the document was indented"),
]


def main():
    fails = []

    print("  FIRE - the command must fire")
    for said, want in FIRE:
        got, _, notes = typed(said)
        ok = got == want
        print("    %s  %s" % ("ok  " if ok else "FAIL", said))
        if not ok:
            print("         want: %r" % want)
            print("         got : %r" % got)
            fails.append(said)

    print("\n  QUIET - ordinary speech, nothing may fire")
    for said, want in QUIET:
        got, _, notes = typed(said)
        ok = got == want and not notes
        print("    %s  %s" % ("ok  " if ok else "FAIL", said))
        if not ok:
            print("         want: %r" % want)
            print("         got : %r  notes=%s" % (got, notes))
            fails.append(said)

    print("\n  SCRATCH THAT across utterances")
    prev = "The meeting is on Tuesday "
    actions, notes = core.plan("scratch that", prev)
    want = [("backspace", len(prev))]
    ok = actions == want
    print("    %s  erases the %d chars previously typed" % ("ok  " if ok else "FAIL", len(prev)))
    if not ok:
        print("         want: %r\n         got : %r" % (want, actions))
        fails.append("scratch across")

    actions, notes = core.plan("The meeting is on Tuesday scratch that Wednesday")
    got = "".join(a[1] for a in actions if a[0] == "type")
    ok = got == "Wednesday" and not any(a[0] == "backspace" for a in actions)
    print("    %s  mid-utterance keeps only what follows -> %r" % ("ok  " if ok else "FAIL", got))
    if not ok:
        fails.append("scratch mid")

    print("\n  %s  (%d failure%s)" % ("PASS" if not fails else "FAIL",
                                      len(fails), "" if len(fails) == 1 else "s"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
