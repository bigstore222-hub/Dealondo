@echo off
title Hotdeal Radar - Running
cd /d "%~dp0scraper"
set RADAR_SCROLL_WAIT_MS=700
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo.
echo ============================================
echo   핫딜 레이더 상시 감시
echo.
echo   이 창을 켜두면 계속 딜을 감시합니다
echo   끄려면 창을 닫으세요
echo.
echo   Amazon, Woot, eBay      15분마다
echo   Nordstrom Rack, Zappos  30분마다
echo   그 외                   2~6시간마다
echo.
echo   FLASH 딜은 즉시 알림
echo   나머지는 08~10시, 13~16시, 20~23시에 알림
echo ============================================
echo.
python scheduler.py
echo.
echo 감시가 중단되었습니다
pause
