"""End to end, and the check that would have caught the F9 crash.

A real bug shipped past ten passing suites: a setting was USED inside
hotkey_loop but the line DEFINING it never landed. Nothing caught it because
no test ever entered that function - it only runs when a key is pressed. The
app started perfectly, then died the instant F9 was hit:

    NameError: name 'VAD_THRESHOLD' is not defined

Unit tests cannot catch that, because the failure is in the wiring between
parts rather than inside any one of them. Two things here do:

  1. A static pass over every function in every module, resolving each global
     name it references. A name that does not exist is a crash waiting for the
     right keypress.
  2. A real run of the recording path with the microphone and keyboard
     replaced, so the code F9 actually reaches is executed.

    python tests/test_endtoend.py
"""
import ast
import builtins
import io
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import sys
import threading
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MODULES = ["dictate.py", "dictate_core.py", "dictate_stream.py",
           "dictate_overlay.py", "dictate_polish.py", "dictate_config.py",
           "dictate_settings.py", "dictate_tray.py"]


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


# --------------------------------------------------------------------------
# 1. every global a function uses must exist
# --------------------------------------------------------------------------

# Implicitly present in every module.
DUNDERS = {"__file__", "__name__", "__doc__", "__spec__", "__package__",
           "__loader__", "__builtins__", "__debug__"}


class _Collector(ast.NodeVisitor):
    """Names each function reads, minus its own locals, params and globals.

    Only TOP-LEVEL functions are scanned. A nested function sees its parent's
    locals through the closure, so scanning it on its own would report every
    captured variable as missing - which is exactly the false positive this
    produced on first run.
    """

    def __init__(self):
        self.used = []          # (function, name, lineno)
        self._depth = 0

    def visit_FunctionDef(self, node):
        if self._depth == 0:
            self._scan(node)        # covers nested bodies via ast.walk
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def _args_of(self, fn):
        got = {a.arg for a in fn.args.args}
        got |= {a.arg for a in fn.args.kwonlyargs}
        if fn.args.vararg:
            got.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            got.add(fn.args.kwarg.arg)
        return got

    def _scan(self, fn):
        bound = set(DUNDERS) | self._args_of(fn)
        # parameters of any nested function are bound inside it too
        for node in ast.walk(fn):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound |= self._args_of(node)
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in ast.walk(node):
                    if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                        bound.add(t.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.For, ast.comprehension)):
                target = getattr(node, "target", None)
                for t in ast.walk(target) if target else []:
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
            elif isinstance(node, ast.With):
                for item in node.items:
                    if item.optional_vars is not None:
                        for t in ast.walk(item.optional_vars):
                            if isinstance(t, ast.Name):
                                bound.add(t.id)
            elif isinstance(node, ast.Lambda):
                bound |= self._args_of(node)

        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in bound:
                    self.used.append((fn.name, node.id, node.lineno))


def test_names_resolve():
    print("\n  1. every global referenced inside a function exists")
    ok = True
    builtin_names = set(dir(builtins))
    for fname in MODULES:
        path = os.path.join(ROOT, fname)
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)

        # names defined at module level
        module_level = set(builtin_names)
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in ast.walk(node):
                    if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                        module_level.add(t.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                module_level.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    module_level.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.Try):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        for a in sub.names:
                            module_level.add((a.asname or a.name).split(".")[0])
                    elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        module_level.add(sub.id)
            elif isinstance(node, ast.If):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        module_level.add(sub.id)

        collector = _Collector()
        collector.visit(tree)
        missing = sorted({(fn, n, ln) for fn, n, ln in collector.used
                          if n not in module_level})
        ok &= check("%-22s %d functions clean" % (fname, len(
            [n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)])),
            not missing,
            "; ".join("%s uses %r (line %d)" % m for m in missing[:3]))
    return ok


# --------------------------------------------------------------------------
# 2. the path F9 actually reaches, with real audio
# --------------------------------------------------------------------------

def load_wav(path):
    with wave.open(path, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return a.astype(np.float32)


def test_record_paths():
    print("\n  2. the recording paths run, with mic and keyboard replaced")
    clip = os.path.join(HERE, "audio", "stream_long.wav")
    if not os.path.exists(clip):
        print("    skip  no corpus; run tests/make_audio.py")
        return True

    import dictate
    import dictate_core as core
    import dictate_stream

    typed = []
    dictate.keyboard.write = lambda t, delay=0: typed.append(t)
    dictate.keyboard.send = lambda k: None

    terms = core.load_vocabulary()
    rules = core.load_corrections()
    prompt, _, _ = core.build_prompt(terms)
    model = core.Transcriber(dictate.MODEL_NAME,
                             on_event=lambda m: None).load()
    audio = load_wav(clip)

    # batch path - what F9 does with streaming off
    typed.clear()
    dictate.transcribe_and_type(model, audio, len(audio) / 16000.0,
                                prompt, rules, terms)
    batch_text = " ".join(typed)
    ok = check("batch path typed something",
               len(batch_text.split()) > 5, batch_text[:56])

    # streaming path - what F9 does by default, through the real worker
    typed.clear()
    session = dictate_stream.StreamingSession(
        model, prompt=prompt, pause_s=dictate.PAUSE_S,
        vad_threshold=dictate.VAD_THRESHOLD)
    stop_evt = threading.Event()
    worker = threading.Thread(target=dictate.stream_worker,
                              args=(session, stop_evt, rules, terms),
                              daemon=True)
    worker.start()
    BLOCK = 1024
    for i in range(0, len(audio), BLOCK):
        dictate._q.put(audio[i:i + BLOCK].reshape(-1, 1))
        time.sleep(BLOCK / 16000.0)
    stop_evt.set()
    worker.join(timeout=60)
    stream_text = " ".join(typed)
    ok &= check("streaming path typed something",
                len(stream_text.split()) > 5, stream_text[:56])
    ok &= check("vocabulary survived the whole pipeline",
                "Zalando" in stream_text and "Naukri" in stream_text)
    ok &= check("worker thread exited cleanly", not worker.is_alive())
    return ok


def test_settings_reach_runtime():
    print("\n  3. every setting is actually read by the running app")
    import dictate
    import dictate_config
    src = io.open(os.path.join(ROOT, "dictate.py"), encoding="utf-8").read()
    unread = [k for k in dictate_config.SCHEMA
              if ('CFG["%s"]' % k) not in src and ('"%s"' % k) not in src]
    ok = check("no setting is silently ignored", not unread, str(unread))
    ok &= check("VAD_THRESHOLD is defined",
                hasattr(dictate, "VAD_THRESHOLD"),
                "this is the exact name that crashed F9")
    for name in ("PAUSE_S", "POLISH", "HOTKEY", "USE_STREAM", "MODEL_NAME"):
        ok &= check("%s is defined" % name, hasattr(dictate, name))
    return ok


def main():
    results = [test_names_resolve(), test_settings_reach_runtime(),
               test_record_paths()]
    print("\n  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
