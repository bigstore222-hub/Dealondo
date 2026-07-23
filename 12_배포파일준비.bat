@echo off
title Dealondo - Prepare Publish Files
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set RADAR_SCROLL_WAIT_MS=700

echo.
echo ============================================
echo   딜온도 배포 파일 준비
echo.
echo   Amazon, Woot, eBay, Nordstrom Rack, Zappos
echo   전체에서 딜을 수집합니다
echo ============================================
echo.
echo 수집 중... 3~5분 걸립니다. 창을 닫지 마세요
echo.

python scheduler.py --once --tier T1,T2

echo.
echo 배포 폴더로 복사 중...
copy /Y "%~dp0web\deals.json" "%~dp0publish\deals.json" >nul
if errorlevel 1 (echo    복사 실패) else (echo    완료)

echo.
echo ============================================
echo   준비 완료
echo.
echo   publish 폴더의 두 파일을 GitHub에 올리세요
echo     - index.html
echo     - deals.json
echo ============================================
echo.
start "" "%~dp0publish"
pause
