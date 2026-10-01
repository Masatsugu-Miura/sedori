# せどりDESK — 本の店舗在庫チェック Discord bot

Discord に **ASIN か JAN（ISBN）** を貼ると、登録した書店チェーンの店舗在庫をまとめて調べて返す bot です。
`index.html` は既存のブラウザ版「せどりDESK」（串刺し検索ランチャー）、`bot/` が今回追加した Discord bot です。

```
/zaiko code:9784101010014              ← 全国
/local code:9784101010014              ← 地元（愛知・京都）だけ
/zaiko code:9784101010014 area:愛知    ← 地域を指定（愛知 / 京都 / 地元 / 新宿 などの地名）
/zaiko code:B0XXXXXXXX stores:kinokuniya,maruzenjunkudo
!zaiko 9784101010014                   ← テキストコマンド（全国）
!local 9784101010014                   ← テキストコマンド（地元）
9784101010014            ← コードや Amazon URL だけのメッセージにも自動で反応（全国）
9784101010014 地元       ← 後ろに「地元」や地域名を付けると絞り込み
```

### 全国検索と地元検索
- **全国**（`/zaiko`）: 全チェーンの結果をチェーンごとに並べます。
- **地元**（`/local`、`area:地元`）: `stores.json` の `home_regions`（初期値は愛知・京都）に展開される地名
  （名古屋・豊橋・岡崎・四条・宇治 …）を店名に含む店舗だけを表示します。該当店舗が無いチェーンや地域外のチェーンは
  末尾の 1 フィールドにリンクだけまとめます。地元の店（三洋堂・大垣書店など、`prefectures` に愛知・京都がある店）は
  リンクのみでも主要欄に出ます。
- 地名の対応表は `stores.json` の `regions` にあり、自由に追加できます（例: `"大阪": ["大阪", "梅田", "難波", …]`）。
- 環境変数 `AUTO_REPLY_SCOPE=local` にすると、コードだけ貼ったときの既定を地元検索にできます。

返信は 1 本のメッセージ（Embed）で、チェーンごとに
🟢在庫あり / 🟡在庫わずか / 🔴在庫なし / ⚪要確認（ページは取れたが在庫表記を読めず） / 🔗リンクのみ / ⚠️取得失敗
と各店舗の行、そしてサイトで直接確認するリンクが並びます。**自動取得に失敗してもリンクは必ず出る**ので、手でワンタップ確認できます。

## コードの扱い

| 入力 | 処理 |
|---|---|
| 10桁（本の ASIN = ISBN-10） | ISBN-13（JAN）に変換して全店検索 |
| 13桁 978/979… | ISBN-13 として検索。ISBN-10（ASIN）も逆算 |
| 13桁 491…（雑誌コード）/ 8桁 | JAN として検索（雑誌非対応の店は要確認に） |
| B0… の ASIN | Amazon 商品ページから ISBN/JAN を拾って検索（Amazon が拒否すると変換不可。JAN を直接入れてください） |
| Amazon の URL | `/dp/XXXX` から ASIN を抽出 |

タイトル・著者・出版社・書影は openBD（無料API）→ Google Books の順に取得します。

## セットアップ

### 1. Discord 側
1. https://discord.com/developers/applications → **New Application**
2. **Bot** タブ → **Reset Token** でトークンを控える
3. 同じ画面の **Privileged Gateway Intents** で **MESSAGE CONTENT INTENT** を ON（コードを貼るだけで反応させるため）
4. **OAuth2 → URL Generator** で Scopes `bot` `applications.commands`、Bot Permissions `Send Messages` `Embed Links` `Read Message History` を選び、生成URLでサーバーに招待

### 2. 起動（ローカル / VPS、Python 3.10 以上）
```bash
pip install -r requirements.txt
cp .env.example .env     # DISCORD_TOKEN を書く。GUILD_ID を入れるとスラッシュコマンドが即反映
python -m bot.main
```

### 3. 起動（Docker）
```bash
docker build -t zaikobot .
docker run -d --name zaikobot --env-file .env -v $(pwd)/stores.json:/app/stores.json zaikobot
```
Railway / Render / Fly.io などでも `Dockerfile` そのままで動きます（環境変数に `DISCORD_TOKEN`）。

## 書店の追加・編集（`stores.json`）

```json
{
  "id": "yurindo",
  "name": "有隣堂",
  "checker": "generic",
  "search": "https://www.yurindo.co.jp/store/search?q={isbn13}",
  "home": "https://www.yurindo.co.jp/",
  "enabled": true,
  "verified": false,
  "stores": ["横浜西口店", "藤沢店"],
  "note": "メモ"
}
```

