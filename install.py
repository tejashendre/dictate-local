"""Make Local Dictation behave like an installed application.

Not a PyInstaller .exe, and that is a deliberate call rather than a shortcut.
The dependencies weigh over 1 GB before the speech model is counted - almost
all of it the CUDA runtime - so a single-file build would be roughly 1.5 GB,
would unpack itself to a temp folder on every launch, and would put the
fragile CUDA DLL discovery behind another layer of indirection. It would be a
worse program that merely looked more official.

What actually makes something feel installed is being in the Start Menu with
its own icon, launching without a console, and running until you quit it. That
is all achievable directly, so this does that:

    python install.py            add Start Menu (and Desktop) shortcuts
    python install.py --remove   take them away again

Everything stays in this folder. Nothing is copied into Program Files and
nothing is written to the registry, so removing it is deleting shortcuts.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "dictation.ico")
TARGET = os.path.join(HERE, "Dictate.cmd")
NAME = "Local Dictation"


def start_menu_dir():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs")


def desktop_dir():
    return os.path.join(os.path.expanduser("~"), "Desktop")


def make_icon(path=ICON):
    """Write a multi-size .ico so it looks right everywhere Windows shows it."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    sizes = [256, 128, 64, 48, 32, 16]
    base = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    # A microphone capsule on a dark rounded field: reads at 16px, which is
    # the size that actually matters in a taskbar.
    d.rounded_rectangle((8, 8, 248, 248), radius=56, fill=(22, 22, 22, 255))
    d.rounded_rectangle((104, 56, 152, 148), radius=24, fill=(61, 220, 132, 255))
    d.arc((76, 96, 180, 188), start=0, end=180, fill=(61, 220, 132, 255), width=14)
    d.line((128, 188, 128, 212), fill=(61, 220, 132, 255), width=14)
    d.line((100, 212, 156, 212), fill=(61, 220, 132, 255), width=14)
    base.save(path, format="ICO",
              sizes=[(s, s) for s in sizes])
    return os.path.exists(path)


def make_shortcut(link_path, description=NAME):
    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        "$s = $w.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.WindowStyle = 7; "
        "$s.Description = '%s'; "
        % (link_path, TARGET, HERE, description)
    )
    if os.path.exists(ICON):
        ps += "$s.IconLocation = '%s'; " % ICON
    ps += "$s.Save()"
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=30)
    return os.path.exists(link_path), (r.stderr or "").strip()[:160]


def install():
    print("  Local Dictation - install\n")
    ok = make_icon()
    print("  %s icon        %s" % ("ok  " if ok else "skip", ICON if ok else
                                   "Pillow not available, using default"))

    made = []
    for folder, label in ((start_menu_dir(), "Start Menu"),
                          (desktop_dir(), "Desktop")):
        if not os.path.isdir(folder):
            print("  skip %s not found" % label)
            continue
        link = os.path.join(folder, NAME + ".lnk")
        good, err = make_shortcut(link)
        print("  %s %-12s %s" % ("ok  " if good else "FAIL", label,
                                 link if good else err))
        if good:
            made.append(link)

    print("\n  Done. Press the Windows key and type 'Local Dictation'.")
    print("  It starts with no console and lives in the notification area.")
    print("  To start it automatically at login, use Settings in the tray menu.")
    return 0 if made else 1


def remove():
    print("  Local Dictation - remove shortcuts\n")
    gone = 0
    for folder in (start_menu_dir(), desktop_dir()):
        link = os.path.join(folder, NAME + ".lnk")
        if os.path.exists(link):
            try:
                os.remove(link)
                print("  removed %s" % link)
                gone += 1
            except Exception as e:
                print("  could not remove %s: %s" % (link, e))
    # the login shortcut lives elsewhere and belongs to the settings window
    try:
        sys.path.insert(0, HERE)
        import dictate_config
        if dictate_config.is_startup_enabled():
            dictate_config.set_startup(False)
            print("  removed the start-with-Windows entry")
            gone += 1
    except Exception:
        pass
    if os.path.exists(ICON):
        try:
            os.remove(ICON)
            print("  removed %s" % ICON)
        except Exception:
            pass
    print("\n  %d item(s) removed. The app itself is still in this folder."
          % gone)
    return 0


if __name__ == "__main__":
    sys.exit(remove() if "--remove" in sys.argv else install())
