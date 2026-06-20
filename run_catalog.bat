@echo off
REM K-Beauty monthly catalog rebuild (1st day 03:00)
REM Called by Windows Task Scheduler.

cd /d "%~dp0"

set "LOGFILE=%~dp0logs\catalog_%date:~0,4%%date:~5,2%%date:~8,2%.log"

echo ============================================ >> "%LOGFILE%" 2>&1
echo [%date% %time%] catalog rebuild start >> "%LOGFILE%" 2>&1

"%~dp0venv\Scripts\python.exe" main.py build-catalog >> "%LOGFILE%" 2>&1

echo [%date% %time%] catalog rebuild end (exit=%ERRORLEVEL%) >> "%LOGFILE%" 2>&1
