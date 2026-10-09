@echo off
rem One-click setup (Windows). Double-click this file.
rem  1) moves this whole folder INTO the existing Kaitori Scanner folder (Japanese name) as  <that folder>\sedori
rem  2) rebuilds the Python venv there (needed after a move)
rem  3) puts start / stop / folder buttons on the Desktop
rem  4) fixes the sign-in autostart shortcut if you registered one
rem Optional: setup.bat "D:\somewhere\folder"  to choose the destination.
rem The work is done by install.ps1 in a new window so this folder can be moved while it runs.
start "" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Dest "%~1"
exit
