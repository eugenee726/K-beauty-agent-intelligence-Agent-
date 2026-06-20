@echo off
REM K-Beauty weekly full pipeline (Thursday 09:00)
REM Called by Windows Task Scheduler.

cd /d "%~dp0"

set "LOGFILE=%~dp0logs\weekly_%date:~0,4%%date:~5,2%%date:~8,2%.log"

echo ============================================ >> "%LOGFILE%" 2>&1
echo [%date% %time%] weekly pipeline start >> "%LOGFILE%" 2>&1

"%~dp0venv\Scripts\python.exe" main.py agentic >> "%LOGFILE%" 2>&1

echo [%date% %time%] weekly pipeline end (exit=%ERRORLEVEL%) >> "%LOGFILE%" 2>&1
