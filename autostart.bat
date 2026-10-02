@echo off
rem Register start.bat to run automatically after you sign in to Windows (puts a shortcut in the Startup folder).
rem Run autostart.bat remove  to unregister.
cd /d "%~dp0"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\sedori-bot.lnk"
if /i "%~1"=="remove" (
  del "%LNK%" >nul 2>nul
  echo Removed. The bot will no longer start automatically.
  pause
  exit /b 0
)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%');$s.TargetPath='%~dp0start.bat';$s.WorkingDirectory='%~dp0';$s.Save()"
if exist "%LNK%" (
  echo Registered. The bot will start automatically after you sign in to Windows.
) else (
  echo Failed to create the shortcut.
)
pause
