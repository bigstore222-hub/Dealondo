@echo off
title Hotdeal Radar - Unregister
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [!] 관리자 권한이 필요합니다
  echo   마우스 오른쪽 클릭 후 "관리자 권한으로 실행"
  echo.
  pause
  exit /b 1
)
echo.
echo 자동 실행을 해제합니다...
echo.
for %%T in (T1 T2 T3 T4) do schtasks /delete /tn "HotdealRadar_%%T" /f >nul 2>&1
echo 해제 완료. 이제 자동으로 돌지 않습니다.
echo.
pause
