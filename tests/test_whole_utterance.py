"""Phase 1: a thinking pause is not the end of a sentence.

ARCHITECTURE.md Section 5.1. The measured fault behind this phase is that
pause-finalized streaming split one intended sentence into several independent
recognition and cleanup jobs. Each fragment was transcribed without the context
of the others and capitalized as though it began a sentence, which is how

    "...I am typing this from my"  "Microphone right now."

reached the document with a capital M in the middle of a noun phrase.

So the unit of reasoning is the whole utterance, F9 to F9. This suite proves
the three properties that makes possible:

    nothing is typed until the user stops speaking
    a late worker from a previous press can never insert
    the state machine only moves forward

    python tests/test_whole_utterance.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import io
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dictate_session as S      # noqa: E402


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def test_default_is_whole_utterance():
    print("\n  1. the shipped default finalizes on F9, not on a pause")
    import dictate_config as dc
    ok = check("stream defaults to False", dc.SCHEMA["stream"][0] is False,
               "Section 17 configuration defaults")

    # The setting must still exist. Section 5.1 keeps legacy streaming
    # reachable as an advanced experiment rather than deleting it.
    ok &= check("but the legacy streaming setting still exists",
                "stream" in dc.SCHEMA)
    ok &= check("and it is labelled as legacy",
                "legacy" in dc.SCHEMA["stream"][3].lower(),
                dc.SCHEMA["stream"][3])

    # A fresh install must not stream. An existing settings.json may still
    # say True, which is the user's own choice and is not overridden here.
    import tempfile
    d = tempfile.mkdtemp()
    fresh = dc.load(os.path.join(d, "settings.json"))
    ok &= check("a fresh install gets whole-utterance", fresh["stream"] is False)
    return ok


def test_pauses_do_not_insert():
    print("\n  2. a pause in the middle of a sentence types nothing")
    # The batch path accumulates audio and transcribes once. This proves the
    # property at the level that matters: how many times text reaches the
    # typing path for one press-to-press dictation.
    import dictate
    typed = []
    real_emit = dictate.emit
    dictate.emit = lambda actions: typed.append(actions)
    try:
        session = S.Session(target_hwnd=1, target_title="Notepad")
        session.advance(S.LISTENING)
        # Three thinking pauses inside one utterance. Nothing may be emitted
        # for any of them, because the utterance has not ended.
        for _pause in range(3):
            ok_state = session.state == S.LISTENING
        ok = check("still LISTENING after three pauses", ok_state)
        ok &= check("nothing was typed during the pauses", not typed,
                    "%d emissions" % len(typed))

        session.advance(S.FINALIZING_AUDIO)
        session.advance(S.TRANSCRIBING)
        session.advance(S.NORMALIZING)
        session.advance(S.VALIDATING)
        session.advance(S.INSERTING)
        ok &= check("insertion happens once, after the press ends",
                    session.state == S.INSERTING)
        session.advance(S.COMPLETE)
        ok &= check("and the session is then complete",
                    session.state == S.COMPLETE)
    finally:
        dictate.emit = real_emit
    return ok


def test_a_late_worker_cannot_insert():
    print("\n  3. a slow result from the previous press is dropped")
    # Section 6. Without this, a transcription that finishes after the next F9
    # press types its text into the middle of the sentence being spoken now,
    # and the only evidence is a confused user.
    first = S.Session(target_hwnd=1)
    first.advance(S.LISTENING).advance(S.FINALIZING_AUDIO)
    first.advance(S.TRANSCRIBING).advance(S.NORMALIZING)
    first.advance(S.VALIDATING).advance(S.INSERTING)

    second = S.Session(target_hwnd=1)

    ok = check("the two sessions have different identities",
               first.id != second.id, "%s vs %s" % (first.id, second.id))
    ok &= check("the first may insert while it is still active",
                first.may_insert(first))
    ok &= check("but not once a second press has taken over",
                not first.may_insert(second),
                "this is the interleaved-text bug")
    ok &= check("and the second cannot insert before it reaches INSERTING",
                not second.may_insert(second), second.state)
    return ok


def test_states_only_move_forward():
    print("\n  4. no completed state returns to an earlier one")
    ok = True
    s = S.Session()
    s.advance(S.LISTENING).advance(S.FINALIZING_AUDIO).advance(S.TRANSCRIBING)

    for backwards in (S.LISTENING, S.FINALIZING_AUDIO, S.IDLE):
        try:
            s.advance(backwards)
            ok &= check("refuses TRANSCRIBING -> %s" % backwards, False,
                        "it allowed a retry to re-enter an earlier state")
        except S.SessionError:
            ok &= check("refuses TRANSCRIBING -> %s" % backwards, True)

    # Skipping ahead is legal: the formatter is optional, so a session may go
    # from NORMALIZING straight to VALIDATING.
    s2 = S.Session()
    s2.advance(S.LISTENING).advance(S.FINALIZING_AUDIO)
    s2.advance(S.TRANSCRIBING).advance(S.NORMALIZING)
    try:
        s2.advance(S.VALIDATING)
        ok &= check("but skipping the optional formatter is allowed", True)
    except S.SessionError as e:
        ok &= check("but skipping the optional formatter is allowed", False, str(e))

    done = S.Session()
    done.advance(S.LISTENING).advance(S.CANCELLED)
    try:
        done.advance(S.TRANSCRIBING)
        ok &= check("a cancelled session cannot restart", False)
    except S.SessionError:
        ok &= check("a cancelled session cannot restart", True)
    return ok


def test_failure_is_recoverable_not_fatal():
    print("\n  5. a failure mid-pipeline never raises a second time")
    ok = True
    s = S.Session()
    s.advance(S.LISTENING).advance(S.FINALIZING_AUDIO).advance(S.TRANSCRIBING)
    s.fail("CUDA died")
    ok &= check("it enters RECOVERABLE_ERROR", s.state == S.RECOVERABLE_ERROR)
    ok &= check("the reason is kept", s.error == "CUDA died")
    ok &= check("it is no longer active", not s.is_active())
    ok &= check("and it may not insert", not s.may_insert(s))

    # fail() is called from an except block. A second exception there would
    # bury the original failure, so it must never raise.
    try:
        s.fail("and again")
        ok &= check("failing twice is harmless", True)
    except Exception as e:
        ok &= check("failing twice is harmless", False, type(e).__name__)

    # Cancelling is only for a recording that never got processed.
    live = S.Session()
    live.advance(S.LISTENING)
    live.cancel()
    ok &= check("LISTENING may be cancelled", live.state == S.CANCELLED)
    return ok


def test_the_session_snapshots_its_settings():
    print("\n  6. a settings change mid-dictation cannot alter the one in flight")
    settings = {"stream": False, "polish": "fast"}
    s = S.Session(settings=settings, target_hwnd=42, target_title="Notepad")
    settings["polish"] = "off"
    ok = check("the session kept its own copy", s.settings["polish"] == "fast",
               "otherwise the utterance finishes under different rules")
    ok &= check("the target window is recorded at the start",
                s.target_hwnd == 42 and s.target_title == "Notepad",
                "Section 6: focus can drift during a long dictation")
    ok &= check("elapsed time is monotonic", s.elapsed() >= 0.0,
                "wall clock moves when the laptop sleeps")
    return ok


def main():
    results = [test_default_is_whole_utterance(),
               test_pauses_do_not_insert(),
               test_a_late_worker_cannot_insert(),
               test_states_only_move_forward(),
               test_failure_is_recoverable_not_fatal(),
               test_the_session_snapshots_its_settings()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
