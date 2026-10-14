#!/usr/bin/env python3
"""ビックカメラの商品一覧を巡回して「生産終了」系の商品を拾う。

使い方:
  python bic_eol.py URL [URL ...]
  python bic_eol.py -f urls.txt
  python bic_eol.py -f urls.txt --browser      # ブロックされる場合 (Playwright 使用)

URL はブラウザで開いたカテゴリ一覧や検索結果ページのアドレスをそのまま貼る。
結果は CSV に出力し、前回までに見つけた商品は seen.json で覚えて「新規」列で区別する。
"""
import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# 拾う表示。長いものから順に判定して、どの表示だったかを記録する
KEYWORDS = ["メーカー生産終了", "生産終了品", "生産終了", "販売終了", "メーカー完売", "在庫限り"]
DEFAULT_KEYWORDS = ["メーカー生産終了", "生産終了品", "生産終了", "販売終了"]

ITEM_RE = re.compile(r"/bc/item/(\d+)")
PRICE_RE = re.compile(r"([\d,]{3,})\s*円")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")


# ---------- 取得 ----------

class RequestsFetcher:
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        })

    def get(self, url):
        r = self.s.get(url, timeout=30)
        r.raise_for_status()
        # Shift_JIS のページもあるので、ヘッダに charset が無ければ中身から推定
        if "charset" not in r.headers.get("Content-Type", "").lower():
            r.encoding = r.apparent_encoding
        return r.text

    def close(self):
        self.s.close()


class BrowserFetcher:
    """requests が弾かれるとき用。pip install playwright && playwright install chromium"""

    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=headless)
        self.page = self.browser.new_page(user_agent=UA, locale="ja-JP")

    def get(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(1500)
        return self.page.content()

    def close(self):
        self.browser.close()
        self.pw.stop()


# ---------- 解析 ----------

def item_block(a):
    """商品リンクから、その商品1件分を包む要素までさかのぼる。
    親に別の商品が入ってきたら一つ手前で止める。"""
    item_id = ITEM_RE.search(a["href"]).group(1)
    block = a
    for parent in a.parents:
        if parent.name in ("body", "html", "[document]"):
            break
        ids = {m.group(1) for x in parent.find_all("a", href=True)
               if (m := ITEM_RE.search(x["href"]))}
        if ids - {item_id}:
            break
        block = parent
    return block


def find_keyword(text, keywords):
    for k in sorted(keywords, key=len, reverse=True):
        if k in text:
            return k
    return None


def parse_list(html, base_url, keywords):
    """一覧ページから (全商品数, 該当商品リスト, 次ページURL) を返す"""
    soup = BeautifulSoup(html, "html.parser")
    links = {}
    for a in soup.find_all("a", href=True):
        m = ITEM_RE.search(a["href"])
        if m:
            links.setdefault(m.group(1), []).append(a)

    hits = []
    for item_id, anchors in links.items():
        block = item_block(anchors[0])
        text = " ".join(block.get_text(" ", strip=True).split())
        kw = find_keyword(text, keywords)
        if not kw:
            continue
        names = [x.get_text(" ", strip=True) or x.get("title", "") for x in anchors]
        img = block.find("img", alt=True)
        if img:
            names.append(img["alt"])
        name = max(names, key=len, default="")
        price = PRICE_RE.search(text)
        hits.append({
            "item_id": item_id,
            "name": name,
            "price": price.group(1).replace(",", "") if price else "",
            "status": kw,
            "url": f"https://www.biccamera.com/bc/item/{item_id}/",
        })
    return len(links), hits, next_page(soup, base_url)


def next_page(soup, base_url):
    a = soup.find("a", rel="next")
    if not a:
        for x in soup.find_all("a", href=True):
            t = x.get_text(strip=True)
            if t in ("次へ", "次のページ", "次へ>", "次へ >", ">", "›", "»") or "次へ" in (x.get("title") or ""):
                a = x
                break
    if not a or a["href"].startswith(("#", "javascript")):
        return None
    return urljoin(base_url, a["href"])


# ---------- 実行 ----------

def crawl(fetcher, start_url, keywords, max_pages, delay, save_html_dir):
    url, page, seen_urls = start_url, 0, set()
    while url and page < max_pages and url not in seen_urls:
        seen_urls.add(url)
        page += 1
        try:
            html = fetcher.get(url)
        except Exception as e:
            print(f"  ! 取得失敗 {url}: {e}", file=sys.stderr)
            return
        if save_html_dir:
            p = Path(save_html_dir) / f"{urlparse(start_url).path.strip('/').replace('/', '_')}_{page}.html"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(html, encoding="utf-8")
        total, hits, nxt = parse_list(html, url, keywords)
        print(f"  p{page}: 商品 {total} 件 / 該当 {len(hits)} 件", file=sys.stderr)
        if total == 0:
            print("  ! 商品が見つかりません。ブロックされているか、ページの形が想定と違います。"
                  " --browser や --save-html を試してください", file=sys.stderr)
        for h in hits:
            h["source"] = start_url
            yield h
        url = nxt
        if url:
            time.sleep(delay + random.uniform(0, delay / 2))


def main():
    ap = argparse.ArgumentParser(description="ビックカメラの生産終了品を一覧ページから拾う")
    ap.add_argument("urls", nargs="*", help="カテゴリ一覧・検索結果ページのURL")
    ap.add_argument("-f", "--file", help="URLを1行1件で書いたファイル (#以降はコメント)")
    ap.add_argument("-o", "--out", help="出力CSV (既定: bic_eol_YYYYmmdd_HHMM.csv)")
    ap.add_argument("--seen", default="seen.json", help="既出商品の記録ファイル")
    ap.add_argument("--max-pages", type=int, default=20, help="1URLあたりの最大ページ数")
    ap.add_argument("--delay", type=float, default=3.0, help="ページ間の待ち秒数")
    ap.add_argument("--with-stock", action="store_true", help="「在庫限り」「メーカー完売」も拾う")
    ap.add_argument("--browser", action="store_true", help="Playwrightのブラウザで取得する")
    ap.add_argument("--show", action="store_true", help="--browser時にブラウザ画面を表示")
    ap.add_argument("--save-html", metavar="DIR", help="取得したHTMLを保存 (調査用)")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                urls.append(line)
    if not urls:
        ap.error("URLを指定してください")

    keywords = KEYWORDS if args.with_stock else DEFAULT_KEYWORDS
    seen_path = Path(args.seen)
    seen = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    now = datetime.now()
    out = args.out or f"bic_eol_{now:%Y%m%d_%H%M}.csv"

    fetcher = BrowserFetcher(headless=not args.show) if args.browser else RequestsFetcher()
    rows = {}
    try:
        for i, u in enumerate(urls):
            print(f"[{i + 1}/{len(urls)}] {u}", file=sys.stderr)
            for h in crawl(fetcher, u, keywords, args.max_pages, args.delay, args.save_html):
                rows.setdefault(h["item_id"], h)
            if i + 1 < len(urls):
                time.sleep(args.delay)
    finally:
        fetcher.close()

    stamp = now.strftime("%Y-%m-%d %H:%M")
    new_count = 0
    for h in rows.values():
        h["new"] = "新規" if h["item_id"] not in seen else ""
        new_count += bool(h["new"])
        h["first_seen"] = seen.setdefault(h["item_id"], stamp)

    cols = ["new", "status", "name", "price", "url", "item_id", "first_seen", "source"]
    # Excelで文字化けしないよう BOM付きUTF-8
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: (r["new"] == "", r["status"], r["name"])))
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n該当 {len(rows)} 件 (新規 {new_count} 件) → {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
