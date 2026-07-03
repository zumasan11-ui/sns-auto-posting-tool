# 各SNS投稿の仕組み

## 投稿入口

主な入口:

- `main.py`: テキスト/画像投稿
- `carousel_poster.py`: Instagramカルーセル、LinkedIn PDF、YouTube Shorts投稿
- `reels_generator.py`: Reels生成、Reels投稿

## X

実装:

- `main.py`
- `tweepy.Client.create_tweet`

対応:

- テキスト即時投稿
- テキスト予約投稿

コマンド:

```bash
python main.py --platform x -t "投稿文"
```

## Threads

実装:

- `main.py`
- Threads Graph API

対応:

- テキスト即時投稿
- 日次自動投稿では本文先頭に `広告分析` を付ける

コマンド:

```bash
python main.py --platform threads -t "投稿文"
```

## Instagram画像投稿

実装:

- `main.py`
- Instagram Graph API

対応:

- 画像URL + キャプション投稿

コマンド:

```bash
python main.py --platform instagram \
  --image-url "https://example.com/image.png" \
  -t "投稿文"
```

注意:

- `--image-url` は公開HTTPS URLが必要

## Instagramカルーセル投稿

実装:

- `carousel_poster.py`

キャプション:

- 日次自動投稿では `広告分析` に固定
- 引用元が抽出できる場合は、タイトル行の下に空行を入れて `引用元：...` も追加する

標準構成:

- 表紙
- `広告分析①` / `ビジネスモデル` の交互ページ
- 最大 `広告分析④` まで
- 最後にプロフィール誘導
- 広告1件なら4枚、広告4件なら10枚

コマンド:

```bash
python carousel_poster.py instagram \
  --base-url "https://example.com/slides" \
  --caption "投稿文" \
  --count 10
```

結果:

- `sns_posts/instagram_carousel_last.json`

## Instagram Reels投稿

実装:

- `reels_generator.py`

キャプション:

- 日次自動投稿では動画系として `勝ち広告を分析してみました` から始まる既存キャプションを使う

コマンド:

```bash
python reels_generator.py --post \
  --video-url "https://example.com/reel.mp4" \
  --caption "投稿文"
```

注意:

- Graph API投稿には公開HTTPSのMP4 URLが必要
- ローカルファイルを直接APIへ投稿することはできない

## Facebookページ

実装:

- `main.py`
- Meta Graph API

対応:

- テキスト投稿
- 画像投稿

コマンド:

```bash
python main.py --platform facebook -t "投稿文"
python main.py --platform facebook --image-url "https://example.com/image.png" -t "投稿文"
```

## LinkedInテキスト投稿

実装:

- `main.py`
- LinkedIn UGC Posts API

コマンド:

```bash
python main.py --platform linkedin -t "投稿文"
```

## LinkedIn PDF投稿

実装:

- `carousel_poster.py`

Instagramカルーセルと同じ画像構成をPDF化して投稿します。
- LinkedIn Documents API
- LinkedIn Posts API

コマンド:

```bash
python carousel_poster.py linkedin \
  --pdf deliverables/carousel_test/linkedin_carousel.pdf \
  --text "投稿文" \
  --title "広告クリエイティブ改善メモ"
```

## YouTube Shorts投稿

実装:

- `youtube_oauth.py`
- `youtube_poster.py`
- `carousel_poster.py`
- `main.py`
- YouTube Data API v3

コマンド:

```bash
python youtube_oauth.py
python youtube_poster.py \
  --video deliverables/reels/structured_reel.mp4 \
  --title "広告クリエイティブ改善メモ #Shorts" \
  --description "概要欄テキスト" \
  --tags 広告 マーケティング Shorts \
  --thumbnail deliverables/reels/thumbnail.png
```

`main.py` から投稿する場合:

```bash
python main.py --platform youtube \
  --video deliverables/reels/structured_reel.mp4 \
  --title "広告クリエイティブ改善メモ #Shorts" \
  -t "概要欄テキスト"
```

結果:

- `https://www.youtube.com/shorts/...`

日次自動投稿では、Reels/Shorts生成時の `thumbnail.png` をYouTube Shortsのカスタムサムネとして自動設定します。Shorts処理後の反映漏れを避けるため、アップロード直後、45秒後、180秒後に同じサムネを再設定します。Instagram Reelsは動画先頭1.5秒の表紙をサムネ/プレビューとして使い、`share_to_feed=false` でフィードへは共有しません。Instagramフィード面はカルーセル投稿だけにします。

Facebook個人アカウントはAPI自動投稿対象外です。Reels生成時に同じ動画とコピペ用キャプションを保存します。Macローカル実行時はFinderで見つけやすい `~/Desktop/Facebook個人投稿用/`、GitHub Actions実行時は `deliverables/facebook_manual/` へ保存します。`latest` ファイルは作りません。

Facebook個人用の動画とキャプションは、7日より古くなると `cleanup_generated_assets.py` で自動削除します。

## Threads長文

Threadsは本文が500文字を超える場合、本文を500文字以内に分割して返信ツリーとして投稿します。画像がある場合は最初の投稿だけに付け、続きはテキスト返信として連ねます。
