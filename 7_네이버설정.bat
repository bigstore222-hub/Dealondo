@echo off
title Hotdeal Radar - Naver API Setup
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python setup_naver.py
echo.
pause
