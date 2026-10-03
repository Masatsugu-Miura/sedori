@echo off
rem Register the bot to start automatically (silently, no window) after you sign in to Windows.
rem Run:  autostart.bat          -> register (uses start_hidden.vbs)
rem       autostart.bat window   -> register the visible version (start.bat) instead
rem       autostart.bat remove   -> unregister
cd /d "%~dp0"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\sedori-bot.lnk"
if /i "%~1"=="remove" (
  del "%LNK%" >nul 2>nul
  echo Removed. The bot will no longer start automatically.
  pause
  exit /b 0
)
set "TARGET=%~dp0start_hidden.vbs"
if /i "%~1"=="window" set "TARGET=%~dp0start.bat"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%');$s.TargetPath='%TARGET%';$s.WorkingDirectory='%~dp0';$s.Save()"
if exist "%LNK%" (
  echo Registered. The bot will start automatically after you sign in to Windows.
  if /i not "%~1"=="window" echo It runs silently with no window. Log: data\bot.log   Stop: stop.bat
) else (
  echo Failed to create the shortcut.
)
pause
