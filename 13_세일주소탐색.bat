@echo off
title Dealondo - Discover Sale URLs
cd /d "%~dp0scraper"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set LOG=%~dp0세일주소탐색결과.txt

echo.
echo ============================================
echo   세일 페이지 주소 탐색
echo.
echo   각 사이트 홈페이지에서 실제 세일 링크를
echo   찾아내 검증합니다
echo ============================================
echo.
echo 탐색 중... 5~15분 걸립니다. 창을 닫지 마세요
echo.

python discover_sale_urls.py T2,T3

echo.
echo ============================================
echo   완료. 결과가 아래 파일에 저장되었습니다
echo     data\sale_urls.csv
echo ============================================
echo.
pause
