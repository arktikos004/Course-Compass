@echo off
rem Restarts the AI backend: stops the process listening on API_PORT, and the loop in
rem run_backend.bat starts it again within 10 seconds (picking up new code, .env or index).
rem Use after editing .env or rebuilding the syllabus index; update.bat also calls it.
rem Keep this file ASCII-only: cmd.exe parses batch files with the ANSI codepage, not UTF-8.

setlocal
cd /d "%~dp0..\.."

rem API_PORT from .env, default 8000
set PORT=8000
if exist .env for /f "usebackq tokens=1,2 delims==" %%a in (".env") do if "%%a"=="API_PORT" set PORT=%%b

set FOUND=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    taskkill /pid %%p /f >nul 2>&1
    set FOUND=1
)
if defined FOUND (
    echo [%date% %time%] stopped backend on port %PORT%, run_backend.bat restarts it in 10 seconds
) else (
    echo [%date% %time%] nothing listening on port %PORT%; is the CourseCompass Backend task running?
)
