@echo off
chcp 65001 >nul
rem せどりDESK 在庫チェック bot をこのフォルダで起動する（Windows 用）。ダブルクリックで実行。
rem 初回は仮想環境を作って依存を入れ、.env が無ければ .env.example から作ってメモ帳で開く。
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py

if not exist .venv (
  echo [1/3] Python の仮想環境を作成しています...
  %PY% -m venv .venv || (echo Python が見つかりません。python.org から 3.10 以上を入れて「Add python.exe to PATH」にチェックしてください & pause & exit /b 1)
)
call .venv\Scripts\activate.bat

echo [2/3] 必要なライブラリを確認しています...
pip install -q -r requirements.txt || (echo ライブラリの取得に失敗しました & pause & exit /b 1)

if not exist .env (
  copy .env.example .env >nul
  echo .env を作りました。開いたメモ帳の DISCORD_TOKEN= の後ろに bot のトークンを貼って保存し、この start.bat をもう一度実行してください。
  start notepad .env
  pause
  exit /b 0
)

echo [3/3] bot を起動します。止めるときはこのウィンドウを閉じてください。
python -m bot.main
pause
