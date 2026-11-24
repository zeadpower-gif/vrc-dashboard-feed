# VRChat ワールドのダッシュボード用データを集めて docs/feed.json に書き出す
#   天気（Open-Meteo）・Yahoo!ニュース（RSS）・VRChat ニュース（RSS）・BOOTH の新着とセール
#   BOOTH とニュースは Gemini で「アバター／衣装／ワールド／ギミック」「新作／セール」に分けて、ひとこと要約を付ける
#   GEMINI_API_KEY が無い・失敗した時は、カテゴリとタイトルの言葉だけで分ける
import hashlib
import base64
import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests

JST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "feed.json")
CACHE = os.path.join(ROOT, "docs", "ai_cache.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; vrc-dashboard-feed/1.0; +https://github.com/)"}
GENRES = ["avatar", "outfit", "world", "gimmick"]

CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def epoch_min(dt):
    return int(dt.timestamp() // 60)


def get(url, **kw):
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=25, **kw)
            if r.status_code == 200:
                return r
            log("HTTP", r.status_code, url)
        except Exception as e:
            log("ERR", e, url)
        time.sleep(2 + i * 3)
    return None


# ───────────────────────── 天気 ─────────────────────────
WMO = {  # コード → (アイコン番号, 日本語)
    0: (0, "快晴"), 1: (0, "晴れ"), 2: (1, "晴れ時々曇り"), 3: (2, "曇り"),
    45: (3, "霧"), 48: (3, "霧"),
    51: (4, "霧雨"), 53: (4, "霧雨"), 55: (4, "霧雨"), 56: (4, "着氷性の霧雨"), 57: (4, "着氷性の霧雨"),
    61: (4, "小雨"), 63: (4, "雨"), 65: (4, "大雨"), 66: (4, "着氷性の雨"), 67: (4, "着氷性の雨"),
    71: (5, "小雪"), 73: (5, "雪"), 75: (5, "大雪"), 77: (5, "霧雪"),
    80: (4, "にわか雨"), 81: (4, "にわか雨"), 82: (4, "激しいにわか雨"),
    85: (5, "にわか雪"), 86: (5, "激しいにわか雪"),
    95: (6, "雷雨"), 96: (6, "雷雨（ひょう）"), 99: (6, "雷雨（ひょう）"),
}
DIR16 = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
         "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]
WD = "月火水木金土日"


