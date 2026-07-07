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

1. 広告スクショとNotion本文を読み込む
2. 表紙を生成
3. `広告分析①` と `ビジネスモデル` を交互に生成
4. 最後にプロフィール誘導を生成
5. LinkedIn投稿用PDFも生成

出力:

- `deliverables/carousel_test/slide_*.png`
- `deliverables/carousel_test/linkedin_carousel.pdf`

カルーセル構成:

- 広告1件: 表紙 + 広告分析① + ビジネスモデル + プロフィール誘導 = 4枚
- 広告2件: 6枚
- 広告3件: 8枚
- 広告4件: 10枚

最大10枚に収めるため、広告は最大4件まで使います。足りない広告ページや空ページは追加しません。

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
4. ページ画像を無音MP4へ変換
5. `thumbnail.png` を生成
6. MixkitからランダムBGMを取得。失敗時は保存済みBGMへフォールバック
7. MP4へBGMを合成

出力:

- `deliverables/reels/pages/*.png`
- `deliverables/reels/thumbnail.png`
- `deliverables/reels/structured_reel_no_bgm.mp4`
- `deliverables/reels/structured_reel.mp4`
- Macローカル: `~/Desktop/Facebook個人投稿用/<run_id>_facebook_personal_reel_XX.mp4`
- Macローカル: `~/Desktop/Facebook個人投稿用/<run_id>_facebook_personal_caption_XX.txt`
- GitHub Actions: `deliverables/facebook_manual/<run_id>_facebook_personal_reel_XX.mp4`
- GitHub Actions: `deliverables/facebook_manual/<run_id>_facebook_personal_caption_XX.txt`

## BGM合成

現在はランダムBGMを標準で合成しています。

素材:

- 通常: `https://mixkit.co/free-stock-music/`
- フォールバック: `assets/audio/mixkit_fallback/*.mp3`

固定仕様は [reel-short-video-template.md](reel-short-video-template.md) を正とします。

取得したBGMは動画尺に合わせてトリムし、`loudnorm` で曲ごとの体感音量を揃えた上でBGM用に控えめの音量へ調整し、冒頭と末尾だけ軽くフェードします。曲名・作者・URLのメモファイルや専用ログは作りません。

## 生成物の掃除

日次自動投稿の計画作成前に `cleanup_generated_assets.py` を実行します。標準では7日より古い次の生成物を削除します。

- `deliverables/auto_post/`
- `public_state/public/runs/`
- `public_state/public/manual_tests/`
- `deliverables/facebook_manual/` の履歴ファイル

Facebook個人手動投稿用の動画とキャプションも、作成から7日より古くなれば削除します。

保持日数は `GENERATED_ASSET_RETENTION_DAYS` で変更できます。

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

## テキスト投稿の分散

X、Threads、Facebookページのテキスト投稿は、1広告1投稿として最大2本まで投稿します。

投稿枠:

- 1本目: `12:00`
- 2本目: `18:00`

同じセクションはX、Threads、Facebookページへ同じタイミングで投稿します。媒体ごとにはずらしません。

画像・動画系は別枠で実行します。

- `12:00`: 1本目をX/Threads/Facebookへ投稿
- `16:00`: 1本目のInstagram/LinkedIn/YouTube画像・動画系を投稿
- `18:00`: 2本目をX/Threads/Facebookへ投稿
- `20:30`: 2本目のInstagram/LinkedIn/YouTube画像・動画系を投稿

同じ時刻内に複数タスクがある場合のみ、枠内でセクションごとに1分ずつずらします。

Threadsの日次投稿はNotion本文をそのまま使います。Instagram/LinkedInカルーセル投稿のキャプションもNotion本文を使います。Reels/Shortsなど動画系は `勝ち広告を分析してみましたvol.{投稿番号}` を使います。キャプションには引用元を空行区切りで追加します。

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
