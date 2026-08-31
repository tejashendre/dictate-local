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
set DICTATE_STREAM=1
set DICTATE_HIDE_CONSOLE=1
echo.
echo   Running elevated. F9 works in every window now.
echo.
python dictate.py
