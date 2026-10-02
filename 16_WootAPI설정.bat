@echo off
title Hotdeal Radar - Woot API Setup
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python setup_woot.py
echo.
pause
