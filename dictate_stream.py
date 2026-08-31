"""dictate_stream - emit text while you are still talking.

This is the step that kills projects like this one, so it is worth being
explicit about why, and about what this file refuses to do.

Streaming dictation dies in one specific way: the tool types a guess, changes
its mind, and then has to unsay it. Backspacing over text the user can already
see produces flicker, races the user's own typing, and destroys anything they
did in the window in between. Every hard problem below is downstream of that.

So the rule here is absolute: NOTHING IS EVER RETYPED OR UNSAID. Text is only
emitted once it cannot change. That single constraint is what makes this
tractable, and it drives the whole design:

  Pause-finalised (the common case, and it is exact)
      Silero VAD watches for a natural pause. On a pause the audio so far is a
      complete phrase, so it is transcribed once and emitted once. No guessing,
      no revision, no flicker. At 120-147 WPM a speaker pauses every few
      seconds, so this alone makes text appear while they are still talking.

  Stable-prefix (the fallback, for long unbroken speech)
      If someone talks past FORCE_AFTER_S without pausing, waiting would feel
      broken. So the buffer is transcribed early and compared with the previous
      pass; the words the two passes AGREE on are stable and safe to emit. This
      is LocalAgreement-2. Anything past the agreement point stays unsaid until
      it settles. Committed audio is then trimmed so cost stays bounded.

What this file deliberately does not do: partial hypotheses, in-place
correction, or speculative typing. Those are the features that look impressive
in a demo and make the tool unusable in an editor.
"""

import numpy as np

SAMPLE_RATE = 16000

# A clause boundary, not a breath.
#
# 0.8 was the original guess and it was wrong, which a sweep showed clearly.
# On speech with mid-clause hesitation - commas, thinking pauses, the way
# anyone actually dictates - the numbers went the opposite way to expectation:
#
#     pause   first text on a 12.3s clip   chunks   words kept
#     0.4s    2.5s                         4        100%
#     0.8s    12.2s                        1        100%
#
# At 0.8 the timer never fires until the speaker stops, so streaming buys
# nothing at all. At 0.4 text lands at 2.5s with no measured loss: it splits
# at commas, which are natural boundaries, not mid-word.
#
# CORRECTED AGAIN, by real speech this time. 0.4 was chosen from that sweep,
# and on an actual dictated paragraph it cut mid-thought repeatedly:
#
#     "...and it proved the whole details. That is more important. The..."
#     "Point is..."                              <- one sentence, split in two
#     "Like having all the amount of..."
#     "Olamo."                                   <- same again
#
# A synthetic voice pauses only at commas. A person pauses to think, mid
# sentence, for longer than that. The trailing "..." in the log is Whisper
# saying it knew the sentence was unfinished.
PAUSE_S = 0.7

# Long unbroken speech: emit a stable prefix rather than making the user wait.
FORCE_AFTER_S = 12.0

# Hard ceiling. Transcribe cost grows with buffer length, so never let the
# buffer grow without bound even if VAD never sees a pause.
MAX_BUFFER_S = 30.0

# Ignore blips: a cough, a door, or a breath is not an utterance. Raised from
# 0.3 after real use - breathing during a pause was clearing the bar and
# Whisper filled the gap with "Thank you."
MIN_SPEECH_S = 0.5


def _core():
    """Imported lazily so this module stays importable without the model."""
    import dictate_core
    return dictate_core


def _tokens(text):
    return text.split()


def stable_prefix(prev, cur):
    """LocalAgreement-2: the words two consecutive passes agree on.

    Returns the number of leading tokens that match. Those cannot change, so
    they are safe to type. Everything after them is still a guess.
    """
    n = 0
    for a, b in zip(prev, cur):
        if a != b:
            break
        n += 1
    return n


