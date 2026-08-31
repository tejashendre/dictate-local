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

set "PYW="
for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%i"
if not exist "%PYW%" set "PYW=pythonw.exe"

start "" "%PYW%" "%~dp0dictate.py"
exit /b
