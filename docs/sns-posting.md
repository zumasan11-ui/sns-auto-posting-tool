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
  --tags 広告 マーケティング Shorts
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
