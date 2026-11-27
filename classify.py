# D1 の items のうち、まだ AI で判定していない商品を Claude に送って、ai 表に結果を書く（計画の ②）
#
#   python classify.py            いつもの実行（1 回に config の ai.per_run 件まで）
#   python classify.py --dry-run  1 グループ分だけ判定して表示する（D1 には書かない）
#
# 必要な環境変数：ANTHROPIC_API_KEY、CF_ACCOUNT_ID / CF_D1_DATABASE_ID / CF_API_TOKEN
# 返してもらうもの：genre / kind / sum / q / avatars（対応アバター名）/ fit（body・head・other）
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

from db import D1

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
A = {"model": "claude-haiku-5-5", "per_run": 150, "batch": 15, "price_in": 0.10, "price_out": 0.50}
A.update(CFG.get("ai", {}))
UA = {"User-Agent": "Mozilla/5.0 (compatible; vrc-dashboard-feed/1.0; +https://github.com/)"}
API = "https://api.anthropic.com/v1/messages"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def now_min():
    return int(datetime.now(timezone.utc).timestamp() // 60)


TOOL = {
    "name": "record_items",
    "description": "商品ごとの判定結果を記録する",
    "input_schema": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "genre": {"type": "string", "enum": ["avatar", "outfit", "world", "gimmick", "other"]},
                "kind": {"type": "string", "enum": ["new", "sale"]},
                "sum": {"type": "string", "description": "日本語 20 文字以内のひとこと紹介。絵文字なし"},
                "q": {"type": "integer", "minimum": 1, "maximum": 5},
                "avatars": {"type": "array", "items": {"type": "string"},
                            "description": "対応アバター名。書かれている通りの基本の名前（くん・ちゃん等は外す）"},
                "fit": {"type": "string", "enum": ["body", "head", "other"]},
            },
            "required": ["id", "genre", "kind", "sum", "q", "avatars", "fit"],
        }}},
        "required": ["items"],
    },
}

PROMPT = (
    "上は BOOTH の VRChat 向け商品です（サムネイル画像付きのものもあります）。商品ごとに record_items で記録してください。\n"
    "- genre: avatar（アバター本体）/ outfit（衣装・髪・アクセサリー・テクスチャなどアバターに付けるもの）/ "
    "world（ワールド・ワールド用アセット）/ gimmick（ギミック・ツール・シェーダー・システム）/ other\n"
    "- kind: 商品名や説明にセール・割引・期間限定価格・発売記念価格とあれば sale、それ以外は new\n"
    "- sum: 日本語 20 文字以内のひとこと紹介（特徴など。絵文字なし）\n"
    "- q: クオリティ 1〜5。サムネイルの完成度（解像度・ライティング・作り込み）、説明の丁寧さ（対応アバター・導入方法・"
    "利用規約）、価格とのつり合い、いいね数で判断。1〜2 は粗い・低解像度・ほぼ説明なし・明らかな流用や生成画像だけ・"
    "VRChat と関係ない・テスト出品\n"
    "- avatars: 商品名や説明で「対応」とされているアバター名をすべて。【〇〇対応】【〇体対応】の中身や「対応アバター：」の"
    "一覧を読む。名前は書かれている通りの基本の名前にし、くん・ちゃん・様などは外す（例：「はるかくん」→「はるか」）。"
    "英語表記が併記されていれば日本語の方だけ。アバター本体の商品はそのアバター自身の名前を入れる。"
    "対応アバターが書かれていなければ空の配列\n"
    "- fit: 衣装・靴・下着・アクセサリーなど体に合わせるものは body、髪型・メイク・顔や目のテクスチャ・表情など頭に"
    "合わせるものは head、それ以外（アバター本体・ワールド・ギミックなど）は other\n")


def thumb(url):
    if not url:
        return None
    try:
        r = requests.get(url, headers=dict(UA, Referer="https://booth.pm/"), timeout=20)
        if r.status_code == 200 and len(r.content) < 1_500_000:
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            if mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                                    "data": base64.b64encode(r.content).decode()}}
    except Exception as e:
        log("thumb", e)
    return None


