# 見張っているアバターに対応した新作と値下げを、Discord の Webhook で流す（計画の ④）
#
#   python notify.py            いつもの実行
#   python notify.py --dry-run  流す予定のものを表示するだけ（Discord には送らない・記録もしない）
#
# 見張るアバターと流す先は config.json の notify.targets に書く。1 つのアバターにつき：
#   name      … 投稿の「対応」欄に出す名前
#   aliases   … AI が読み取った対応アバター名と比べる名前（くん・ちゃん等は気にしなくていい）
#   booth_ids … アバター本体の BOOTH 商品 ID。説明文にこの商品へのリンクがあれば対応とみなす
#   body / head / other … 衣装など（体）/ 髪型・メイクなど（頭）/ それ以外 を流す Webhook の Secret 名。
#                         書かなければ流さない。同じ Secret 名を書いたアバター同士は同じチャンネルにまとまる
#
# DISCORD_WEBHOOK_TEST が入っている間は、全部をこのテスト用チャンネルに送る（どこ行きかは投稿の下に書く）
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

from db import D1

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
N = {"targets": [], "drop_min_pct": 15, "drop_days": 30, "since_hours": 48, "max_per_run": 10,
     "username": "BOOTH 新作・セール速報"}
N.update(CFG.get("notify", {}))
TARGETS = N["targets"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def now_min():
    return int(datetime.now(timezone.utc).timestamp() // 60)


def norm(s):
    s = str(s).lower().replace(" ", "").replace("　", "")
    for w in ("くん", "ちゃん", "さん", "様"):
        s = s.replace(w, "")
    return s


def hit_targets(avatars, descr):
    """この商品が対応している見張りアバター（targets の要素）のリスト"""
    names = [norm(a) for a in avatars]
    descr = descr or ""
    out = []
    for tg in TARGETS:
        by_name = any(norm(al) and norm(al) in n for al in tg.get("aliases", []) for n in names)
        by_id = any(re.search(rf"items/{bid}(?!\d)", descr) for bid in tg.get("booth_ids", []))
        if by_name or by_id:
            out.append(tg)
    return out


def routes(fit, hits):
    """{Webhook の Secret 名: [そのチャンネルに関係するアバター名]}"""
    part = fit if fit in ("body", "head") else "other"
    out = {}
    for tg in hits:
        env = tg.get(part)
        if env:
            out.setdefault(env, []).append(tg["name"])
    return out


def label(env):
    return env.replace("DISCORD_WEBHOOK_", "")


def yen(p):
    return "無料" if p == 0 else f"¥{p:,}"


def embed(it, kind, price, old, names, avatars, env, test):
    others = max(0, len(avatars) - len(names))
    taiou = "・".join(names) + (f" ほか {others} 体" if others > 0 else "")
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
        "footer": {"text": ("値下げ" if kind == "drop" else "新作") + (f"　→ {label(env)} 行き（テスト）" if test else "")},
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
            time.sleep(float((r.json() or {}).get("retry_after", 2)) + 0.5)
            continue
        log("Discord", r.status_code, r.text[:200])
        return False
    return False


def main():
    dry = "--dry-run" in sys.argv
    if not TARGETS:
        log("config.json の notify.targets が空なので、何もしません")
        return
    test_url = os.environ.get("DISCORD_WEBHOOK_TEST", "").strip()
    envs = {tg.get(p) for tg in TARGETS for p in ("body", "head", "other") if tg.get(p)}
    urls = {e: os.environ.get(e, "").strip() for e in envs}
    if not dry and not test_url and not any(urls.values()):
        log("Discord の Webhook が設定されていないので、何もしません")
        return
    db = D1()
    t = now_min()

    # 名前か本体の商品 ID が入っていそうな商品だけ取り出す（細かい判定は Python で）
    conds, params = [], []
    for tg in TARGETS:
        for al in tg.get("aliases", []):
            conds.append("ai.avatars LIKE ?")
            params.append(f"%{al}%")
        for bid in tg.get("booth_ids", []):
            conds.append("i.descr LIKE ?")
            params.append(f"%items/{bid}%")
    rows = db.q(
        "SELECT i.id, i.name, i.shop, i.thumb_url, i.price_varies, i.src, i.first_seen, i.descr, "
        "ai.avatars, ai.fit, ai.sum FROM items i JOIN ai ON ai.item_id = i.id "
        f"WHERE i.adult = 0 AND i.last_seen >= ? AND ({' OR '.join(conds)})",
        [t - max(N["since_hours"], 6 * 24) * 60] + params)
    if not rows:
        log("流す候補はありません")
        return
    ids = [r["id"] for r in rows]
    hist = {}
    for r in db.select_in("SELECT item_id, price, seen_at FROM prices WHERE item_id IN ({}) AND seen_at >= ? "
                          "ORDER BY seen_at", ids, [t - N["drop_days"] * 1440]):
        hist.setdefault(r["item_id"], []).append(r)
    done = {(p["item_id"], p["kind"], p["price"], p["channel"])
            for p in db.select_in("SELECT item_id, kind, price, channel FROM posts WHERE item_id IN ({})", ids)}

    queue = []   # (順番の時刻, 商品, 種類, 値段, 元の値段, チャンネルに関係するアバター名, 対応一覧, Secret 名)
    for it in rows:
        h = hist.get(it["id"])
        if not h:
            continue
        price = h[-1]["price"]
        avatars = json.loads(it["avatars"] or "[]")
        rt = routes(it["fit"], hit_targets(avatars, it["descr"]))
        if not rt:
            continue
        top = max(x["price"] for x in h)
        is_new = it["src"] == "new" and it["first_seen"] >= t - N["since_hours"] * 60
        is_drop = top > 0 and price < top and (1 - price / top) * 100 >= N["drop_min_pct"]
        for env, names in rt.items():
            if is_new and not any(d[0] == it["id"] and d[1] == "new" and d[3] == env for d in done):
                queue.append((it["first_seen"], it, "new", price, None, names, avatars, env))
            if is_drop and (it["id"], "drop", price, env) not in done:
                queue.append((h[-1]["seen_at"], it, "drop", price, top, names, avatars, env))
    queue.sort(key=lambda x: x[0])
    log(f"流す候補：{len(queue)} 件（1 回に {N['max_per_run']} 件まで）")

    sent = 0
    for _, it, kind, price, old, names, avatars, env in queue[: N["max_per_run"]]:
        if dry:
            log(f"[{label(env)}] {'値下げ' if kind == 'drop' else '新作'} {it['name']} {yen(price)} 対応={avatars}")
            continue
        url = test_url or urls.get(env)
        if not url:
            continue
        payload = {"username": N["username"],
                   "embeds": [embed(it, kind, price, old, names, avatars, env, bool(test_url))]}
        if send(url, payload):
            db.q("INSERT OR IGNORE INTO posts (item_id, kind, price, channel, posted_at) VALUES (?, ?, ?, ?, ?)",
                 [it["id"], kind, price, env, t])
            sent += 1
        time.sleep(1.2)
    log(f"Discord に流した数：{sent} 件")


if __name__ == "__main__":
    main()
