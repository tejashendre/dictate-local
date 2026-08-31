@echo off
title Find Your Key
cd /d "%~dp0"
python dictate.py --keys
pause
