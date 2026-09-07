"""dictate_config - one settings file instead of eleven environment variables.

The tool grew a switch per feature and ended up with four launchers and a wall
of DICTATE_* variables. That is a toolkit, not an app. Everything now lives in
settings.json next to the code, editable from the pill.

Precedence, highest first:

    1. an environment variable, if set   - so a test can force a value
    2. settings.json
    3. the default below

Environment variables still work, which keeps every existing test and the
tuning notes valid, but nobody has to use them.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "settings.json")

# Bumped when a stored settings file needs changing rather than merely
# extending. Adding a key never needs this: an absent key already falls back to
# its default. This is only for the case below, where a value that was once
# right became wrong.
SETTINGS_VERSION = 1


# Keys that exist for the code rather than for the user. dictate.py never reads
# these and no settings window shows them, so the end-to-end check that every
# setting reaches the running app has to skip them - and, so this cannot become
# a place to hide a dead setting, that same check proves each one is really used
# inside this module.
INTERNAL = ("settings_version",)

# key -> (default, env var, needs restart, description)
SCHEMA = {
    "hotkey":       ("f9",       "DICTATE_KEY",      True,
                     "Key that starts and stops dictation"),
    "model":        ("small.en", "DICTATE_MODEL",    True,
                     "Whisper model. small.en suits this GPU"),
    "device":       ("auto",     "DICTATE_DEVICE",   True,
                     "auto, cuda or cpu"),
    # ARCHITECTURE.md Section 5.1: F9 release ends the utterance, not a
    # thinking pause. Pause chunking split one intended sentence into several
    # independent recognition and cleanup jobs, which is what made the final
    # text worse than the raw transcript. Legacy streaming stays reachable from
    # Advanced settings until v2 passes acceptance.
    "settings_version": (SETTINGS_VERSION, None, False,
                     "Internal. Which migrations have already been applied"),
    "stream":       (False,      "DICTATE_STREAM",   True,
                     "Legacy: type as you pause, instead of only when you stop"),
    "pause_s":      (0.7,        "DICTATE_PAUSE",    False,
                     "Silence that ends a phrase, in seconds"),
    "voice_level":  (0.0,        None,               False,
                     "Learned loudness of your voice. Set automatically; "
                     "0 means not learned yet"),
    "noise_gate":   (True,       "DICTATE_GATE",     False,
                     "Ignore voices quieter than yours, such as a television"),
    "vad_threshold": (0.6,       "DICTATE_VAD",      False,
                      "How strict to be about what counts as speech. Higher "
                      "ignores more background noise"),
    "polish":       ("fast",     "DICTATE_POLISH",   False,
                     "off, fast or llm cleanup"),
    "vocab":        (True,       "DICTATE_VOCAB",    True,
                     "Bias the model toward vocabulary.txt"),
    "fuzzy":        (True,       "DICTATE_FUZZY",    False,
                     "Snap near-misses back to your terms"),
    "commands":     (True,       "DICTATE_COMMANDS", False,
                     "Spoken punctuation and commands"),
    "overlay":      (True,       "DICTATE_OVERLAY",  True,
                     "Show the floating pill"),
    "hide_console": (False,      "DICTATE_HIDE_CONSOLE", True,
                     "Hide the console window, pill only"),
    "run_at_login": (False,      None,               False,
                     "Start automatically when Windows starts"),
}


def _migrate(stored):
    """Bring an older settings file forward. Returns (stored, notes).

    Changing a default does not reach anyone who already has a settings file,
    because a stored value wins over a default. That is correct almost always,
    and it silently withheld the entire v2 decode path here.

    "stream" defaulted to True in v1. Section 5.1 changed it to False because
    pause chunking splits one intended sentence into several independent
    recognition and cleanup jobs, which is the fault v2 exists to fix. Measured
    on 51 real recordings, replaying the same audio through the real
    StreamingSession at the user's own pause_s and vad_threshold:

        group                    n     whole    stream     cost
        split into phrases       5     10.3%     44.4%    +34.1   names 7/7 -> 4/7
        emitted as one phrase   46     22.3%     22.5%     +0.2   names unchanged

    So streaming costs nothing until it actually splits a sentence, and roughly
    one utterance in ten gets split. On those it loses a third of the words and
    three names of seven, which drops protected names to 86.7% - below the 95%
    Section 21.3 asks for.

    Applied once. The version stamp is what makes that true: someone who turns
    streaming back on afterwards has made a choice, and a migration that kept
    overruling it would be a bug rather than a fix. Streaming stays reachable in
    Advanced settings, which is what Section 5.1 asks for.
    """
    notes = []
    if not stored:
        # No file yet, or an unreadable one. Defaults already carry the new
        # behaviour, so there is nothing to bring forward.
        return stored, notes

    version = stored.get("settings_version")
    if not isinstance(version, int) or isinstance(version, bool):
        version = 0

    if version < 1:
        if stored.get("stream") is True:
            stored["stream"] = False
            notes.append("stream: true -> false. Pause chunking cost 34 points "
                         "of accuracy on the utterances it split. Turn it back "
                         "on in Advanced settings if you want live text.")
        version = 1

    stored["settings_version"] = version
    return stored, notes


def _coerce(value, default):
    """Environment variables arrive as strings. Match the default's type."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("0", "false", "no", "off", "")
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return str(value)


