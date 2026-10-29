@echo off
rem Course Compass AI backend for the public site. Setup steps: docs/DEPLOY_WINDOWS.md
rem Runs "python main.py api" and starts it again whenever it exits (a crash, or update.bat
rem stopping it to load new code). API key, shared secret and port are read from .env.
rem Keep this file ASCII-only: cmd.exe parses batch files with the ANSI codepage, not UTF-8.

cd /d "%~dp0..\.."
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if not exist logs mkdir logs

:loop
".venv\Scripts\python.exe" main.py api
echo [%date% %time%] backend exited with code %errorlevel%, restarting in 10 seconds >> logs\backend_restarts.log
timeout /t 10 /nobreak >nul
goto loop
