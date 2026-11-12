@echo off
setlocal
title 콜센터일마감
cd /d "%~dp0"

set PORT=8517
set URL=http://localhost:%PORT%

echo.
echo   ==========================================
echo     Call Center Daily Close
echo   ==========================================
echo.

rem --- already running? just open the browser ---
netstat -ano | findstr /r /c:"LISTENING.*:%PORT% " >nul 2>&1
if not errorlevel 1 (
    echo   Already running. Opening browser...
    start "" "%URL%"
    ping -n 3 127.0.0.1 >nul 2>&1
    exit /b 0
)

rem --- python check ---
where python >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] python not found. Install Python or add it to PATH.
    pause
    exit /b 1
)

python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo   Installing required packages, please wait...
    python -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo   [ERROR] package install failed.
        pause
        exit /b 1
    )
)

rem --- open browser after a short delay ---
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 5; Start-Process '%URL%'"

echo   Starting server...  ( closing this window stops the app )
echo   %URL%
echo.

python -m streamlit run "app\streamlit_app.py" --server.port %PORT% --server.headless true --browser.gatherUsageStats false

echo.
echo   App stopped.
pause
