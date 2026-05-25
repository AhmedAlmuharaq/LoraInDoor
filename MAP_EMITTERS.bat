@echo off
SET RCONDA=C:\Users\VICTUS\radioconda\python.exe
SET DIR=D:\UFR STGI\second Semester\final project\InDoorLora

echo ============================================
echo  InDoorLora - Emitter Mapping Wizard
echo ============================================
echo.
echo Make sure positioning_server.py is running first!
echo.
pause

cd /d "%DIR%"
%RCONDA% find_emitter_map.py

echo.
echo Done! Use the --map parameter shown above.
pause
