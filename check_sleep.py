"""Did the app survive the last time this machine slept?

Run this after closing the lid and opening it again. It answers one question
and nothing else, because that question took six attempts to get right and is
the only one that cannot be settled from a test suite:

    when Windows suspended and resumed, did dictation still work?

It reads two things already on disk. dictate.log, which the app writes, and the
Windows Kernel-Power event log, which records every entry into and exit from
low-power standby. Nothing here needs the app to be running and nothing is sent
anywhere.

    python check_sleep.py

The history, so the output makes sense. F9 stopped working after sleep five
separate times. Four fixes each corrected a real bug in the recovery routine
and none of them helped, because the watchdog inferred sleep from a wall-clock
jump and Modern Standby never produces one, so nothing ever called the routine.
The fifth fired correctly and the process then crashed in native code, because
the statement written to leak the dead model was in fact freeing it. The sixth
gave up on rebuilding the GPU inside a process whose CUDA context is gone and
hands off to a fresh process instead.

That sixth attempt is what this checks. It has never faced a real sleep.
"""
import io
import os
import re
import subprocess
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "dictate.log")


def standby_windows():
    """(entered, exited) pairs from Kernel-Power, most recent last."""
    ps = (
        "Get-WinEvent -FilterHashtable @{LogName='System';"
        "ProviderName='Microsoft-Windows-Kernel-Power';"
        "StartTime=(Get-Date).AddDays(-2)} -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Id -in 506,507} | "
        "Sort-Object TimeCreated | "
        "ForEach-Object { '{0:yyyy-MM-dd HH:mm:ss} {1}' -f $_.TimeCreated, $_.Id }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=40).stdout
    except Exception:
        return []
    events = []
    for line in out.splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(50[67])", line.strip())
        if m:
            events.append((m.group(1), m.group(2)))
    spans, entered = [], None
    for when, kind in events:
        if kind == "506":
            entered = when
        elif kind == "507" and entered:
            spans.append((entered, when))
            entered = None
    return spans


def app_faults():
    """Times pythonw crashed, which is how this failed for five of six tries."""
    ps = ("Get-WinEvent -FilterHashtable @{LogName='Application';"
          "StartTime=(Get-Date).AddDays(-2);Id=1000} -ErrorAction SilentlyContinue | "
          "Where-Object { $_.Message -like '*pythonw*' } | "
          "ForEach-Object { '{0:yyyy-MM-dd HH:mm:ss}' -f $_.TimeCreated }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=40).stdout
    except Exception:
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def running():
    ps = ("$p = Get-Process -Name pythonw -ErrorAction SilentlyContinue | "
          "Select-Object -First 1; if ($p) { 'yes' } else { 'no' }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20).stdout
        return out.strip() == "yes"
    except Exception:
        return None


def main():
    print()
    if not os.path.exists(LOG):
        print("  no dictate.log next to the app; has it ever run?")
        return 1
    log = io.open(LOG, encoding="utf-8", errors="replace").read()

    spans = standby_windows()
    if not spans:
        print("  Windows has not recorded a sleep in the last two days.")
        print("  Close the lid, wait a few minutes, open it, and run this again.")
        return 0

    entered, exited = spans[-1]
    mins = 0
    try:
        a = datetime.datetime.strptime(entered, "%Y-%m-%d %H:%M:%S")
        b = datetime.datetime.strptime(exited, "%Y-%m-%d %H:%M:%S")
        mins = (b - a).total_seconds() / 60.0
    except Exception:
        pass
    print("  last sleep : %s to %s  (%.0f minutes)" % (entered, exited, mins))

    # Only look at the session that was running when the sleep happened.
    # Searching the whole file matches "woke after" lines left by earlier runs
    # and reports a survival that never happened, which this tool did on its
    # very first execution.
    starts = [(m.group(1), m.end()) for m in
              re.finditer(r"=== started (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", log)]
    if starts:
        began, at = starts[-1][0], starts[-1][1]
        session = log[at:]
    else:
        began, session = None, log

    if began and began > exited:
        print("  the app was started AFTER that sleep, at %s," % began)
        print("  so this instance has not been through one yet.")
        print()
        print("  VERDICT: nothing to judge yet. Close the lid, wait a few")
        print("  minutes, open it, then run this again.")
        return 0

    woke = re.findall(r"woke after ([^\n]+), re-arming", session)
    handed = "handing off to a fresh process" in session
    faults = [f for f in app_faults() if f >= exited]
    alive = running()

    print("  detected   : %s" % ("yes, '%s'" % woke[-1] if woke
                                 else "NO, the watchdog never fired"))
    print("  handed off : %s" % ("yes" if handed else "no"))
    print("  crashed    : %s" % (", ".join(faults) if faults else "no"))
    print("  running now: %s" % {True: "yes", False: "no",
                                 None: "could not tell"}[alive])
    print()

    if not woke:
        print("  VERDICT: the watchdog did not notice the sleep.")
        print("  That is the fault that hid four correct fixes. Send")
        print("  dictate.log to whoever maintains this.")
        return 1
    if faults:
        print("  VERDICT: it noticed the sleep and then crashed anyway.")
        print("  That is the sixth failure of this bug. The hand-off did not")
        print("  save it, and the crash time above is the evidence.")
        return 1
    if alive is False:
        print("  VERDICT: no crash recorded, but nothing is running.")
        print("  Windows may have hibernated, which kills the process outright.")
        print("  run_at_login in settings decides whether it comes back alone.")
        return 1

    print("  VERDICT: it survived. Press F9 and say something to be certain,")
    print("  because a process that is alive is not proof the hotkey is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
