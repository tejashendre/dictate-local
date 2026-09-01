"""No window may invent its own colour.

dictate_theme.py opens by claiming its colours are "a single set used by both
the pill and the settings window, so the app reads as one thing rather than two
programs that happen to ship together".

That was untrue when it was written. The green #3ddc84 was spelled out in four
separate files, and in install.py it was written as the RGB tuple
(61, 220, 132) - a copy that no search for the colour would ever find. Changing
the accent meant hunting every duplicate, and missing one meant the pill and
the tray quietly disagreed about what the app looked like.

So the docstring's claim is enforced here instead of merely asserted there.

    python tests/test_palette.py
"""
import os

# Never let a test write to the live settings file: a test's audio levels
# once got saved as the user's voice level and broke dictation.
os.environ["DICTATE_TESTING"] = "1"
import io
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SOURCE = "dictate_theme.py"


def check(name, ok, detail=""):
    print("    %s  %s%s" % ("ok  " if ok else "FAIL", name,
                            ("  -> " + detail) if detail else ""))
    return bool(ok)


def modules():
    """Every shipped module except the palette itself."""
    for name in sorted(os.listdir(ROOT)):
        if name.endswith(".py") and name != SOURCE:
            yield name, io.open(os.path.join(ROOT, name),
                                encoding="utf-8").read()


def palette():
    import dictate_theme as theme
    return {k: v for k, v in vars(theme).items()
            if k.isupper() and isinstance(v, str) and v.startswith("#")}


def main():
    ok = True

    print("\n  1. no colour is written outside %s" % SOURCE)
    stray = []
    for name, body in modules():
        for n, line in enumerate(body.split("\n"), 1):
            # Comments may quote a measured colour as evidence; only code that
            # actually sets one is a second source of truth.
            code = "" if line.lstrip().startswith("#") else line.split("#", 1)[0]
            for hit in re.findall(r"#[0-9a-fA-F]{6}\b", line):
                if hit in code or ('"%s"' % hit) in line and code:
                    stray.append("%s:%d %s" % (name, n, hit))
    ok &= check("every hex colour lives in the palette", not stray,
                "; ".join(stray[:4]))

    print("\n  2. and none is re-spelled as an RGB tuple")
    # This is the copy that hides: install.py drew the microphone in
    # (61, 220, 132) and grepping for #3ddc84 could never have found it.
    import dictate_theme as theme
    known = {theme.rgb(v): k for k, v in palette().items()}
    tuples = []
    for name, body in modules():
        for r, g, b in re.findall(
                r"\((\d{1,3}),\s*(\d{1,3}),\s*(\d{1,3})\s*[,)]", body):
            got = (int(r), int(g), int(b))
            if got in known:
                tuples.append("%s %s == theme.%s" % (name, got, known[got]))
    ok &= check("no palette colour appears as a raw tuple", not tuples,
                "; ".join(tuples[:4]))

    print("\n  3. the palette carries nothing dead")
    everything = "".join(body for _n, body in modules())
    everything += io.open(os.path.join(ROOT, SOURCE), encoding="utf-8").read()
    unused = [k for k in palette() if everything.count(k) <= 1]
    ok &= check("no colour is defined and never used", not unused, str(unused))

    print("\n  4. the palette can be imported without a working compositor")
    # The consumers used to guard "import dictate_theme" in try/except because
    # it touched dwmapi at import time. That guard forced every one of them to
    # carry a fallback copy of the colours - which is how the duplication got
    # in. Resolving the DLLs lazily is what makes a single source possible.
    head = io.open(os.path.join(ROOT, SOURCE), encoding="utf-8").read()
    head = head[:head.index("# ---", head.index("Palette") - 400)] \
        if "Palette" in head else head
    ok &= check("no windll lookup at import time",
                "ctypes.windll.dwmapi" not in head
                and "ctypes.windll.user32" not in head,
                "module-level windll would resurrect the fallback copies")

    print("\n  %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
