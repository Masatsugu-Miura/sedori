@echo off
rem Launcher for the stock-check Discord bot (Windows). Double-click to run.
rem First run: creates a venv, installs deps, creates .env from .env.example and opens it in Notepad.
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py

if not exist .venv (
  echo [1/3] Creating Python virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Python not found. Install Python 3.10+ from python.org and check "Add python.exe to PATH".
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat

echo [2/3] Installing required libraries...
pip install -q -r requirements.txt
if errorlevel 1 (
  echo Failed to install libraries. Check your internet connection.
  pause
  exit /b 1
)

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Created .env - Notepad will open it now.
  echo Paste your bot token after DISCORD_TOKEN=  then save, close Notepad, and run start.bat again.
  echo.
  start "" notepad .env
  pause
  exit /b 0
)

echo [3/3] Starting the bot. Keep this window open. Close it to stop the bot.
python -m bot.main
pause
