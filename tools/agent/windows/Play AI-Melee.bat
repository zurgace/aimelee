@echo off
REM Starts melee.exe with Phillip playing port 2 (see README-AI-Melee.txt).
REM Extra arguments go to play.py, for example: "Play AI-Melee.bat" --quick
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.12 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer, then run this again.
    echo.
    pause
    exit /b 1
)

py -3 ai-melee\play.py %*
if errorlevel 1 (
    echo.
    echo AI-Melee stopped with an error; the message above says what is missing.
    pause
)
