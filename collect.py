# BOOTH の VRChat タグの「新着」と「セールの言葉で検索した結果」を読んで、
# 商品と値段を Cloudflare D1 に記録する（計画の ①）
#
#   python collect.py            いつもの実行（前回から config の collect.hours 経っていなければ何もしない）
#   python collect.py --force    時間に関係なく実行
#   python collect.py --dry-run  BOOTH を少し読んで、読み取れた中身を表示するだけ（D1 には書かない）
#
# 読み方
#   1. 新着順の一覧（/ja/items?tags[]=VRChat&sort=new）を 1 ページずつ読み、
#      データベースにもう入っている商品が続けて出てきたら止める（前回の続きまで）
#   2. 「セール」「SALE」などの言葉で検索した一覧も数ページ読む（古い商品の値下げを拾うため）
#   3. 見かけた商品はすべて items に入れ（なければ追加、あれば最後に見た時刻を更新）、
#      値段が前回と違えば prices に 1 行足す
#   4. まだ詳しく読んでいない商品だけ、商品ごとの JSON で説明・いいね数・画像 URL を読む（1 回に上限あり）
import html
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
C = {"hours": 6, "new_max_pages": 5, "known_streak": 15, "sale_words": ["セール", "SALE", "OFF"],
     "sale_max_pages": 2, "details_per_run": 150, "page_wait": 2, "item_wait": 1}
