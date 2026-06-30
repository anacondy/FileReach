@echo off
REM ============================================================
REM   FileReach — Windows one-click launcher
REM   Double-click this file. It asks for permission ONCE,
REM   sets up its environment, and opens the search UI.
REM ============================================================
SETLOCAL
cd /d "%~dp0"

REM Prefer python, fall back to py launcher
where python >nul 2>nul
IF %ERRORLEVEL%==0 (
    python run.py
) ELSE (
    py run.py
)

REM If Python is missing, show a friendly message.
IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo ----------------------------------------------------------
    echo  Python was not found on this PC.
    echo  Install Python 3.10+ from https://www.python.org/downloads/
    echo  (tick "Add Python to PATH" during install), then re-run.
    echo ----------------------------------------------------------
    pause
)
ENDLOCAL
