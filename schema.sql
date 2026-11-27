-- BOOTH セール情報データベース（Cloudflare D1）
-- D1 の画面の「Console」に、下の CREATE の行を 1 行ずつ貼って実行する（何度実行しても大丈夫）
-- 列の意味は計画書の「データベースの表の設計」を参照

CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, name TEXT, shop TEXT, shop_url TEXT, category TEXT, thumb_url TEXT, likes INTEGER, descr TEXT, nimg INTEGER, adult INTEGER DEFAULT 0, price_varies INTEGER DEFAULT 0, src TEXT, first_seen INTEGER, last_seen INTEGER, detail_at INTEGER);
CREATE INDEX IF NOT EXISTS items_detail ON items (detail_at, first_seen);
CREATE TABLE IF NOT EXISTS prices (item_id TEXT, price INTEGER, seen_at INTEGER, PRIMARY KEY (item_id, seen_at));
CREATE TABLE IF NOT EXISTS ai (item_id TEXT PRIMARY KEY, genre TEXT, q INTEGER, sum TEXT, avatars TEXT, fit TEXT, kind TEXT, model TEXT, judged_at INTEGER);
CREATE TABLE IF NOT EXISTS posts (item_id TEXT, kind TEXT, price INTEGER, channel TEXT, posted_at INTEGER, PRIMARY KEY (item_id, kind, price, channel));
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
