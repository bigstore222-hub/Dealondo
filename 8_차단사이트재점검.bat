@echo off
title Hotdeal Radar - Recheck Blocked Sites
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set LOG=%~dp0재점검결과.txt
python recheck_blocked.py > "%LOG%" 2>&1
type "%LOG%"
echo.
start notepad "%LOG%"
pause
