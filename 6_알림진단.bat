@echo off
title Hotdeal Radar - Telegram Check
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python diag_telegram.py
echo.
pause
