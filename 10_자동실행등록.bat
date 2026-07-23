@echo off
title Hotdeal Radar - Register Auto Run
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [!] 관리자 권한이 필요합니다
  echo.
  echo   이 파일을 마우스 오른쪽 클릭 후
  echo   "관리자 권한으로 실행"을 선택해 주세요
  echo.
  pause
  exit /b 1
)

set DIR=%~dp0scraper
echo.
echo ============================================
echo   자동 실행 등록
echo ============================================
echo.
echo   창을 띄워두지 않아도 백그라운드로 돌아갑니다.
echo   PC를 껐다 켜도 자동으로 재개됩니다.
echo.

echo [1/4] Amazon, Woot, eBay 등록 (15분마다)
schtasks /create /tn "HotdealRadar_T1" /tr "cmd /c cd /d \"%DIR%\" && set PYTHONUTF8=1&& set RADAR_SCROLL_WAIT_MS=700&& python scheduler.py --once --tier T1" /sc minute /mo 15 /f /rl highest >nul
if errorlevel 1 (echo    실패) else (echo    완료)

echo [2/4] Nordstrom Rack, Zappos 등 등록 (30분마다)
schtasks /create /tn "HotdealRadar_T2" /tr "cmd /c cd /d \"%DIR%\" && set PYTHONUTF8=1&& set RADAR_SCROLL_WAIT_MS=700&& python scheduler.py --once --tier T2" /sc minute /mo 30 /f /rl highest >nul
if errorlevel 1 (echo    실패) else (echo    완료)

echo [3/4] 중간 우선순위 사이트 등록 (2시간마다)
schtasks /create /tn "HotdealRadar_T3" /tr "cmd /c cd /d \"%DIR%\" && set PYTHONUTF8=1&& python scheduler.py --once --tier T3" /sc hourly /mo 2 /f /rl highest >nul
if errorlevel 1 (echo    실패) else (echo    완료)

echo [4/4] 나머지 사이트 등록 (6시간마다)
schtasks /create /tn "HotdealRadar_T4" /tr "cmd /c cd /d \"%DIR%\" && set PYTHONUTF8=1&& python scheduler.py --once --tier T4" /sc hourly /mo 6 /f /rl highest >nul
if errorlevel 1 (echo    실패) else (echo    완료)

echo.
echo ============================================
echo   등록 완료
echo.
echo   이제 3_상시감시시작.bat 을 켜둘 필요가 없습니다.
echo   해제하려면 11_자동실행해제.bat 을 실행하세요.
echo.
echo   등록 상태 확인:
echo ============================================
schtasks /query /tn "HotdealRadar_T1" /fo list | findstr /i "TaskName Status Next"
echo.
pause