class StreamingSession:
    """Accumulates audio and decides when text is safe to emit.

    Deliberately has no microphone and no keyboard in it. Audio goes in via
    feed(), decisions come out of poll(), which makes the whole policy
    testable by replaying a WAV in chunks.
    """

    def __init__(self, transcriber, prompt=None, vad_options=None,
                 pause_s=PAUSE_S, force_after_s=FORCE_AFTER_S,
                 max_buffer_s=MAX_BUFFER_S, vad_threshold=None):
        self.transcriber = transcriber
        self.prompt = prompt
        self.pause_s = pause_s
        self.force_after_s = force_after_s
        self.max_buffer_s = max_buffer_s

        # Higher threshold = stricter about what counts as speech, so a
        # television or a conversation across the room is less likely to be
        # treated as you talking. Reported in real use: "why are you listening
        # to the audio background noise".
        from faster_whisper.vad import VadOptions
        self.vad_options = vad_options or VadOptions(
            threshold=vad_threshold if vad_threshold is not None else 0.6,
            min_silence_duration_ms=200, speech_pad_ms=100)

        self.buf = np.zeros(0, dtype=np.float32)
        self._prev_tokens = []
        self._emitted_in_buf = 0     # tokens of this buffer already emitted
        self._since_commit_s = 0.0
        # Duration of the phrase most recently emitted. Without it the log
        # cannot show how fast the speaker actually talks, which is the
        # one measurement this project most needs from real use.
        self.last_phrase_s = 0.0
        self.last_speech_s = 0.0
        self.dropped_fillers = 0
        self.dropped_repeats = 0

    # -- input ------------------------------------------------------------

    def feed(self, chunk):
        """Append captured audio. Called from the audio thread, so it stays
        cheap: no VAD, no model, just a copy."""
        if chunk is None or len(chunk) == 0:
            return
        self.buf = np.concatenate([self.buf, chunk.astype(np.float32).ravel()])

    @property
    def seconds(self):
        return len(self.buf) / SAMPLE_RATE

    # -- voice activity ---------------------------------------------------

    def _speech_regions(self):
        from faster_whisper.vad import get_speech_timestamps
        if len(self.buf) < int(0.1 * SAMPLE_RATE):
            return []
        return get_speech_timestamps(self.buf, self.vad_options)

    def _trailing_silence(self, regions):
        """Seconds of silence at the end of the buffer."""
        if not regions:
            return self.seconds
        return self.seconds - (regions[-1]["end"] / SAMPLE_RATE)

    @staticmethod
    def _speech_seconds(regions):
        return sum(r["end"] - r["start"] for r in regions) / SAMPLE_RATE

    # -- decisions --------------------------------------------------------

    def poll(self):
        """Decide whether anything can be safely emitted right now.

        Returns a list of strings to type. Usually empty. Never returns text
        that a later call could contradict.
        """
        if self.seconds < MIN_SPEECH_S:
            return []

        regions = self._speech_regions()
        speech_s = self._speech_seconds(regions)

        if speech_s < MIN_SPEECH_S:
            # Nothing but noise. Drop it so the buffer does not creep upward.
            if self.seconds > 2.0:
                self._reset()
            return []

        silence = self._trailing_silence(regions)

        if silence >= self.pause_s:
            return self._finalise()

        if self.seconds >= self.max_buffer_s:
            return self._finalise()

        if self.seconds - self._since_commit_s >= self.force_after_s:
            return self._emit_stable_prefix()

        return []

    def finish(self):
        """End of dictation. Emit whatever is left, guessing nothing."""
        if self.seconds < MIN_SPEECH_S:
            self._reset()
            return []
        regions = self._speech_regions()
        if self._speech_seconds(regions) < MIN_SPEECH_S:
            self._reset()
            return []
        return self._finalise()

    # -- emitting ---------------------------------------------------------

    def _transcribe(self):
        return self.transcriber.transcribe(self.buf, prompt=self.prompt)

    def _finalise(self):
        """The phrase is complete. Transcribe once, emit the rest, reset.

        The guards here are the ones that were missing in real use. The batch
        path had them; this path did not, which is why a run of "Thank you."
        got typed during pauses. Whisper fills ambiguity with subtitle filler
        and sometimes locks into repeating one sentence.
        """
        self.last_phrase_s = self.seconds
        self.last_speech_s = self._speech_seconds(self._speech_regions())
        text = self._transcribe()

        text, repeats = _core().collapse_repetition(text)
        if repeats:
            self.dropped_repeats += repeats
        if _core().looks_hallucinated(text, self.last_speech_s):
            self.dropped_fillers += 1
            self._reset()
            return []

        toks = _tokens(text)
        out = toks[self._emitted_in_buf:]
        self._reset()
        return [" ".join(out)] if out else []

    def _emit_stable_prefix(self):
        """Mid-phrase. Emit only what two consecutive passes agree on."""
        self.last_phrase_s = self.seconds
        self.last_speech_s = self._speech_seconds(self._speech_regions())
        text = self._transcribe()
        toks = _tokens(text)
        agreed = stable_prefix(self._prev_tokens, toks)
        self._prev_tokens = toks
        self._since_commit_s = self.seconds

        if agreed <= self._emitted_in_buf:
            return []
        out = toks[self._emitted_in_buf:agreed]
        self._emitted_in_buf = agreed
        return [" ".join(out)] if out else []

    def _reset(self):
        self.buf = np.zeros(0, dtype=np.float32)
        self._prev_tokens = []
        self._emitted_in_buf = 0
        self._since_commit_s = 0.0
