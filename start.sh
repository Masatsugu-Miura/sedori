#!/usr/bin/env bash
# せどりDESK 在庫チェック bot をこのフォルダで起動する（Mac / Linux 用）: bash start.sh
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "[1/3] Python の仮想環境を作成しています..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "[2/3] 必要なライブラリを確認しています..."
pip install -q -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env を作りました。DISCORD_TOKEN= の後ろに bot のトークンを書いて保存し、もう一度 bash start.sh を実行してください。"
  exit 0
fi
echo "[3/3] bot を起動します。止めるときは Ctrl+C。"
exec python -m bot.main
