# はるか・彼方・悠莉に対応した新作と値下げを、Discord の Webhook で流す（計画の ④）
#
#   python notify.py            いつもの実行
#   python notify.py --dry-run  流す予定のものを表示するだけ（Discord には送らない・記録もしない）
#
# 流す先
#   衣装など体に合わせるもの（fit = body）           → 衣装チャンネル（はるか・彼方・悠莉のどれか対応なら）
#   髪型・メイクなど頭に合わせるもの（fit = head）   → はるか / 彼方 のチャンネル（書かれている方だけ。悠莉は流さない）
#   それ以外（アバター本体・ギミックなど）           → はるか / 彼方 のチャンネル
#
# 環境変数（GitHub の Secret）
#   DISCORD_WEBHOOK_OUTFIT / DISCORD_WEBHOOK_HARUKA / DISCORD_WEBHOOK_KANATA
#   DISCORD_WEBHOOK_TEST … これが入っている間は、全部をこのテスト用チャンネルに送る（どこ行きかは投稿の下に書く）
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

from db import D1

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
N = {
    "avatars": {"haruka": ["はるか", "Haruka"], "kanata": ["彼方", "Kanata"], "yuuri": ["悠莉", "Yuuri"]},
    "names": {"haruka": "はるか", "kanata": "彼方", "yuuri": "悠莉"},
    "head_channels": ["haruka", "kanata"],
    "drop_min_pct": 15,       # これ以上の値下げだけ流す
    "drop_days": 30,          # 「元の値段」はこの日数の中の一番高い値段
    "since_hours": 48,        # この時間より前に見つけた新作は流さない（初回にまとめて流れないように）
    "max_per_run": 10,        # 1 回に流す最大の数（残りは次の回）
    "username": "BOOTH 新作・セール速報",
}
N.update(CFG.get("notify", {}))
CHANNEL_ENV = {"outfit": "DISCORD_WEBHOOK_OUTFIT", "haruka": "DISCORD_WEBHOOK_HARUKA", "kanata": "DISCORD_WEBHOOK_KANATA"}
CHANNEL_LABEL = {"outfit": "衣装", "haruka": "はるか", "kanata": "彼方"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def now_min():
    return int(datetime.now(timezone.utc).timestamp() // 60)


def norm(s):
    s = str(s).lower().replace(" ", "").replace("　", "")
    for w in ("くん", "ちゃん", "さん", "様"):
        s = s.replace(w, "")
    return s


def matched(avatars):
    """対応アバターの一覧から、見張っているアバター（haruka / kanata / yuuri）を返す"""
    got = []
    names = [norm(a) for a in avatars]
    for key, aliases in N["avatars"].items():
        if any(norm(al) and norm(al) in n for al in aliases for n in names):
            got.append(key)
    return got


def channels_for(fit, hits):
    if fit == "body":
        return ["outfit"] if hits else []
    return [k for k in N["head_channels"] if k in hits]


def yen(p):
    return "無料" if p == 0 else f"¥{p:,}"


def embed(it, kind, price, old, hits, avatars, channel, test):
    watch = [N["names"][k] for k in ("haruka", "kanata", "yuuri") if k in hits]
    others = len(avatars) - len(watch)
    taiou = "・".join(watch) + (f" ほか {others} 体" if others > 0 else "")
    if kind == "drop":
        pct = round((1 - price / old) * 100)
        price_text = f"~~{yen(old)}~~ → **{yen(price)}**（{pct}%OFF）"
    else:
        price_text = yen(price) + ("〜" if it["price_varies"] else "")
    e = {
        "title": (it["name"] or "")[:250],
        "url": f"https://booth.pm/ja/items/{it['id']}",
        "description": it["sum"] or "",
        "color": 0xF24D59 if kind == "drop" else 0xFF7A45,
        "fields": [
            {"name": "値段", "value": price_text, "inline": True},
            {"name": "対応", "value": taiou or "—", "inline": True},
            {"name": "ショップ", "value": (it["shop"] or "—")[:100], "inline": True},
        ],
        "footer": {"text": ("値下げ" if kind == "drop" else "新作") + (f"　→ {CHANNEL_LABEL[channel]}チャンネル行き（テスト）" if test else "")},
    }
    if it["thumb_url"]:
        e["thumbnail"] = {"url": it["thumb_url"]}
    return e


def send(url, payload):
    for attempt in range(4):
        r = requests.post(url + ("&" if "?" in url else "?") + "wait=true", json=payload, timeout=30)
        if r.status_code in (200, 204):
            return True
        if r.status_code == 429:
            wait = float((r.json() or {}).get("retry_after", 2)) + 0.5
            time.sleep(wait)
            continue
        log("Discord", r.status_code, r.text[:200])
        return False
    return False


def main():
    dry = "--dry-run" in sys.argv
    test_url = os.environ.get("DISCORD_WEBHOOK_TEST", "").strip()
    urls = {k: os.environ.get(v, "").strip() for k, v in CHANNEL_ENV.items()}
    if not dry and not test_url and not any(urls.values()):
        log("Discord の Webhook が設定されていないので、何もしません")
        return
    db = D1()
    t = now_min()
    # 見張っているアバター名が対応アバターに入っている商品だけ取り出す（細かい判定は Python で）
    aliases = [a for al in N["avatars"].values() for a in al]
    like = " OR ".join(["ai.avatars LIKE ?"] * len(aliases))
    rows = db.q(
        "SELECT i.id, i.name, i.shop, i.thumb_url, i.price_varies, i.src, i.first_seen, ai.avatars, ai.fit, ai.sum "
        "FROM items i JOIN ai ON ai.item_id = i.id "
        f"WHERE i.adult = 0 AND i.last_seen >= ? AND ({like})",
        [t - max(N["since_hours"], 6 * 24) * 60] + [f"%{a}%" for a in aliases])
    if not rows:
        log("流す候補はありません")
        return
    hist = {}
    for r in db.select_in("SELECT item_id, price, seen_at FROM prices WHERE item_id IN ({}) AND seen_at >= ? "
                          "ORDER BY seen_at", [r["id"] for r in rows], [t - N["drop_days"] * 1440]):
        hist.setdefault(r["item_id"], []).append(r)
    done = {(p["item_id"], p["kind"], p["price"], p["channel"])
            for p in db.select_in("SELECT item_id, kind, price, channel FROM posts WHERE item_id IN ({})",
                                  [r["id"] for r in rows])}

    queue = []   # (投稿する順番の時刻, 商品, 種類, 値段, 元の値段, 当たったアバター, 対応一覧, チャンネル)
    for it in rows:
        h = hist.get(it["id"])
        if not h:
            continue
        price = h[-1]["price"]
        avatars = json.loads(it["avatars"] or "[]")
        hits = matched(avatars)
        chans = channels_for(it["fit"], hits)
        if not chans:
            continue
        if it["src"] == "new" and it["first_seen"] >= t - N["since_hours"] * 60:
            for c in chans:
                if not any(d[0] == it["id"] and d[1] == "new" and d[3] == c for d in done):   # 新作は値段に関係なく 1 回だけ
                    queue.append((it["first_seen"], it, "new", price, None, hits, avatars, c))
        top = max(x["price"] for x in h)
        if top > 0 and price < top and (1 - price / top) * 100 >= N["drop_min_pct"]:
            for c in chans:
                if (it["id"], "drop", price, c) not in done:
                    queue.append((h[-1]["seen_at"], it, "drop", price, top, hits, avatars, c))
    queue.sort(key=lambda x: x[0])
    log(f"流す候補：{len(queue)} 件（1 回に {N['max_per_run']} 件まで）")

    sent = 0
    for _, it, kind, price, old, hits, avatars, c in queue[: N["max_per_run"]]:
        url = test_url or urls.get(c)
        if dry:
            log(f"[{CHANNEL_LABEL[c]}] {'値下げ' if kind == 'drop' else '新作'} {it['name']} {yen(price)} 対応={avatars}")
            continue
        if not url:
            continue
        payload = {"username": N["username"],
                   "embeds": [embed(it, kind, price, old, hits, avatars, c, bool(test_url))]}
        if send(url, payload):
            db.q("INSERT OR IGNORE INTO posts (item_id, kind, price, channel, posted_at) VALUES (?, ?, ?, ?, ?)",
                 [it["id"], kind, price, c, t])
            sent += 1
        time.sleep(1.2)
    log(f"Discord に流した数：{sent} 件")


if __name__ == "__main__":
    main()
