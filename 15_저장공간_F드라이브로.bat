@echo off
title Dealondo - 저장공간 F드라이브 이전
echo ============================================
echo   딜온도 저장공간을 F 드라이브로 옮깁니다
echo ============================================
echo.
echo [주의] 상시감시(3번)나 테스트(2번) 실행 창이 열려 있으면
echo        먼저 모두 닫은 뒤 계속하세요 (DB 잠금 방지).
echo.
pause

if not exist "F:\" (
  echo.
  echo   F 드라이브를 찾을 수 없습니다.
  echo   F 드라이브를 연결한 뒤 다시 실행하세요.
  echo.
  pause
  exit /b 1
)

echo.
echo [1/5] F:\dealondo 폴더 생성...
mkdir "F:\dealondo" 2>nul
mkdir "F:\dealondo\tmp" 2>nul

echo [2/5] 가격이력 DB 이동...
if exist "%LOCALAPPDATA%\hotdeal_radar\radar.db" (
  move /y "%LOCALAPPDATA%\hotdeal_radar\radar.db" "F:\dealondo\radar.db" >nul
  if exist "%LOCALAPPDATA%\hotdeal_radar\radar.db-journal" move /y "%LOCALAPPDATA%\hotdeal_radar\radar.db-journal" "F:\dealondo\" >nul
  echo    DB 이동 완료
) else (
  echo    기존 DB 없음 - 새로 F:에 생성됩니다
)

echo [3/5] Playwright 브라우저 이동... 수십 초 걸립니다
if exist "%LOCALAPPDATA%\ms-playwright" (
  robocopy "%LOCALAPPDATA%\ms-playwright" "F:\dealondo\ms-playwright" /move /e /nfl /ndl /njh /njs /nc /ns >nul
  echo    브라우저 이동 완료
) else (
  echo    브라우저 없음 - 1_최초설치.bat 다시 실행 시 F:에 설치됩니다
)

echo [4/5] C 드라이브에 남은 임시파일 정리...
for /d %%D in ("%TEMP%\playwright*" "%TEMP%\scoped_dir*" "%TEMP%\.org.chromium.*") do rmdir /s /q "%%D" 2>nul
del /q "%TEMP%\playwright*" 2>nul

echo [5/5] 환경변수 등록 - 앞으로 F: 사용...
setx RADAR_DB "F:\dealondo\radar.db" >nul
setx RADAR_TMP "F:\dealondo\tmp" >nul
setx PLAYWRIGHT_BROWSERS_PATH "F:\dealondo\ms-playwright" >nul

echo.
echo ============================================
echo   완료! 앞으로 모든 기록이 F:\dealondo 에 저장됩니다.
echo.
echo   [중요] 지금 열려 있는 모든 창(cmd)을 닫고
echo          프로그램을 새로 실행하세요.
echo          환경변수는 새로 연 창부터 적용됩니다.
echo ============================================
echo.
pause
