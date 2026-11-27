# Cloudflare D1 を HTTP API で読み書きする小さな道具
#   必要な環境変数（GitHub の Secret）：CF_ACCOUNT_ID / CF_D1_DATABASE_ID / CF_API_TOKEN
import os
import time

import requests

MAX_PARAMS = 90   # D1 は 1 回の問い合わせに ? を 100 個までしか使えないので少し余裕を持たせる


class D1:
    def __init__(self):
        acc = os.environ.get("CF_ACCOUNT_ID", "").strip()
        dbid = os.environ.get("CF_D1_DATABASE_ID", "").strip()
        token = os.environ.get("CF_API_TOKEN", "").strip()
        if not (acc and dbid and token):
            raise RuntimeError("CF_ACCOUNT_ID / CF_D1_DATABASE_ID / CF_API_TOKEN が設定されていません")
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{dbid}/query"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def q(self, sql, params=None):
        """SQL を 1 つ実行して、結果の行（dict のリスト）を返す"""
        for attempt in range(4):
            try:
                r = requests.post(self.url, headers=self.headers,
                                  json={"sql": sql, "params": list(params or [])}, timeout=60)
            except Exception as e:
                r, err = None, str(e)
            if r is not None and r.status_code == 200:
                j = r.json()
                if j.get("success"):
                    res = j.get("result") or [{}]
                    return res[0].get("results") or []
                raise RuntimeError(f"D1 エラー: {j.get('errors')}  SQL: {sql[:120]}")
            if r is not None and r.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"D1 HTTP {r.status_code}: {r.text[:300]}")
            time.sleep(3 * (attempt + 1))
        raise RuntimeError("D1 に何度やってもつながりませんでした")

    def insert_many(self, head, ncols, rows, tail=""):
        """head = 'INSERT INTO t (a,b)' のように VALUES の前まで。rows を ? の上限に合わせて分けて入れる"""
        per = max(1, MAX_PARAMS // ncols)
        one = "(" + ",".join("?" * ncols) + ")"
        for i in range(0, len(rows), per):
            chunk = rows[i:i + per]
            self.q(f"{head} VALUES {','.join([one] * len(chunk))} {tail}", [v for row in chunk for v in row])

    def select_in(self, sql_with_in, ids, extra_params=()):
        """'... WHERE id IN ({}) ...' の {} に ? を並べて、ids を分けて問い合わせる"""
        out = []
        ids = list(ids)
        per = MAX_PARAMS - len(extra_params)
        for i in range(0, len(ids), per):
            chunk = ids[i:i + per]
            out += self.q(sql_with_in.format(",".join("?" * len(chunk))), list(chunk) + list(extra_params))
        return out

    def get_state(self, key, default=None):
        rows = self.q("SELECT value FROM state WHERE key = ?", [key])
        return rows[0]["value"] if rows else default

    def set_state(self, key, value):
        self.q("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               [key, str(value)])