def ask(items):
    """items を 1 回で送る。返り値：(結果のリスト, 入力トークン, 出力トークン)。失敗したら (None, 0, 0)"""
    content = []
    for it in items:
        info = {"id": it["id"], "title": it["name"], "shop": it["shop"], "category": it["category"],
                "price": it.get("price"), "likes": it["likes"], "images": it["nimg"], "desc": (it["descr"] or "")[:300]}
        content.append({"type": "text", "text": json.dumps(info, ensure_ascii=False)})
        if not it["adult"]:                     # 成人向けの画像は送らない
            im = thumb(it["thumb_url"])
            if im:
                content.append({"type": "text", "text": f"↑ id={it['id']} のサムネイル："})
                content.append(im)
    content.append({"type": "text", "text": PROMPT})
    body = {"model": A["model"], "max_tokens": 4000, "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "record_items"},
            "messages": [{"role": "user", "content": content}]}
    # キーは貼った時の改行・空白を消して使う
    headers = {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", "").strip(), "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(API, headers=headers, json=body, timeout=180)
        except (requests.ConnectionError, requests.Timeout) as e:
            log("Claude 接続エラー", e)
            r = None
        except Exception as e:                 # キーの形がおかしい など。やり直しても直らない
            log("Claude", e)
            return None, 0, 0
        if r is not None and r.status_code == 200:
            j = r.json()
            u = j.get("usage") or {}
            for block in j.get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "record_items":
                    return block.get("input", {}).get("items", []), u.get("input_tokens", 0), u.get("output_tokens", 0)
            log("Claude：結果の形が想定と違いました")
            return None, u.get("input_tokens", 0), u.get("output_tokens", 0)
        if r is not None and r.status_code not in (429, 500, 502, 503, 529):
            log("Claude", r.status_code, r.text[:300])
            return None, 0, 0
        wait = 20 * (attempt + 1)
        log("Claude", r.status_code if r is not None else "接続エラー", f"{wait} 秒待ってやり直し")
        time.sleep(wait)
    return None, 0, 0


def main():
    dry = "--dry-run" in sys.argv
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY がないので、AI の判定はしません")
        return
    db = D1()
    limit = A["batch"] if dry else A["per_run"]
    todo = db.q(
        "SELECT i.id, i.name, i.shop, i.category, i.thumb_url, i.likes, i.nimg, i.descr, i.adult, "
        "(SELECT price FROM prices p WHERE p.item_id = i.id ORDER BY seen_at DESC LIMIT 1) AS price "
        "FROM items i LEFT JOIN ai ON ai.item_id = i.id "
        "WHERE i.detail_at IS NOT NULL AND ai.item_id IS NULL ORDER BY i.first_seen DESC LIMIT ?", [limit])
    if not todo:
        log("判定する商品はありません")
        return
    t = now_min()
    tin = tout = done = 0
    for i in range(0, len(todo), A["batch"]):
        group = todo[i:i + A["batch"]]
        res, a, b = ask(group)
        tin, tout = tin + a, tout + b
        if res is None:
            log("この回の判定はここで止めます（次の回にやり直し）")
            break
        ids = {it["id"] for it in group}
        rows = []
        for r_ in res:
            if not isinstance(r_, dict) or r_.get("id") not in ids:
                continue
            avatars = [str(x).strip() for x in (r_.get("avatars") or []) if str(x).strip()][:40]
            rows.append((r_["id"], r_.get("genre", "other"), int(r_.get("q") or 3), (r_.get("sum") or "")[:24],
                         json.dumps(avatars, ensure_ascii=False), r_.get("fit", "other"), r_.get("kind", "new"),
                         A["model"], t))
        if dry:
            for row in rows:
                log(row)
            break
        db.insert_many("INSERT OR REPLACE INTO ai (item_id, genre, q, sum, avatars, fit, kind, model, judged_at)", 9, rows)
        done += len(rows)
    cost = tin / 1e6 * A["price_in"] + tout / 1e6 * A["price_out"]
    log(f"判定：{done} 件　トークン 入力 {tin:,} / 出力 {tout:,}　（約 ${cost:.4f}）")


if __name__ == "__main__":
    main()
