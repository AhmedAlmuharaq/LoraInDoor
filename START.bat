@echo off
SET RCONDA=C:\Users\VICTUS\radioconda\python.exe
SET DIR=D:\UFR STGI\second Semester\final project\InDoorLora

echo Starting InDoorLora...
echo.

:: Kill anything on port 5556
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5556 ^| findstr LISTENING') do (
    echo Killing old process on port 5556 (PID %%a)...
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: Server (system python - no UHD needed)
echo [1] Starting Positioning Server...
start "InDoorLora Server" cmd /k "cd /d "%DIR%" && python positioning_server.py"
timeout /t 3 /nobreak >nul

:: USRP Bridge (RadioConda python - has UHD)
echo [2] Starting USRP Bridge...
start "USRP Bridge" cmd /k "cd /d "%DIR%" && %RCONDA% usrp_lora_bridge.py --debug"

echo.
echo Both windows started.
echo Open InDoorLora_Dashboard.html in your browser.
echo.
pause
