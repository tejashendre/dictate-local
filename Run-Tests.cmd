@echo off
title Dictation Tests
cd /d "%~dp0"
setlocal enabledelayedexpansion

REM Every suite in tests\ runs here. The previous version listed seven of
REM sixteen, and two of those seven were unreachable: "tests\test_endtoend.py"
REM and "tests\test_polish.py" had been saved with the \t read as a literal tab,
REM so the file names were "tests<TAB>est_endtoend.py". Five suites ran while
REM the window said the suite had passed.
REM
REM So the list is no longer hand written. Anything matching tests\test_*.py is
REM discovered and run, which means a new suite cannot be forgotten here.

echo.
echo   Running every dictation test suite.
echo   First run builds the speech corpus with the local Windows voices.
echo.

if not exist "tests\audio\control_00.wav" (
    echo   --- building test corpus ---
    python "tests\make_audio.py"
    echo.
)

set PASS=0
set FAIL=0
set FAILED=

for %%F in ("tests\test_*.py") do (
    echo   --- %%~nF ---
    python "tests\%%~nxF"
    if errorlevel 1 (
        set /a FAIL+=1
        set FAILED=!FAILED! %%~nF
    ) else (
        set /a PASS+=1
    )
    echo.
)

echo   ================================================
echo     !PASS! passed, !FAIL! failed
if not "!FAILED!"=="" echo     failing: !FAILED!
echo   ================================================
echo.
pause
