-- BOOTH セール情報データベース（Cloudflare D1）
-- D1 の画面の「Console」にこのファイルの中身を貼って実行する（何度実行しても大丈夫）
-- 時刻はすべて「1970 年からの分（UTC）」。feed.json の m と同じ

-- 商品 1 つ = 1 行
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,            -- BOOTH の商品 ID
  name TEXT,
  shop TEXT,
  shop_url TEXT,
  category TEXT,
  thumb_url TEXT,                 -- 画像は保存しない。URL だけ
  likes INTEGER,
  descr TEXT,                     -- 説明の先頭 400 文字
  nimg INTEGER,                   -- 画像の枚数
  adult INTEGER DEFAULT 0,        -- 成人向けなら 1（Discord には流さない）
  price_varies INTEGER DEFAULT 0, -- バリエーションで値段が違う（「¥ 200~」）なら 1
  src TEXT,                       -- 最初に見つけた場所（new / sale）
  first_seen INTEGER,
  last_seen INTEGER,
  detail_at INTEGER               -- 商品 JSON で詳しく読んだ時刻（まだなら NULL）
);
CREATE INDEX IF NOT EXISTS items_detail ON items (detail_at, first_seen);

-- 値段が変わった瞬間 1 つ = 1 行（前回と同じ値段なら書かない）
CREATE TABLE IF NOT EXISTS prices (
  item_id TEXT,
  price INTEGER,                  -- 円（バリエーションがある時は一番安い値段）
  seen_at INTEGER,
  PRIMARY KEY (item_id, seen_at)
);

-- AI（Claude）の判定 1 つ = 1 行（② で使う）
CREATE TABLE IF NOT EXISTS ai (
  item_id TEXT PRIMARY KEY,
  genre TEXT,                     -- avatar / outfit / world / gimmick / other
  q INTEGER,                      -- 品質 1〜5
  sum TEXT,                       -- ひとこと紹介
  avatars TEXT,                   -- 対応アバター（JSON の配列）
  fit TEXT,                       -- body（衣装など）/ head（髪型・メイクなど）/ other
  model TEXT,
  judged_at INTEGER
);

-- Discord への投稿 1 つ = 1 行（④ で使う。同じものを二重に流さないため）
CREATE TABLE IF NOT EXISTS posts (
  item_id TEXT,
  kind TEXT,                      -- new / drop
  price INTEGER,
  channel TEXT,                   -- outfit / haruka / kanata
  posted_at INTEGER,
  PRIMARY KEY (item_id, kind, price, channel)
);

-- 前回いつ動いたか など
CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT
);