C.update(CFG.get("collect", {}))
UA = {"User-Agent": "Mozilla/5.0 (compatible; vrc-dashboard-feed/1.0; +https://github.com/)"}
LIST_URL = "https://booth.pm/ja/items"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def now_min():
    return int(datetime.now(timezone.utc).timestamp() // 60)


def get(url):
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code == 200:
                return r
            log("HTTP", r.status_code, url)
            if r.status_code == 404:
                return r          # 消えた商品など。呼んだ側で見分ける
        except Exception as e:
            log("ERR", e, url)
        time.sleep(3 + i * 5)
    return None


# ───────────────────────── 値段・一覧の読み取り ─────────────────────────
def parse_price(s):
    """'¥ 500' / '500' / '¥ 200~' → (500, False) / (500, False) / (200, True)。読めなければ (None, False)"""
    s = html.unescape(str(s or ""))
    digits = re.sub(r"[^\d]", "", s.split("~")[0].split("〜")[0])
    return (int(digits) if digits else None), ("~" in s or "〜" in s)


def _text(pattern, block):
    m = re.search(pattern, block, re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def parse_cards(page):
    """一覧ページの商品カードを読む。返り値：[{id, name, price, varies, shop, shop_url, category, thumb}]"""
    starts = list(re.finditer(r"<li\b[^>]*data-product-id=\"(\d+)\"[^>]*>", page))
    out, seen = [], set()
    for i, m in enumerate(starts):
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        tag = m.group(0)
        end = starts[i + 1].start() if i + 1 < len(starts) else min(len(page), m.end() + 8000)
        block = page[m.end():end]

        def attr(name):
            a = re.search(name + r"=\"([^\"]*)\"", tag)
            return html.unescape(a.group(1)) if a else ""

        price_txt = _text(r"class=\"[^\"]*price[^\"]*\"[^>]*>(.*?)</", block)
        price, varies = parse_price(attr("data-product-price") or price_txt)
        varies = varies or "~" in price_txt
        shop_url = re.search(r"href=\"(https://[a-z0-9-]+\.booth\.pm/)\"", block)
        thumb = re.search(r"(?:data-original|src)=\"(https://booth\.pximg\.net/[^\"]+)\"", block)
        out.append({
            "id": pid,
            "name": attr("data-product-name") or _text(r"class=\"[^\"]*item-card__title[^\"]*\"[^>]*>(.*?)</a>", block),
            "price": price, "varies": varies,
            "shop": _text(r"class=\"[^\"]*item-card__shop-name[^\"]*\"[^>]*>(.*?)</", block),
            "shop_url": shop_url.group(1) if shop_url else "",
            "category": _text(r"class=\"[^\"]*item-card__category[^\"]*\"[^>]*>(.*?)</a>", block),
            "thumb": thumb.group(1) if thumb else "",
        })
    if not out:   # カードの形が変わっていた時：商品 ID だけ拾う（値段は商品 JSON で読む）
        for pid in dict.fromkeys(re.findall(r"/items/(\d+)", page)):
            out.append({"id": pid, "name": "", "price": None, "varies": False, "shop": "", "shop_url": "",
                        "category": "", "thumb": ""})
        if out:
            log("注意：商品カードの形が読めなかったので、商品 ID だけ拾いました")
    return out


def list_page(q="", page=1):
    params = [("tags[]", "VRChat"), ("sort", "new")]
    if q:
        params.append(("q", q))
    if page > 1:
        params.append(("page", page))
    r = get(LIST_URL + "?" + urllib.parse.urlencode(params))
    time.sleep(C["page_wait"])
    return parse_cards(r.text) if r is not None and r.status_code == 200 else []


def item_detail(pid):
    """商品ごとの JSON。返り値は dict。商品が消えていたら "gone"、一時的に読めなければ None（次の回にもう一度）"""
    r = get(f"https://booth.pm/ja/items/{pid}.json")
    time.sleep(C["item_wait"])
    if r is None:
        return None
    if r.status_code == 404:
        return "gone"
    try:
        j = r.json()
    except Exception as e:
        log("item json", pid, e)
        return None
    prices = [v.get("price") for v in (j.get("variations") or []) if isinstance(v.get("price"), (int, float))]
    price, varies = parse_price(j.get("price", ""))
    if prices:
        price, varies = int(min(prices)), varies or len(set(prices)) > 1
    imgs = j.get("images") or []
    return {
        "name": j.get("name") or "",
        "shop": (j.get("shop") or {}).get("name") or "",
        "shop_url": (j.get("shop") or {}).get("url") or "",
        "category": (j.get("category") or {}).get("name") or "",
        "thumb": (imgs[0].get("resized") or imgs[0].get("original") or "") if imgs else "",
        "likes": int(j.get("wish_lists_count") or 0),
        "descr": re.sub(r"\s+", " ", j.get("description") or "")[:400],
        "nimg": len(imgs),
        "adult": 1 if j.get("is_adult") else 0,
        "price": price, "varies": varies,
    }


# ───────────────────────── データベースに書く ─────────────────────────
def latest_prices(db, ids):
    rows = db.select_in(
        "SELECT p.item_id, p.price FROM prices p JOIN (SELECT item_id, MAX(seen_at) AS m FROM prices "
        "WHERE item_id IN ({}) GROUP BY item_id) x ON p.item_id = x.item_id AND p.seen_at = x.m", ids)
    return {r["item_id"]: r["price"] for r in rows}


def record_prices(db, pairs, t):
    """pairs = {id: 値段}。前回と違うものだけ prices に足す。足した件数と、下がった件数を返す"""
    pairs = {k: v for k, v in pairs.items() if v is not None}
    if not pairs:
        return 0, 0
    last = latest_prices(db, pairs.keys())
    rows, drops = [], 0
    for pid, p in pairs.items():
        if last.get(pid) != p:
            rows.append((pid, p, t))
            if pid in last and p < last[pid]:
                drops += 1
    db.insert_many("INSERT OR IGNORE INTO prices (item_id, price, seen_at)", 3, rows)
    return len(rows), drops


def collect(db, dry=False):
    t = now_min()
    seen = {}   # id → カード（新着を先に。同じ商品は最初に見たものを使う）

    # 1. 新着：もう入っている商品が known_streak 件続いたら止める
    streak = 0
    for page in range(1, C["new_max_pages"] + 1):
        cards = list_page(page=page)
        if not cards:
            break
        known = set() if dry else {r["id"] for r in db.select_in("SELECT id FROM items WHERE id IN ({})",
                                                                   [c["id"] for c in cards])}
        for c in cards:
            c["src"] = "new"
            seen.setdefault(c["id"], c)
            streak = streak + 1 if c["id"] in known else 0
        log(f"新着 {page} ページ目：{len(cards)} 件（うち既知 {len(known)} 件）")
        if streak >= C["known_streak"] or dry:
            break

    # 2. セールの言葉で検索
    for word in C["sale_words"]:
        for page in range(1, C["sale_max_pages"] + 1):
            cards = list_page(q=word, page=page)
            if not cards:
                break
            for c in cards:
                c["src"] = "sale"
                seen.setdefault(c["id"], c)
            log(f"「{word}」 {page} ページ目：{len(cards)} 件")
            if dry:
                break

    log(f"見かけた商品：{len(seen)} 件")
    if dry:
        for c in list(seen.values())[:5]:
            log(json.dumps(c, ensure_ascii=False))
        no_price = sum(1 for c in seen.values() if c["price"] is None)
        log(f"カードから値段を読めなかった商品：{no_price} 件")
        return

    # 3. items に入れる（あれば名前などと最後に見た時刻を更新）
    rows = [(c["id"], c["name"], c["shop"], c["shop_url"], c["category"], c["thumb"], int(c["varies"]),
             c["src"], t, t) for c in seen.values()]
    db.insert_many(
        "INSERT INTO items (id, name, shop, shop_url, category, thumb_url, price_varies, src, first_seen, last_seen)", 10,
        rows,
        "ON CONFLICT(id) DO UPDATE SET "
        "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE items.name END, "
        "shop = CASE WHEN excluded.shop != '' THEN excluded.shop ELSE items.shop END, "
        "thumb_url = CASE WHEN excluded.thumb_url != '' THEN excluded.thumb_url ELSE items.thumb_url END, "
        "price_varies = excluded.price_varies, last_seen = excluded.last_seen")
    added, drops = record_prices(db, {c["id"]: c["price"] for c in seen.values()}, t)
    log(f"値段の記録：{added} 件（値下げ {drops} 件）")

    # 4. まだ詳しく読んでいない商品（新しいものから）
    todo = db.q("SELECT id FROM items WHERE detail_at IS NULL ORDER BY first_seen DESC, id DESC LIMIT ?",
                [C["details_per_run"]])
    detail_prices = {}
    for r in todo:
        d = item_detail(r["id"])
        if d is None:
            continue                                                         # 一時的に読めない：次の回にもう一度
        if d == "gone":
            db.q("UPDATE items SET detail_at = ? WHERE id = ?", [t, r["id"]])   # 消えた商品はもう読まない
            continue
        db.q("UPDATE items SET name = CASE WHEN ? != '' THEN ? ELSE name END, shop = ?, shop_url = ?, category = ?, "
             "thumb_url = ?, likes = ?, descr = ?, nimg = ?, adult = ?, price_varies = ?, detail_at = ? WHERE id = ?",
             [d["name"], d["name"], d["shop"], d["shop_url"], d["category"], d["thumb"], d["likes"], d["descr"],
              d["nimg"], d["adult"], int(d["varies"]), t, r["id"]])
        if seen.get(r["id"], {}).get("price") is None:
            detail_prices[r["id"]] = d["price"]
    if detail_prices:
        record_prices(db, detail_prices, t)
    left = db.q("SELECT COUNT(*) AS n FROM items WHERE detail_at IS NULL")[0]["n"]
    log(f"詳しく読んだ商品：{len(todo)} 件（残り {left} 件は次の回）")


def main():
    dry = "--dry-run" in sys.argv
    if dry:
        collect(None, dry=True)
        return
    from db import D1
    db = D1()
    last = int(db.get_state("last_collect", "0") or 0)
    if "--force" not in sys.argv and now_min() - last < C["hours"] * 60 - 5:
        log(f"前回から {C['hours']} 時間たっていないので、今回は何もしません")
        return
    collect(db)
    db.set_state("last_collect", now_min())
    total = db.q("SELECT COUNT(*) AS n FROM items")[0]["n"]
    log(f"データベースの商品：{total} 件")


if __name__ == "__main__":
    main()
