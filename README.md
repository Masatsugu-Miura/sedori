# せどりDESK — 本の店舗在庫チェック Discord bot

Discord に **ASIN か JAN（ISBN）** を貼ると、登録した書店チェーンの店舗在庫をまとめて調べて返す bot です。
`index.html` は既存のブラウザ版「せどりDESK」（串刺し検索ランチャー）、`bot/` が今回追加した Discord bot です。

```
/zaiko code:9784101010014              ← 地元（愛知・京都）＝既定
/zaiko code:9784101010014 area:全国    ← 全国
/local code:9784101010014              ← 地元（愛知・京都）だけ
/zaiko code:9784101010014 area:愛知    ← 地域を指定（全国 / 愛知 / 京都 / 地元 / 新宿 などの地名）
/zaiko code:B0XXXXXXXX stores:kinokuniya,maruzenjunkudo
!zaiko 9784101010014                   ← テキストコマンド（地元）
!zaiko 9784101010014 全国              ← テキストコマンド（全国）
!local 9784101010014                   ← テキストコマンド（地元）
9784101010014            ← コードや Amazon URL だけのメッセージにも自動で反応（地元）
9784101010014 全国       ← 後ろに「全国」を付けると全国、地域名を付けるとその地域で絞り込み
```

### 全国検索と地元検索
- 地域を指定しなければ **地元**（愛知・京都）を検索します。環境変数 `DEFAULT_SCOPE=all` で既定を全国にできます。
- **全国**（`area:全国`、`!zaiko <code> 全国`、`<code> 全国`）: 全チェーンの結果をチェーンごとに並べます。
- **地元**（`/local`、`area:地元`、指定なし）: `stores.json` の `home_regions`（初期値は愛知・京都）に展開される地名
  （名古屋・豊橋・岡崎・四条・宇治 …）を店名に含む店舗だけを表示します。該当店舗が無いチェーンや地域外のチェーンは
  末尾の 1 フィールドにリンクだけまとめます。地元の店（三洋堂・大垣書店など、`prefectures` に愛知・京都がある店）は
  リンクのみでも主要欄に出ます。
- 地名の対応表は `stores.json` の `regions` にあり、自由に追加できます（例: `"大阪": ["大阪", "梅田", "難波", …]`）。
- コードの後ろに地域でない言葉（例「在庫ある？」）が付いていても、地域指定なしとして既定の範囲で検索します。

返信は 1 本のメッセージ（Embed）です。先頭に書誌・在庫あり店舗の合計・Keepa の価格推移グラフ（本の ASIN が分かるとき）、
続けてチェーンごとに
🟢在庫あり / 🟡在庫わずか / 🔴在庫なし / ⚪要確認（ページは取れたが在庫表記を読めず） / 🔗リンクのみ / ⚠️取得失敗
のフィールドが並びます。各フィールドは `🟢n 🟡n 🔴n（確認 N店）` の件数行、**在庫あり・わずかの店だけ**の行
（`🟢 店名（県・市） ×在庫数`、在庫なしの店は件数のみ）、サイトで直接確認するリンク、の順で統一しています。
**自動取得に失敗してもリンクは必ず出る**ので、手でワンタップ確認できます。

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
Windows は `start.bat` をダブルクリック、Mac / Linux は `bash start.sh`。初回は仮想環境を作り、`.env` が無ければ作って止まるので、
`DISCORD_TOKEN` を書いてもう一度実行します。手でやる場合は:
```bash
pip install -r requirements.txt
cp .env.example .env     # DISCORD_TOKEN を書く。GUILD_ID を入れるとスラッシュコマンドが即反映
python -m bot.main
```

### bot を立てる前に結果を見たいとき（Webhook テスト）
Discord のチャンネル設定 → 連携サービス → ウェブフックで URL を作り、`.env` に `DISCORD_WEBHOOK_URL=` として書いてから

