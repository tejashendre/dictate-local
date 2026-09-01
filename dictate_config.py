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

# key -> (default, env var, needs restart, description)
SCHEMA = {
    "hotkey":       ("f9",       "DICTATE_KEY",      True,
                     "Key that starts and stops dictation"),
    "model":        ("small.en", "DICTATE_MODEL",    True,
                     "Whisper model. small.en suits this GPU"),
    "device":       ("auto",     "DICTATE_DEVICE",   True,
                     "auto, cuda or cpu"),
    "stream":       (True,       "DICTATE_STREAM",   True,
                     "Type as you pause, instead of only when you stop"),
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


def load():
    """Return the full settings dict, env overriding file overriding default."""
    stored = {}
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except Exception:
        pass

    out = {}
    for key, (default, env, _restart, _desc) in SCHEMA.items():
        value = default
        if key in stored:
            value = _coerce(stored[key], default)
        if env and env in os.environ:
            value = _coerce(os.environ[env], default)
        out[key] = value
    return out


def save(settings):
    """Write only the keys we know about, so the file stays clean."""
    data = {k: settings[k] for k in SCHEMA if k in settings}
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, PATH)      # atomic, so a crash cannot leave half a file
    return PATH


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
