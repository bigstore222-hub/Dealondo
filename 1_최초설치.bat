@echo off
title Hotdeal Radar - Setup
cd /d "%~dp0scraper"
echo.
echo ============================================
echo   핫딜 레이더 최초 설치
echo   처음 한 번만 실행하면 됩니다
echo ============================================
echo.
echo [1/3] 파이썬 확인 중...
python --version
if errorlevel 1 goto NOPY
echo.
echo [2/3] Playwright 설치 중... 1~2분 걸립니다
python -m pip install playwright
if errorlevel 1 goto FAIL
echo.
echo [3/3] 브라우저 다운로드 중... 약 120MB, 2~3분
python -m playwright install chromium
if errorlevel 1 goto FAIL
echo.
echo ============================================
echo   설치가 모두 끝났습니다
echo   다음은 2_테스트실행.bat 을 실행하세요
echo ============================================
echo.
pause
exit /b 0
:NOPY
echo.
echo ============================================
echo   파이썬이 설치되어 있지 않습니다
echo.
echo   1. python.org/downloads 접속
echo   2. Download Python 버튼 클릭
echo   3. 설치 파일 실행
echo.
echo   중요: 설치 첫 화면 맨 아래
echo   Add python.exe to PATH 를 체크하세요
echo.
echo   설치 후 이 파일을 다시 실행하세요
echo ============================================
echo.
pause
exit /b 1
:FAIL
echo.
echo   설치에 실패했습니다
echo   위 메시지를 클로드에게 보여주세요
echo.
pause
exit /b 1
