@echo off
title Dealondo - Threads Token Check
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python setup_threads.py --check
echo.
pause
