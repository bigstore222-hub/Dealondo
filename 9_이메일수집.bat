@echo off
title Hotdeal Radar - Email Deals
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set LOG=%~dp0이메일수집결과.txt
echo.
echo ============================================
echo   이메일 뉴스레터에서 딜 추출
echo ============================================
echo.
python sources_email.py
echo.
echo ============================================
echo   끝났습니다
echo ============================================
echo.
pause