def weather():
    cities = [dict(c, mine=False) for c in CFG["cities"]] + [dict(c, mine=True) for c in CFG.get("my_cities", [])]
    params = {
        "latitude": ",".join(str(c["lat"]) for c in cities),
        "longitude": ",".join(str(c["lon"]) for c in cities),
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m",
        "hourly": "precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "wind_speed_10m_max,wind_direction_10m_dominant",
        "wind_speed_unit": "ms", "timezone": "Asia/Tokyo", "forecast_days": 4,
    }
    r = get("https://api.open-meteo.com/v1/forecast", params=params)
    if r is None:
        return []
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    out = []
    now_h = datetime.now(JST).strftime("%Y-%m-%dT%H:00")
    for c, d in zip(cities, data):
        cur, day, hr = d["current"], d["daily"], d["hourly"]
        icon, text = WMO.get(cur["weather_code"], (2, "—"))
        pop = 0
        if now_h in hr["time"]:
            pop = hr["precipitation_probability"][hr["time"].index(now_h)] or 0
        wdeg = cur["wind_direction_10m"] or 0
        days = []
        for i in range(1, min(4, len(day["time"]))):
            dt = datetime.strptime(day["time"][i], "%Y-%m-%d")
            ic, tx = WMO.get(day["weather_code"][i], (2, "—"))
            days.append({
                "d": f"{dt.month}/{dt.day}({WD[dt.weekday()]})", "icon": ic, "text": tx,
                "hi": round(day["temperature_2m_max"][i]), "lo": round(day["temperature_2m_min"][i]),
                "pop": day["precipitation_probability_max"][i] or 0,
                "wspd": round(day["wind_speed_10m_max"][i] or 0, 1),
                "wdir": DIR16[int(((day["wind_direction_10m_dominant"][i] or 0) + 11.25) // 22.5) % 16],
            })
        out.append({
            "name": c["name"], "mine": c["mine"],
            "now": {
                "t": round(cur["temperature_2m"]), "icon": icon, "text": text,
                "hum": round(cur["relative_humidity_2m"]), "pop": pop,
                "wdeg": round(wdeg), "wdir": DIR16[int((wdeg + 11.25) // 22.5) % 16],
                "wspd": round(cur["wind_speed_10m"], 1),
                "hi": round(day["temperature_2m_max"][0]), "lo": round(day["temperature_2m_min"][0]),
                "dpop": day["precipitation_probability_max"][0] or 0,
            },
            "days": days,
        })
    return out


# ───────────────────────── RSS ─────────────────────────
def parse_date(s):
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def rss(url, limit=10, pattern=""):
    r = get(url)
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        log("RSS parse", e, url)
        return []
    items = []
    for it in root.iter():
        if not it.tag.endswith("item") and not it.tag.endswith("entry"):
            continue
        def f(name):
            for ch in it:
                if ch.tag.split("}")[-1] == name:
                    return (ch.text or "").strip() or ch.attrib.get("href", "")
            return ""
        title = html.unescape(f("title"))
        cats = " ".join((ch.text or "") for ch in it if ch.tag.split("}")[-1] == "category")
        if pattern and not re.search(pattern, title + " " + cats + " " + f("description"), re.I):
            continue
        dt = parse_date(f("pubDate") or f("published") or f("updated"))
        items.append({"t": title, "url": f("link"), "m": epoch_min(dt), "desc": re.sub("<[^>]+>", "", html.unescape(f("description")))[:200]})
        if len(items) >= limit:
            break
    return items


# ───────────────────────── BOOTH ─────────────────────────
def booth_list(category, keyword=""):
    q = {"sort": "new", "tags[]": "VRChat"}
    if keyword:
        q["q"] = keyword
    url = f"https://booth.pm/ja/browse/{urllib.parse.quote(category)}?" + urllib.parse.urlencode(q)
    r = get(url)
    if r is None:
        return []
    page = r.text
    items, seen = [], set()
    # 商品カード（data-product-*）を読む
    for m in re.finditer(r"<li[^>]*data-product-id=\"(\d+)\"[^>]*>", page):
        tag = m.group(0)
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        def attr(n):
            a = re.search(n + r"=\"([^\"]*)\"", tag)
            return html.unescape(a.group(1)) if a else ""
        items.append({"id": pid, "t": attr("data-product-name"), "p": attr("data-product-price"),
                      "s": attr("data-product-brand"), "cat": attr("data-product-category")})
    if not items:   # 形が変わっていた時：商品ページへのリンクだけ拾って、商品ごとの JSON を読む
        for pid in dict.fromkeys(re.findall(r"/items/(\d+)", page)):
            items.append({"id": pid})
    return items[: CFG.get("booth_per_category", 12)]


def booth_detail(it):
    """商品ごとの JSON から、いいね数・説明・サムネイルを読む（クオリティの判定に使う）"""
    r = get(f"https://booth.pm/ja/items/{it['id']}.json")
    if r is not None:
        try:
            j = r.json()
            it["t"] = it.get("t") or j.get("name", "")
            it["p"] = it.get("p") or j.get("price", "")
            it["s"] = it.get("s") or (j.get("shop") or {}).get("name", "")
            it["desc"] = re.sub(r"\s+", " ", j.get("description") or "")[:400]
            it["dlen"] = len(j.get("description") or "")
            it["likes"] = int(j.get("wish_lists_count") or 0)
            it["nimg"] = len(j.get("images") or [])
            imgs = j.get("images") or []
            if imgs:
                it["img"] = imgs[0].get("resized") or imgs[0].get("original") or ""
        except Exception as e:
            log("booth json", it["id"], e)
    time.sleep(1)
    return it


def thumb(url):
    """サムネイルを小さく読み込む（AI に見た目を判定してもらう）"""
    if not url:
        return None
    try:
        r = requests.get(url, headers=dict(UA, Referer="https://booth.pm/"), timeout=20)
        if r.status_code == 200 and len(r.content) < 1_500_000:
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            return {"inline_data": {"mime_type": mime, "data": base64.b64encode(r.content).decode()}}
    except Exception as e:
        log("thumb", e)
    return None


def yen(p):
    digits = re.sub(r"[^\d]", "", str(p))
    return f"¥{int(digits):,}" if digits else str(p)


SALE_WORDS = r"セール|SALE|sale|％OFF|%OFF|%off|割引|値下げ|半額|期間限定価格|ブラックフライデー"


# ───────────────────────── Gemini ─────────────────────────
def gemini(prompt, schema=None, search=False, parts=None):
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    body = {"contents": [{"role": "user", "parts": (parts or []) + [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    else:
        body["generationConfig"] = {"responseMimeType": "application/json"}
        if schema:
            body["generationConfig"]["responseSchema"] = schema
    for model in CFG.get("gemini_models", ["gemini-flash-latest"]):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = None
        for attempt in range(3):          # 混んでいる時（503・429）は少し待ってやり直す
            try:
                r = requests.post(url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                  json=body, timeout=120)
            except Exception as e:
                log("Gemini", model, e)
                r = None
            if r is not None and r.status_code in (429, 500, 503) and attempt < 2:
                wait = 15 * (attempt + 1)
                log("Gemini", model, r.status_code, f"混んでいるので {wait} 秒待ってやり直し")
                time.sleep(wait)
                continue
            break
        if r is None:
            continue
        if r.status_code != 200:
            try:
                msg = r.json()["error"]["message"][:120]
            except Exception:
                msg = r.text[:120]
            log("Gemini", model, r.status_code, msg)
            continue
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            m = re.search(r"[\[{].*[\]}]", text, re.S)
            return json.loads(m.group(0) if m else text)
        except Exception as e:
            log("Gemini parse", model, e)
    return None


CLASSIFY_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "id": {"type": "STRING"},
        "genre": {"type": "STRING", "enum": ["avatar", "outfit", "world", "gimmick", "other"]},
        "kind": {"type": "STRING", "enum": ["new", "sale"]},
        "sum": {"type": "STRING"},
        "q": {"type": "INTEGER"},
    }, "required": ["id", "genre", "kind", "sum", "q"]},
}


def heuristic_q(it):
    """AI が使えない時のクオリティ（いいね数・説明の長さ・画像の枚数から 1〜5）"""
    q = 2
    likes = it.get("likes", 0)
    q += (likes >= 20) + (likes >= 100)
    q += it.get("dlen", 0) >= 400 or it.get("nimg", 0) >= 4
    return min(5, q)


def classify(items, cache):
    todo = [it for it in items if it["key"] not in cache]
    for i in range(0, len(todo), 15):           # 画像つきなので 15 件ずつ
        batch = todo[i:i + 15]
        parts = []
        for it in batch:
            info = {"id": it["key"], "title": it["t"], "shop": it.get("s", ""), "category": it.get("cat", ""),
                    "price": it.get("p", ""), "likes": it.get("likes"), "images": it.get("nimg"),
                    "hint": it.get("hint", ""), "desc": it.get("desc", "")[:300]}
            parts.append({"text": json.dumps(info, ensure_ascii=False)})
            im = thumb(it.get("img", ""))
            if im:
                parts.append({"text": f"↑ id={it['key']} のサムネイル："})
                parts.append(im)
        prompt = (
            "上は VRChat 向けの BOOTH 商品とニュースの一覧です（商品にはサムネイル画像が付いています）。"
            "それぞれについて次を JSON 配列で返してください。\n"
            "- genre: avatar（アバター本体）/ outfit（衣装・髪・アクセサリー）/ world（ワールド・ワールド用アセット）/ "
            "gimmick（ギミック・ツール・シェーダー・システム）/ other\n"
            "- kind: セール・割引・期間限定価格なら sale、それ以外は new\n"
            "- sum: 日本語で20文字以内のひとこと紹介（対応アバターや特徴など。絵文字なし）\n"
            "- q: クオリティ 1〜5。サムネイルの見た目の完成度（解像度・ライティング・作り込み）、説明の丁寧さ（対応アバター・"
            "導入方法・利用規約）、価格とのつり合い、いいね数で判断。ニュースは 5。\n"
            "  1〜2 の例：粗い・低解像度・ほぼ説明なし・明らかな流用や生成画像だけ・VRChat と関係ない・テスト出品\n")
        res = gemini(prompt, CLASSIFY_SCHEMA, parts=parts)
        if isinstance(res, list):
            for r_ in res:
                if isinstance(r_, dict) and "id" in r_:
                    cache[r_["id"]] = {"genre": r_.get("genre", "other"), "kind": r_.get("kind", "new"),
                                       "sum": (r_.get("sum") or "")[:24], "q": int(r_.get("q") or 3)}
    for it in items:
        c = cache.get(it["key"])
        if c:
            it.update(c)
        else:   # AI が使えない時
            it["genre"] = it.get("hint_genre", "other")
            it["kind"] = "sale" if re.search(SALE_WORDS, it["t"] + it.get("desc", "")) else "new"
            it["sum"] = ""
            it["q"] = 5 if it["key"].startswith("n") else heuristic_q(it)


def ai_sale_search():
    """Gemini の Google 検索で、今やっている VRChat 関連のセールを探す（BOOTH の一覧で拾えないもの用）"""
    prompt = (
        "今日は" + datetime.now(JST).strftime("%Y年%m月%d日") + "です。現在開催中の VRChat 向けアバター・衣装・ワールド・"
        "ギミックの BOOTH セール情報を Google 検索で調べ、確実に開催中のものだけを最大12件、次の形式の JSON 配列だけで答えてください。"
        "[{\"genre\":\"avatar|outfit|world|gimmick\",\"t\":\"商品名\",\"s\":\"ショップ名\",\"p\":\"セール価格（分かれば）\","
        "\"sum\":\"20文字以内（割引率や期限など）\",\"url\":\"情報元URL\"}]")
    res = gemini(prompt, search=True)
    out = []
    if isinstance(res, list):
        for r_ in res:
            if isinstance(r_, dict) and r_.get("t") and r_.get("genre") in GENRES:
                out.append({"t": r_["t"][:60], "s": r_.get("s", "")[:30], "p": r_.get("p", ""), "k": "SALE",
                            "sum": r_.get("sum", "")[:24], "m": epoch_min(datetime.now(timezone.utc)),
                            "genre": r_["genre"], "src": "AI検索"})
    return out


# ───────────────────────── まとめ ─────────────────────────
def main():
    feed = {"updated": datetime.now(JST).strftime("%m/%d %H:%M"), "now": epoch_min(datetime.now(timezone.utc))}
    feed["weather"] = weather()
    log("weather", len(feed["weather"]))

    n = CFG.get("items_per_list", 10)
    feed["yahoo"] = []
    for y in CFG["yahoo"]:
        its = rss(y["url"], n)
        feed["yahoo"].append({"name": y["name"], "items": [{"t": i["t"], "m": i["m"]} for i in its]})
    log("yahoo", sum(len(y["items"]) for y in feed["yahoo"]))

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            cache = {}

    # BOOTH（カテゴリ別の新着＋「セール」で検索した新着）。見に行くのは booth_hours ごと（それ以外は前回の結果を使う）
    now_m = epoch_min(datetime.now(timezone.utc))
    last_b = cache.get("_booth", {})
    if now_m - last_b.get("m", 0) >= CFG.get("booth_hours", 12) * 60 - 5:
        booth = []
        for g, cat in CFG["booth"].items():
            for kw, hint in (("", "new"), ("セール", "sale")):
                for it in booth_list(cat, kw):
                    it = booth_detail(it)
                    if not it.get("t"):
                        continue
                    it["key"] = "b" + it["id"] + ("s" if hint == "sale" else "")
                    it["hint"] = "「セール」で検索して見つかった" if hint == "sale" else ""
                    it["hint_genre"] = g
                    it["m"] = now_m
                    booth.append(it)
                time.sleep(2)
        if booth:
            last_b = {"m": now_m, "items": booth}
        log("booth fetched", len(booth))
    else:
        log("booth cached", len(last_b.get("items", [])))
    booth = last_b.get("items", [])
    cache["_booth"] = last_b

    # VRChat ニュース
    news = []
    for src in CFG.get("vrc_news", []):
        for it in rss(src["url"], n, src.get("filter", "")):
            it["key"] = "n" + hashlib.md5((it["url"] or it["t"]).encode()).hexdigest()[:10]
            it["s"] = src["name"]
            news.append(it)
    news.sort(key=lambda x: -x["m"])
    log("news", len(news))

    classify(booth + news, cache)

    vrc = {g: [] for g in GENRES}
    seen = set()
    min_q, min_likes = CFG.get("min_quality", 3), CFG.get("min_likes", 0)
    dropped = 0
    for it in booth:
        g = it.get("genre", "other")
        if g not in vrc or it["id"] + g in seen:
            continue
        if it.get("q", 3) < min_q or it.get("likes", 0) < min_likes:   # クオリティが低いものははねる
            dropped += 1
            continue
        seen.add(it["id"] + g)
        vrc[g].append({"t": it["t"][:60], "s": it.get("s", "")[:30], "p": yen(it.get("p", "")),
                       "k": "SALE" if it.get("kind") == "sale" else "NEW", "sum": it.get("sum", ""), "m": it["m"],
                       "q": it.get("q", 3), "l": it.get("likes", 0)})
    log("booth dropped (low quality)", dropped)
    if CFG.get("ai_sale_search"):   # Google 検索つきの AI は 6 時間に1回だけ（無料枠の節約）
        last = cache.get("_sale_search", {})
        if now_m - last.get("m", 0) > 6 * 60:
            found = ai_sale_search()
            if found:
                last = {"m": now_m, "items": found}
        for it in last.get("items", []):
            it = dict(it)
            vrc[it.pop("genre")].append(it)
        cache["_sale_search"] = last
    for g in GENRES:   # セールを先に、そのあと新着
        vrc[g] = sorted(vrc[g], key=lambda x: (x["k"] != "SALE", -x.get("q", 3), -x.get("l", 0)))[: n * 2]
    vrc["news"] = [{"t": it["t"][:70], "s": it["s"], "sum": it.get("sum", ""), "m": it["m"], "k": "NEWS"} for it in news[:n]]
    feed["vrc"] = vrc

    # キャッシュは最近使ったものだけ残す
    keep = {it["key"] for it in booth + news} | {"_sale_search", "_booth"}
    json.dump({k: v for k, v in cache.items() if k in keep}, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(feed, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
