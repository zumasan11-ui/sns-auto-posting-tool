# 実行フロー

## カルーセル画像生成フロー

```bash
python carousel_generator.py \
  --image /path/to/ad.png \
  --text "分析文" \
  --output-dir deliverables/carousel_test \
  --count 10
```

処理:

1. 広告スクショを読み込む
2. 分析文からスライド本文を生成
3. `slide_01.png` から `slide_10.png` を生成
4. LinkedIn投稿用PDFも生成

出力:

- `deliverables/carousel_test/slide_*.png`
- `deliverables/carousel_test/linkedin_carousel.pdf`

## Reels動画生成フロー

```bash
python reels_generator.py \
  --structured-reel \
  --ad-images /path/to/ad1.png /path/to/ad2.png \
  --ad-text "広告分析文" \
  --business-text "ビジネスモデル分析文" \
  --cover-title "なぜこの広告は◯ヶ月回っているのか？" \
  --pages-dir deliverables/reels/pages \
  --output deliverables/reels/structured_reel.mp4 \
  --font-style mincho \
  --cover-font-style noto \
  --cover-duration 1.5 \
  --slide-duration 3 \
  --transition none
```

処理:

1. 表紙ページを生成
2. 広告ページを生成
3. ビジネスモデルページを生成
4. ページ画像をMP4へ変換
5. 必要に応じてBGMを合成

出力:

- `deliverables/reels/pages/*.png`
- `deliverables/reels/structured_reel_no_fade.mp4`
- `deliverables/reels/structured_reel.mp4`

## BGM合成

現在は仮BGMをローカル生成して合成しています。

生成物:

- `deliverables/reels/bgm_upbeat.wav`

本番では、権利クリア済み音源を使ってffmpegで差し替えます。

## Instagramカルーセル投稿フロー

```bash
python carousel_poster.py instagram \
  --base-url "https://example.com/slides" \
  --caption "投稿文" \
  --count 10
```

前提:

- `slide_01.png` から `slide_10.png` が公開HTTPS URLで取得できる

処理:

1. 各画像の子コンテナを作成
2. 親CAROUSELコンテナを作成
3. `media_publish`
4. `sns_posts/instagram_carousel_last.json` に結果保存

## Instagram Reels投稿フロー

```bash
python reels_generator.py --post \
  --video-url "https://example.com/reel.mp4" \
  --caption "投稿文"
```

前提:

- MP4が公開HTTPS URLで取得できる

処理:

1. Reelsコンテナ作成
2. コンテナ処理完了待ち
3. `media_publish`
4. `sns_posts/instagram_reel_last.json` に結果保存

## X予約投稿フロー

予約:

```bash
python main.py --platform x --schedule-at "2026-06-29 09:00" -t "投稿文"
```

実行:

```bash
python main.py --run-due
```

保存:

- `sns_posts/x_queue.json`
- `sns_posts/x_post_log.jsonl`

## Notion API読み書きフロー

スキーマ確認:

```bash
python notion_api.py schema
```

一覧取得:

```bash
python notion_api.py list --limit 10
```

作成:

```bash
python notion_api.py create \
  --properties-json '{"Name":{"title":[{"text":{"content":"投稿案"}}]}}' \
  --body "投稿本文メモ"
```

更新:

```bash
python notion_api.py update \
  --page-id PAGE_ID \
  --properties-json '{"Post URL":{"url":"https://example.com/post"}}'
```

前提:

- `.env` に `NOTION_TOKEN` と `NOTION_DATABASE_ID` が入っている
- 対象Notionデータベースにインテグレーションを招待済み

処理:

1. Notion APIでデータベーススキーマを取得
2. Notion APIでページを取得または作成
3. 投稿完了後などにページプロパティを書き戻す
