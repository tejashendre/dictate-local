@echo off
rem Dictation that reaches EVERY window, including elevated ones.
rem
rem Windows enforces User Interface Privilege Isolation: a normal process
rem cannot send keystrokes to a window owned by an administrator process. So
rem without this, dictating into Task Manager or an admin terminal silently
rem does nothing - no error, no text. Running elevated is the only fix.
rem
rem This asks for the UAC prompt once, then behaves like Dictate-Hands-Free.

net session >nul 2>&1
if %errorLevel% == 0 goto :run

echo.
echo   Asking for administrator rights so dictation reaches every window.
echo   You will see one UAC prompt.
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
title Local Dictation (everywhere)
cd /d "%~dp0"
rem DICTATE_STREAM=1 used to be set here, which forced the legacy
rem pause-chunked mode back on. An environment variable outranks both the
rem saved setting and the one-time migration, so running this launcher
rem silently reinstated a mode measured at 34 points worse on the
rem utterances it splits. This launcher exists for reach, not for decoding
rem behaviour, so it no longer touches it.
set DICTATE_HIDE_CONSOLE=1
echo.
echo   Running elevated. F9 works in every window now.
echo.
python dictate.py