def load(path=None):
    """Return the full settings dict, env overriding file overriding default."""
    path = path or PATH
    stored = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except Exception:
        pass
    # Valid JSON is not necessarily a settings file. "null" parses to None and
    # the lookup below then raises TypeError, which happens before the log
    # exists and so takes the app down with nothing to read. A bare string is
    # worse than useless: "key in stored" would quietly match a substring.
    if not isinstance(stored, dict):
        stored = {}

    stored, notes = _migrate(stored)

    out = {}
    for key, (default, env, _restart, _desc) in SCHEMA.items():
        value = default
        if key in stored:
            value = _coerce(stored[key], default)
        if env and env in os.environ:
            value = _coerce(os.environ[env], default)
        out[key] = value
    if notes:
        # Printed rather than logged: this runs before the log file exists, and
        # a settings file changing underneath someone without a word is exactly
        # the kind of silent behaviour this project keeps getting bitten by.
        print("  settings migrated to version %d:" % SETTINGS_VERSION)
        for note in notes:
            print("    %s" % note)
        save(out, path if path != PATH else None)

    return out


def save(settings, path=None):
    """Write only the keys we know about, so the file stays clean.

    Refuses to write the LIVE file while a test is running. Not caution - a bug
    that happened: tests/test_endtoend.py imports dictate and pushes the loud
    synthetic corpus through the real transcribe path, which learned that level
    and saved it. The real microphone is about ten times quieter, so the noise
    gate then rejected every real phrase and dictation stopped working, with
    the reason visible only in a log file.

    Tests can still prove persistence by passing an explicit path; only the
    user's own file is protected.
    """
    target = path or PATH
    if path is None and os.environ.get("DICTATE_TESTING") == "1":
        return None
    data = {k: settings[k] for k in SCHEMA if k in settings}
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, target)    # atomic, so a crash cannot leave half a file
    return target


def needs_restart(key):
    return SCHEMA.get(key, (None, None, False, ""))[2]


def describe(key):
    return SCHEMA.get(key, (None, None, False, ""))[3]


# --------------------------------------------------------------------------
# Start with Windows
# --------------------------------------------------------------------------
#
# A shortcut in the Startup folder, not a registry Run key or a scheduled
# task. It is the version the user can see, understand and delete by hand,
# which matters for a personal tool that types into every window.

def _startup_dir():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def startup_shortcut_path():
    return os.path.join(_startup_dir(), "Local Dictation.lnk")


def is_startup_enabled():
    return os.path.exists(startup_shortcut_path())


def set_startup(enabled):
    """Create or remove the Startup shortcut. Returns (ok, message)."""
    link = startup_shortcut_path()
    if not enabled:
        try:
            if os.path.exists(link):
                os.remove(link)
            return True, "will no longer start with Windows"
        except Exception as e:
            return False, "could not remove shortcut: %s" % e

    target = os.path.join(HERE, "Dictate.cmd")
    if not os.path.exists(target):
        return False, "Dictate.cmd not found"
    if not os.path.isdir(_startup_dir()):
        return False, "Startup folder not found"

    import subprocess
    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        "$s = $w.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.WindowStyle = 7; "        # minimised
        "$s.Description = 'Local Dictation'; "
        "$s.Save()" % (link, target, HERE)
    )
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False, (r.stderr or "shortcut creation failed").strip()[:120]
        return os.path.exists(link), "will start with Windows"
    except Exception as e:
        return False, "could not create shortcut: %s" % e