```bash
python scripts/webhook_test.py 9784101010014          # 地元（愛知・京都）の結果を Webhook に投稿
python scripts/webhook_test.py 9784101010014 全国     # 全国
python scripts/webhook_test.py 9784101010014 --dry    # 投稿せず画面に出すだけ
```
bot のトークンやサーバー招待なしで、実際の検索結果と各書店の読み取り状況を確認できます。

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
| 紀伊國屋書店 | 「店の在庫を確認」ページに店ごとに問い合わせ | 実サイトで確認済み。地元は該当店のみ、全国は全72店（約20秒） |
| 丸善ジュンク堂書店 | 商品ページが使う店舗在庫 API | 実サイトで確認済み（104店） |
| 三洋堂書店（愛知） | ISBN 検索 → 商品ページの全店在庫表 | 実サイトで確認済み |
| 三省堂書店 | 店舗在庫一覧ページ（BookStockList） | 実サイトで確認済み |
| 有隣堂 | 検索サイトの API（独自エンコードを復号） | 実サイトで確認済み（36店） |
| 未来屋書店（イオン） | 都道府県ごとの店舗一覧 API ＋ 在庫 API | 実サイトで確認済み。全国は47都道府県ぶん |
| TSUTAYA / 蔦屋書店 | 「在庫のある店舗を探す」を住所キーワードで | 実サイトで確認済み。全国は約33ページ（約50秒、上限あり） |
| ブックファースト | 店ごとに在庫を問い合わせ（22店） | 実サイトで確認済み |
| アニメイト | 店舗在庫 API（サイトの JS にある公開キー） | 実サイトで確認済み。一般書は在庫データに無いことが多い |
| くまざわ書店 | 商品ページの店舗在庫表を解析 | **未検証**（作成環境から接続不可。日本国内からは開ける見込み）。いけだ書店も同じ検索の対象。全店の在庫は下記の書店在庫情報プロジェクト経由でも取れる |
| 書店在庫情報プロジェクト（版元ドットコム） | 近隣書店 API ＋ カーリル蔵書検索 API（openBS） | 実サイトで確認済み。くまざわ・いけだ・大垣・ブックファースト・豊川堂・TOUTEN・NAgoya Book Center・トーハン系（らくだ・カルコス等）。日販系（BOOKSえみたす等）は 2026-10 時点で全店エラー |
| 大垣書店（京都） | リンク（在庫検索サイトへ） | 在庫検索サイトが海外からの接続を拒否するため自動取得なし |
| BOOKOFF | リンク | 店舗在庫はサイト上に出ない（常に0店） |
| 宮脇書店 | リンク（在庫検索システムへ） | コードは手入力 |
| Honya Club、e-hon | リンク | 店舗在庫はログイン／My書店設定が必要 |
| 書籍横断検索システム | リンク | ネット書店の横断確認用 |
| 喜久屋書店、文教堂、ヴィレッジヴァンガード、本の王国、正文館書店、夢屋書店 | リンク | 店舗在庫はネットで確認不可のため `enabled:false` |
| BOOKSえみたす、カルコス、豊川堂 | リンク | 店舗在庫は書店在庫情報プロジェクト側に出るため `enabled:false`（えみたすは日販系のエラーが直れば出る） |
| Amazon / Keepa | リンク | 相場確認用 |

ほんらぶ（日販）・本コレ（CCC）はアプリ専用で Web の在庫画面が無いため対象外です（ほんらぶの在庫データは書店在庫情報プロジェクトに日販系として連携されています）。

> **注意**: 各社サイトの構造は変わることがあるので、読めなくなった店は ⚪要確認＋リンクで返します。
> 合わない店があれば `stores.json` の `search` / `checker` と `bot/stores/` の該当チェッカーを調整してください。
> 短時間に大量のコードを連投すると書店サイト側にブロックされることがあります（同一サイトへの同時接続は 4 に制限しています）。

## 動作の細かい話

- 同じコード＋同じ絞り込みの再検索は 5 分キャッシュします（`CACHE_TTL_SECONDS`）。同時に走る検索は 2 件まで（`MAX_PARALLEL_SEARCHES`）、1 検索あたり同一サイトへの同時接続は 4 まで（全国検索は 1 件あたり 1 分弱かかることがあります）。
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
