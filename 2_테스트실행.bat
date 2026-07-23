@echo off
title Dealondo - Test
cd /d "%~dp0scraper"
set RADAR_SCROLL_WAIT_MS=700
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set RADAR_LOG=%~dp0실행결과.txt

echo Dealondo - Test Result > "%RADAR_LOG%"
echo Run at: %date% %time% >> "%RADAR_LOG%"

echo.
echo ============================================
echo   딜온도 테스트 실행 (T2 + T3, 즉시 발송)
echo.
echo   아래에 진행 상황이 한 줄씩 나옵니다.
echo   사이트가 많아 5~15분 걸립니다. 창을 닫지 마세요.
echo ============================================
echo.

python watchlist.py
echo.
python scheduler.py --once --force-send --tier T2,T3

echo.
echo ============================================
echo   완료. 결과는 실행결과.txt 에도 저장됐습니다.
echo ============================================
echo.
pause
