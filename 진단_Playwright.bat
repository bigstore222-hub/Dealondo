@echo off
title Playwright 진단
cd /d "%~dp0scraper"
echo.
echo ============================================
echo   Playwright 설치 문제 진단
echo   결과를 전부 복사해서 클로드에게 주세요
echo ============================================
echo.
echo --- 1. 파이썬 위치와 버전 ---
python -c "import sys; print(sys.executable); print(sys.version)"
echo.
echo --- 2. Playwright 드라이버 위치 ---
python -c "import playwright, os; p=os.path.dirname(playwright.__file__); print(p); print(os.listdir(os.path.join(p,'driver')))"
echo.
echo --- 3. 드라이버 node.exe 직접 실행 ---
python -c "import playwright, os; d=os.path.join(os.path.dirname(playwright.__file__),'driver','node.exe'); print('경로:', d); print('존재:', os.path.exists(d)); print('크기:', os.path.getsize(d) if os.path.exists(d) else 0)"
echo.
echo --- 4. node.exe 실행 테스트 ---
for /f "delims=" %%i in ('python -c "import playwright,os;print(os.path.join(os.path.dirname(playwright.__file__),'driver','node.exe'))"') do set NODEEXE=%%i
"%NODEEXE%" --version
echo    (위에 v20 같은 버전이 나오면 정상)
echo.
echo --- 5. 상세 오류 로그 ---
set DEBUG=pw:install
python -m playwright install chromium
echo.
echo ============================================
echo   진단 끝. 위 내용을 전부 복사해 주세요
echo ============================================
echo.
pause