| 項目 | 意味 |
|---|---|
| `search` | 検索URL。コード位置に `{isbn13}` `{isbn10}` `{jan}` `{asin}` `{code}` か `%s` |
| `search_alt` | `search` が埋められないコード種別のとき、または `search` が 404 のときの代替URL（検索トップなど） |
| `checker` | `kinokuniya` / `honto`（専用パーサ）、`generic`（ページを開いて「店名＋在庫表記」を汎用的に拾う）、`link`（リンクだけ出す） |
| `stores` | 店名の部分一致フィルタ。空なら全店。`/zaiko area:` で一時的な絞り込みも可 |
| `prefectures` | 出店地域。`["全国"]` か県名のリスト。地元検索で「地域外」に回すかどうかの判定に使う |
| `verified` | `false` にすると返信に「URL未検証」と注記が付く |
| `enabled` | `false` で検索対象から外す（`/togglestore id` で切替可） |

Discord からも追加できます：
- `/addstore name:〇〇書店 url:https://…?q=9784101010014` — その書店で実際に ISBN 検索した URL をそのまま貼れば、
  ISBN 部分を自動で `{isbn13}` に置き換えて登録します（`auto:True` で汎用解析、`stores:` で対象店舗を指定）
- `/stores` — 一覧、`/togglestore store_id:` — 有効/無効
- `search_alt` に代替URLを書くと、`search` が作れないコード（雑誌 JAN など）のときに使われます

### スプレッドシートの書店リストを取り込む
Google スプレッドシートを **ファイル → ダウンロード → CSV** で保存し、

```bash
python scripts/import_sheet.py 書店リスト.csv          # 追記
python scripts/import_sheet.py 書店リスト.csv --replace  # 置き換え
```

列は自動判定します（ヘッダーに「店名」「URL / 検索URL」「店舗 / 支店」「メモ」があれば優先）。
URL は `{isbn13}` 入りでも、実際に ISBN で検索したときの URL をそのまま貼ったものでも構いません（ISBN 部分を自動で置き換えます）。
URL が無い行はホームページだけのリンク店になります。`--auto` を付けると検索ページを開いて在庫表記を読む店として登録します。

## 収録チェーンと検証状況

スプレッドシートの一覧をもとにしています。

| チェーン | 方式 | 状況 |
|---|---|---|
| 紀伊國屋書店 | 商品ページの店舗在庫一覧を解析 | URL確認済み・パーサは実サイトでの最終確認待ち |
| 丸善ジュンク堂書店（maruzenjunkudo.co.jp） | 商品ページを解析、無ければ検索ページ | **検索URL未検証** |
| 三洋堂書店（愛知）、大垣書店（京都） | 検索ページを汎用解析 | **検索URL未検証**。地元検索で主要欄に出る |
| 三省堂、有隣堂、くまざわ、未来屋、TSUTAYA、ブックファースト、アニメイト | 検索ページを汎用解析 | **検索URL未検証**（ダメなら各社の検索トップにフォールバック） |
| 宮脇書店 | リンク（在庫検索システムへ） | コードは手入力 |
| BOOKOFF | 検索ページを汎用解析 | URL確認済み |
| Honya Club、e-hon | リンク | 店舗在庫はログイン／My書店設定が必要 |
| 版元ドットコム、書籍横断検索システム | リンク | ネット書店の横断確認用 |
| 喜久屋書店、文教堂、ヴィレッジヴァンガード | リンク | 店舗在庫はネットで確認不可のため `enabled:false` |
| Amazon / Keepa | リンク | 相場確認用 |

ほんらぶ・本コレはアプリ専用のため対象外です。

> **注意**: この bot を作った環境からは書店サイトへ接続できなかったため、スクレイピング部分（在庫表記の読み取り）は
> ローカルの疑似サイトでのテストのみです。各社サイトの構造は変わることがあるので、読めなかった店は ⚪要確認＋リンクで返します。
> 実際に1冊試して、合わない店があれば `stores.json` の `search` / `checker` を調整してください。
> 短時間に大量のコードを連投すると書店サイト側にブロックされることがあります（同時接続は 6 に制限しています）。

## 動作の細かい話

- 同じコード＋同じ絞り込みの再検索は 5 分キャッシュします（`CACHE_TTL_SECONDS`）。同時に走る検索は 2 件まで（`MAX_PARALLEL_SEARCHES`）、1 検索あたり書店サイトへの同時接続は 6 まで。
- 書店ページは UTF-8 / Shift_JIS / EUC-JP を自動判定して読みます。
- 返信が Discord の上限（Embed 10 個・6000 文字）を超える場合は複数メッセージに分けます。
- 店名の読み取りは「在庫表記の直前にある店名」を取るので、「ジュンク堂書店 池袋本店 在庫あり」は支店名まで保持されます。

## テスト
```bash
pip install -r requirements-dev.txt
pytest
```
コード判定・在庫表記の解析・Embed の分割・CSV 取り込み・疑似書店サイトでの一連の流れをテストしています。

## 構成
```
bot/main.py          Discord クライアント、スラッシュコマンド、自動反応
bot/codes.py         ASIN / JAN / ISBN 判定と変換
bot/lookup.py        openBD / Google Books 書誌取得、B0 ASIN → JAN 解決
bot/stores/          書店チェッカー（base, generic, kinokuniya, honto）と一括実行
bot/render.py        Discord Embed 生成
stores.json          書店チェーン設定
scripts/import_sheet.py  CSV（スプレッドシート）取り込み
tests/               pytest
```
