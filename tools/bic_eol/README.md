# ビックカメラ 生産終了ピッカー

ビックカメラのカテゴリ一覧・検索結果ページを巡回し、「メーカー生産終了」「生産終了品」「生産終了」「販売終了」の表示がある商品を CSV に書き出します。

## 準備（初回だけ）

```
pip install -r requirements.txt
```

## 使い方

1. ブラウザでビックカメラの見たいカテゴリや検索結果を開き、アドレスを `urls.txt` に1行ずつ貼る
2. 実行

```
python bic_eol.py -f urls.txt
```

`bic_eol_日付.csv` ができます（Excel でそのまま開けます）。

- 次のページは「次へ」リンクをたどって自動で巡回します（1URLあたり最大20ページ。`--max-pages` で変更）
- 前回までに見つけた商品は `seen.json` に記録され、新しく出てきた商品は「新規」列に印が付きます
- 「在庫限り」「メーカー完売」も拾うには `--with-stock` を付けます
- アクセス間隔は既定で約3秒です（`--delay`）。お店のサーバーに負担をかけないよう、短くしすぎないでください

## うまく取れないとき

「商品が見つかりません」と出る場合は、アクセスがブロックされているか、ページの作りが想定と違っています。

- ブラウザ経由で取得: `pip install playwright` → `playwright install chromium` → `python bic_eol.py -f urls.txt --browser`
- 調査用に HTML を保存: `python bic_eol.py URL --save-html html` で保存された HTML を共有してもらえれば、解析部分を直せます
