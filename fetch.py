# VRChat ワールドのインフォボード用データを集めて docs/ に書き出す
#   docs/feed.json        … 天気（Open-Meteo）・VRChat ニュース（RSS）・BOOTH の目玉商品（D1 から）
#   docs/booth_atlas.jpg  … BOOTH の目玉商品のサムネイルを 1 枚にまとめたタイル画像（ワールドが 1 回で読める）
#
# BOOTH の商品は collect.py / classify.py が D1 に入れたもの（Claude の判定つき）から選ぶ。
# D1 の設定（CF_* の環境変数）がない時は、BOOTH の部分は空になる。
import html
import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests

JST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "feed.json")
ATLAS = os.path.join(ROOT, "docs", "booth_atlas.jpg")
UA = {"User-Agent": "Mozilla/5.0 (compatible; vrc-dashboard-feed/1.0; +https://github.com/)"}
GENRES = ["avatar", "outfit", "world", "gimmick"]

CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
S = {"slots": 24, "cols": 6, "cell": 256, "days": 3, "min_quality": CFG.get("min_quality", 3),
     "drop_min_pct": 15, "max_avatars": 3}
S.update(CFG.get("showcase", {}))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def epoch_min(dt):
    return int(dt.timestamp() // 60)


def get(url, **kw):
    headers = kw.pop("headers", UA)
    for i in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=25, **kw)
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


# ───────────────────────── VRChat ニュース（RSS） ─────────────────────────
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
        desc = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", html.unescape(f("description")))).strip()
        if pattern and not re.search(pattern, title + " " + cats + " " + desc, re.I):
            continue
        dt = parse_date(f("pubDate") or f("published") or f("updated"))
        items.append({"t": title, "m": epoch_min(dt), "desc": desc})
        if len(items) >= limit:
            break
    return items


# ───────────────────────── BOOTH の目玉商品（D1 から） ─────────────────────────
def yen(p):
    return "無料" if p == 0 else f"¥{p:,}"


def pick_booth(now_m):
    """D1 から最近の新作・値下げを選ぶ。返り値：選んだ商品のリスト（よい順）"""
    if not os.environ.get("CF_API_TOKEN", "").strip():
        log("booth：D1 の設定がないので、BOOTH は空にします")
        return []
    from db import D1
    db = D1()
    since = now_m - S["days"] * 1440
    rows = db.q(
        "SELECT i.id, i.name, i.shop, i.thumb_url, i.price_varies, i.src, i.first_seen, i.likes, "
        "ai.genre, ai.q, ai.sum, ai.avatars, ai.kind, "
        "(SELECT price FROM prices p WHERE p.item_id = i.id ORDER BY seen_at DESC LIMIT 1) AS price, "
        "(SELECT MAX(price) FROM prices p WHERE p.item_id = i.id AND p.seen_at >= ?) AS top "
        "FROM items i JOIN ai ON ai.item_id = i.id "
        "WHERE i.adult = 0 AND ai.q >= ? AND ai.genre IN ('avatar','outfit','world','gimmick') AND i.last_seen >= ?",
        [now_m - 30 * 1440, S["min_quality"], since])
    out = []
    for r in rows:
        if r["price"] is None:
            continue
        top = r["top"] or r["price"]
        off = round((1 - r["price"] / top) * 100) if top > 0 and r["price"] < top else 0
        is_new = r["src"] == "new" and r["first_seen"] >= since
        if off >= S["drop_min_pct"] or (is_new and r["kind"] == "sale"):
            k = "SALE"
        elif is_new:
            k = "NEW"
        else:
            continue
        av = json.loads(r["avatars"] or "[]")
        av_text = "・".join(av[:S["max_avatars"]]) + (f" ほか{len(av) - S['max_avatars']}体" if len(av) > S["max_avatars"] else "")
        out.append({"id": r["id"], "t": (r["name"] or "")[:80], "s": (r["shop"] or "")[:30], "g": r["genre"],
                    "p": yen(r["price"]) + ("〜" if r["price_varies"] else ""), "old": yen(top) if off else "",
                    "off": off, "k": k, "sum": r["sum"] or "", "av": av_text, "m": r["first_seen"],
                    "q": r["q"], "l": r["likes"] or 0, "thumb": r["thumb_url"] or ""})
    # 値下げ（割引が大きい順）→ 品質 → いいね → 新しい順
    out.sort(key=lambda x: (x["k"] != "SALE", -x["off"], -x["q"], -x["l"], -x["m"]))
    log(f"booth：候補 {len(out)} 件")
    return out


