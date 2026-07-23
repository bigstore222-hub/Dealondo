@echo off
title Hotdeal Radar - Board
cd /d "%~dp0web"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo.
echo ============================================
echo   딜보드를 브라우저로 엽니다
echo   이 창을 닫으면 딜보드도 닫힙니다
echo ============================================
echo.
start "" http://localhost:8000
python -m http.server 8000
pause
