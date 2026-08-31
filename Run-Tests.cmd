@echo off
title Dictation Tests
cd /d "%~dp0"
echo.
echo   Running the dictation test suite.
echo   First run builds the speech corpus with the local Windows voices.
echo.
if not exist "tests\audio\control_00.wav" (
    echo   --- building test corpus ---
    python tests\make_audio.py
    echo.
)
echo   --- END TO END: does the F9 path actually run ---
python tests	est_endtoend.py
echo.
echo   --- vocabulary: do your terms come out right ---
python tests\test_vocab.py
echo.
echo   --- near-miss snapping: and does it ever rewrite ordinary English ---
python tests\test_fuzzy.py
echo.
echo   --- polish: is filler removed, and are real words ever eaten ---
python tests	est_polish.py
echo.
echo   --- commands: do they fire, and do they stay quiet ---
python tests\test_commands.py
echo.
echo   --- gpu fallback: does it degrade instead of crashing ---
python tests\test_fallback.py
echo.
echo   --- streaming: does text appear before you stop talking ---
python tests\test_stream.py
echo.
pause
