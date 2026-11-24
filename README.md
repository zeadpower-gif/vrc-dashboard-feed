# vrc-dashboard-feed

コンテナハウスのダッシュボード用データ（天気・Yahoo!ニュース・VRChat の新作／セール・ニュース）を
GitHub Actions が1時間ごとに集めて `docs/feed.json` に書き出します。GitHub Pages で公開して、ワールドから読み込みます。

## はじめにやること
1. GitHub で新しいリポジトリ `vrc-dashboard-feed` を作る（Public）。このフォルダの中身を全部アップロード
2. Gemini の API キー：Google AI Studio（https://aistudio.google.com/apikey）で作成
   → リポジトリの Settings → Secrets and variables → Actions → New repository secret
   名前 `GEMINI_API_KEY`、値にキーを貼る
3. Settings → Pages → Source「Deploy from a branch」、Branch `main` / フォルダ `/docs` → Save
4. Actions タブ → update-feed → Run workflow（1回手動で動かす）
5. `https://ユーザー名.github.io/vrc-dashboard-feed/feed.json` が開けたら、Unity の CH_Dashboard の Feed Url に入れる

## 設定（config.json）
- `cities`：主要都市（ワールドの天気で選べる）
- `my_cities`：自分で足す地域（★が付く）。名前・緯度・経度を書く
- `yahoo`：ニュースのカテゴリ（Yahoo!ニュースの RSS）
- `booth`：BOOTH のカテゴリ名（アバター／衣装／ワールド／ギミック）
- `booth_hours`：BOOTH を見に行く間隔（時間）。それ以外の時は前回の結果を使う
- `min_quality`：これより低いクオリティ（1〜5）の商品は出さない。AI がサムネイル・説明・価格・いいね数で判定
- `min_likes`：いいね数がこれより少ない商品は出さない（0 なら制限なし）
- `ai_sale_search`：Gemini の Google 検索でセールを探す（6時間に1回）
