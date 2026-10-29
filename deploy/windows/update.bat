@echo off
rem Daily update for the AI backend host. Setup steps: docs/DEPLOY_WINDOWS.md
rem Scheduled at 04:30, after the 04:00 data update on GitHub has been committed.
rem - Course data needs no restart: the API reloads when a newer processed CSV appears.
rem - Code, dependencies or the prediction file do: restart_backend.bat stops it and
rem   run_backend.bat starts it again within 10 seconds.
rem Keep this file ASCII-only: cmd.exe parses batch files with the ANSI codepage, not UTF-8.

cd /d "%~dp0..\.."
if not exist logs mkdir logs
call :main >> logs\update.log 2>&1
exit /b %errorlevel%

:main
for /f %%i in ('git rev-parse HEAD') do set OLD=%%i
git pull --ff-only
if errorlevel 1 (
    echo [%date% %time%] git pull failed
    exit /b 1
)

git diff --quiet %OLD% HEAD -- requirements.txt requirements-ai.txt
if errorlevel 1 ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt -r requirements-ai.txt

git diff --quiet %OLD% HEAD -- src config main.py data/models requirements.txt requirements-ai.txt
if not errorlevel 1 (
    echo [%date% %time%] data only, no restart needed
    exit /b 0
)

call "%~dp0restart_backend.bat"
