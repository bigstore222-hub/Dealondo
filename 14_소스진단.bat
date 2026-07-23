@echo off
title Dealondo - Source Diagnosis
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

python diag_sources.py

echo.
echo ============================================
echo   알림 이력을 초기화할까요?
echo.
echo   개발/테스트로 쌓인 이력 때문에 새 딜이
echo   계속 걸러진다면 초기화하세요.
echo   (가격 이력은 보존됩니다)
echo ============================================
echo.
set /p ANS="초기화하려면 y 입력 후 엔터 [y/N]: "
if /I "%ANS%"=="y" (
  python diag_sources.py --reset
)
echo.
pause
