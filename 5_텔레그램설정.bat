@echo off
title Hotdeal Radar - Telegram Setup
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python setup_telegram.py
echo.
pause