def build_atlas(items):
    """先頭から slots 件のサムネイルを 1 枚の画像にまとめる。各商品に slot（何番目のマス。なければ -1）を付ける"""
    from PIL import Image
    cols, cell, slots = S["cols"], S["cell"], S["slots"]
    rows = (slots + cols - 1) // cols
    atlas = Image.new("RGB", (cols * cell, rows * cell), (24, 26, 38))
    n = 0
    for it in items:
        it["slot"] = -1
        if n >= slots or not it["thumb"]:
            continue
        r = get(it["thumb"], headers=dict(UA, Referer="https://booth.pm/"))
        if r is None:
            continue
        try:
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:
            log("thumb", e)
            continue
        w, h = im.size   # 真ん中を正方形に切り抜いて縮める
        side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
        im = im.resize((cell, cell), Image.LANCZOS)
        atlas.paste(im, ((n % cols) * cell, (n // cols) * cell))
        it["slot"] = n
        n += 1
    atlas.save(ATLAS, "JPEG", quality=85, optimize=True)
    log(f"atlas：{n} 枚 → {ATLAS}")
    return {"cols": cols, "rows": rows}


# ───────────────────────── まとめ ─────────────────────────
def main():
    now = datetime.now(timezone.utc)
    now_m = epoch_min(now)
    feed = {"updated": datetime.now(JST).strftime("%m/%d %H:%M"), "now": now_m}

    feed["weather"] = weather()
    if not feed["weather"]:   # 天気が取れなかった時は、公開中の feed.json の天気を残す
        r = get(CFG.get("pages_url", "").rstrip("/") + "/feed.json") if CFG.get("pages_url") else None
        try:
            prev = r.json() if r is not None else json.load(open(OUT, encoding="utf-8"))
            feed["weather"] = prev.get("weather", [])
            log("weather：取れなかったので前回の天気を使います")
        except Exception:
            pass
    log("weather", len(feed["weather"]))

    n = CFG.get("items_per_list", 10)
    news = []
    for src in CFG.get("vrc_news", []):
        for it in rss(src["url"], n, src.get("filter", "")):
            news.append({"t": it["t"][:70], "s": src["name"], "sum": it["desc"][:60], "m": it["m"], "k": "NEWS"})
    news = [x for x in news if x["m"] >= now_m - CFG.get("news_days", 14) * 1440]   # 古いニュースは出さない
    news.sort(key=lambda x: -x["m"])
    log("news", len(news))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    try:
        booth = pick_booth(now_m)
    except Exception as e:
        log("booth：D1 から読めませんでした", e)
        booth = []
    shown = booth[: S["slots"]]
    if shown:
        feed["atlas"] = build_atlas(shown)
    feed["booth"] = [{k: v for k, v in it.items() if k not in ("thumb", "id", "q", "l")} for it in shown]

    # 前のパネル（ジャンル別タブ）用。新しいパネルでは使わない
    vrc = {g: [] for g in GENRES}
    for it in booth:
        if len(vrc[it["g"]]) < n * 2:
            vrc[it["g"]].append({"t": it["t"][:60], "s": it["s"], "p": it["p"], "k": it["k"], "sum": it["sum"],
                                 "m": it["m"], "q": it["q"], "l": it["l"]})
    vrc["news"] = news[:n]
    feed["vrc"] = vrc

    json.dump(feed, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
