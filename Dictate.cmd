@echo off
rem Start Local Dictation as a real background application.
rem
rem The previous version ran python.exe in this window, so closing the window
rem killed the app. That is not how software should behave. This launches the
rem windowless interpreter detached and then exits immediately: the app keeps
rem running with only its tray icon, and no console is left on screen.
rem
rem Output goes to dictate.log, since there is no console to print to.

cd /d "%~dp0"

rem "python" on PATH is not reliable: it depends on whatever else got
rem installed on this machine and where it landed in PATH (a bare Python 3.12
rem put there for an unrelated project silently took over "python" and broke
rem this launcher, since it has none of this app's packages). The "py"
rem launcher instead picks the Python version this app was actually set up
rem with, so ask it, not "python".
set "PYW="
for /f "delims=" %%i in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%i"
if not exist "%PYW%" set "PYW=pythonw.exe"

rem What "py" defaults to can change again later (another install taking
rem the default, this one getting removed). Since the whole point of this
rem launcher is running windowless with no console, a broken interpreter
rem would otherwise fail on the "import sounddevice" line inside dictate.py
rem with nothing on screen and nothing in dictate.log to explain it - the
rem exact failure this file exists to fix. So check here, where a failure
rem can still be shown, instead of trusting the windowless process to.
"%PYW%" -c "import sounddevice, faster_whisper, keyboard, pystray" 2>nul
if errorlevel 1 (
    >>"%~dp0dictate.log" echo(
    >>"%~dp0dictate.log" echo   === launch failed %DATE% %TIME%: "%PYW%" is missing required packages ===
    set "DICTATE_LAUNCH_ERR=Local Dictation did not start. "%PYW%" is missing required packages (sounddevice / faster_whisper / keyboard / pystray). Fix: open a terminal in this folder and run  py -m pip install -r requirements.txt"
    powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show([Environment]::GetEnvironmentVariable('DICTATE_LAUNCH_ERR'), 'Local Dictation', 'OK', 'Error') | Out-Null"
    exit /b 1
)

start "" "%PYW%" "%~dp0dictate.py"
exit /b
