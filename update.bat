@echo off
rem Update the bot to the latest version in one click. Keeps your .env, .venv and data folder.
rem After it finishes: close the bot window and run start.bat again.
cd /d "%~dp0"
set "URL=https://github.com/Masatsugu-Miura/sedori/archive/refs/heads/claude/discord-book-inventory-bot-tm2q4h.zip"

echo [1/3] Downloading the latest version...
curl -sSL -o update.zip "%URL%"
if errorlevel 1 (
  echo Download failed. Check your internet connection.
  pause
  exit /b 1
)

echo [2/3] Extracting...
if exist _update rd /s /q _update
mkdir _update
tar -xf update.zip -C _update
if errorlevel 1 (
  echo Extract failed.
  pause
  exit /b 1
)
set "SRC="
for /d %%D in (_update\*) do set "SRC=%%D"
if not defined SRC (
  echo Nothing extracted.
  pause
  exit /b 1
)

echo [3/3] Copying new files over this folder (.env, .venv and data are kept)...
xcopy "%SRC%\*" "%~dp0" /E /Y /I /Q >nul
rd /s /q _update
del update.zip

echo.
echo Update complete.
echo Now close the bot window (the one that says "logged in as") and double-click start.bat again.
pause
