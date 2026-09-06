"""One press-to-press dictation, and the states it is allowed to pass through.

ARCHITECTURE.md Section 6. The reason this exists as its own object rather than
as loose globals is the rule at the end of that section:

    A worker result may insert text only when its session identifier still
    matches the active completed session.

Without an identity there is nothing to compare. A slow transcription from the
previous F9 press would arrive after the next one had started and type its text
into the middle of a new sentence, and the only evidence would be a confused
user. With an identity the late result is recognised as stale and dropped.

The state machine is deliberately one-directional. Section 6 says no completed
state may return to an earlier state, which rules out the class of bug where a
retry re-enters TRANSCRIBING while an insertion is already in flight.

Nothing here touches audio, a model, the keyboard, or the disk. It is the
bookkeeping only, so the whole of it is testable without a GPU.
"""
import itertools
import time

IDLE = "IDLE"
LISTENING = "LISTENING"
FINALIZING_AUDIO = "FINALIZING_AUDIO"
TRANSCRIBING = "TRANSCRIBING"
NORMALIZING = "NORMALIZING"
FORMATTING = "FORMATTING"
VALIDATING = "VALIDATING"
INSERTING = "INSERTING"
COMPLETE = "COMPLETE"
RECOVERABLE_ERROR = "RECOVERABLE_ERROR"
CANCELLED = "CANCELLED"

# The happy path, in order. Section 6.
PIPELINE = (IDLE, LISTENING, FINALIZING_AUDIO, TRANSCRIBING, NORMALIZING,
            FORMATTING, VALIDATING, INSERTING, COMPLETE)

TERMINAL = frozenset((COMPLETE, CANCELLED))

# Anything past LISTENING is processing, and processing may fail.
PROCESSING = frozenset((FINALIZING_AUDIO, TRANSCRIBING, NORMALIZING,
                        FORMATTING, VALIDATING, INSERTING))

_ORDER = {name: i for i, name in enumerate(PIPELINE)}
_counter = itertools.count(1)


class SessionError(RuntimeError):
    """A transition the architecture forbids."""


class Session:
    """One F9 press to the next, with an identity a late worker can be checked
    against."""

    __slots__ = ("id", "state", "started", "target_hwnd", "target_title",
                 "microphone", "app_category", "settings", "history",
                 "error", "audio_seconds")

    def __init__(self, target_hwnd=None, target_title="", microphone="",
                 app_category="unknown", settings=None):
        self.id = "s%04d-%d" % (next(_counter), int(time.time()))
        self.state = IDLE
        # Monotonic, because wall clock moves when the laptop sleeps and this
        # number is used for latency budgets.
        self.started = time.monotonic()
        self.target_hwnd = target_hwnd
        self.target_title = target_title
        self.microphone = microphone
        self.app_category = app_category
        # A snapshot, so a settings change mid-dictation cannot alter how the
        # utterance already in flight is finished.
        self.settings = dict(settings or {})
        self.history = [(IDLE, 0.0)]
        self.error = None
        self.audio_seconds = 0.0

    # -- transitions ------------------------------------------------------

    def can_advance(self, target):
        """Is this transition allowed from where we are now?"""
        if self.state in TERMINAL:
            return False
        if target == RECOVERABLE_ERROR:
            return self.state in PROCESSING or self.state == LISTENING
        if target == CANCELLED:
            return self.state == LISTENING
        if self.state == RECOVERABLE_ERROR:
            return False
        if target not in _ORDER or self.state not in _ORDER:
            return False
        # Forward only. Skipping ahead is allowed because the formatter is
        # optional and a session may go straight from NORMALIZING to
        # VALIDATING, but going back never is.
        return _ORDER[target] > _ORDER[self.state]

    def advance(self, target):
        """Move to the next state, or raise if the architecture forbids it."""
        if not self.can_advance(target):
            raise SessionError("%s cannot move from %s to %s"
                               % (self.id, self.state, target))
        self.state = target
        self.history.append((target, self.elapsed()))
        return self

    def fail(self, reason):
        """Enter RECOVERABLE_ERROR. Never raises, because the caller is
        already handling a failure and a second exception would bury it."""
        self.error = str(reason)
        if self.can_advance(RECOVERABLE_ERROR):
            self.state = RECOVERABLE_ERROR
            self.history.append((RECOVERABLE_ERROR, self.elapsed()))
        return self

    def cancel(self):
        if self.can_advance(CANCELLED):
            self.state = CANCELLED
            self.history.append((CANCELLED, self.elapsed()))
        return self

    # -- queries ----------------------------------------------------------

    def elapsed(self):
        return time.monotonic() - self.started

    def is_active(self):
        return self.state not in TERMINAL and self.state != RECOVERABLE_ERROR

    def may_insert(self, active_session):
        """Section 6: a worker may insert only into the session that is still
        the active one.

        This is what stops a slow transcription from the previous press typing
        into the sentence you are speaking now.
        """
        return (active_session is not None
                and active_session.id == self.id
                and self.state == INSERTING)

    def describe(self):
        return "%s %s after %.2fs" % (self.id, self.state, self.elapsed())
