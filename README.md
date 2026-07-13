# SNS自動投稿ツール

広告分析コンテンツを、SNS向けのカルーセル画像、LinkedIn PDF、Instagram Reels動画に変換し、各SNSへ投稿するためのPythonツール群です。

## できること

- 広告スクショからInstagram/LinkedIn向けカルーセル画像を生成
- カルーセル画像からLinkedIn投稿用PDFを生成
- 広告画像と分析文からInstagram Reels用MP4を生成
- Instagramカルーセル投稿、Instagram Reels投稿
- YouTube Shorts投稿
- Notion APIによるデータベース読み書き
- Google Sheets APIによるスプレッドシート読み書き
- X、Threads、Instagram、Facebookページ、LinkedInへの投稿
- GitHub Actionsによる毎日自動投稿
- Xの予約投稿
- Threads/LinkedIn/YouTube OAuth補助
- Threads/Meta/LinkedIn/YouTubeアクセストークン自動更新
- Instagram Business Account ID取得補助
- 自動投稿生成物の7日保持・自動削除

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` に必要なAPIキーとアクセストークンを入れてください。

詳細は [docs/environment.md](docs/environment.md) を参照してください。

## 主要コマンド

### カルーセル画像生成

```bash
python carousel_generator.py \
  --image /path/to/ad.png \
  --text "分析文" \
  --output-dir deliverables/carousel_test \
  --count 10
```

### Instagram Reels動画生成

```bash
python reels_generator.py \
  --structured-reel \
  --ad-images /path/to/ad1.png /path/to/ad2.png \
  --ad-text "広告分析文" \
  --business-text "ビジネスモデル分析文" \
  --cover-title "なぜこの広告は◯ヶ月回っているのか？" \
  --output deliverables/reels/structured_reel.mp4 \
  --cover-duration 1.5 \
  --slide-duration 3 \
  --transition none
```

Reels/Shorts生成時は、表紙画像を `thumbnail.png` として保存します。YouTube Shorts投稿ではこの画像を自動でサムネ設定し、Instagram Reelsでは動画先頭1.5秒の表紙をサムネ/プレビューとして使います。Instagram Reelsはフィードへ共有せず、Instagramフィード面はカルーセル投稿だけにします。個人Facebookへ手動投稿するため、同じ動画とコピペ用キャプションを `deliverables/facebook_manual/` にも保存します。

### Instagramカルーセル投稿

```bash
python carousel_poster.py instagram \
  --base-url "https://example.com/slides" \
  --caption "投稿文" \
  --count 10
```

### Instagram Reels投稿

```bash
python reels_generator.py --post \
  --video-url "https://example.com/reel.mp4" \
  --caption "投稿文"
```

Instagram Graph APIではローカルMP4を直接投稿できません。Meta側から取得できる公開HTTPS URLが必要です。
標準では `share_to_feed=false` で投稿し、ReelsをInstagramフィードへ共有しません。

### LinkedIn PDF投稿

```bash
python carousel_poster.py linkedin \
  --pdf deliverables/carousel_test/linkedin_carousel.pdf \
  --text "投稿文" \
  --title "広告クリエイティブ改善メモ"
```

### YouTube Shorts投稿

初回だけOAuth認証を行い、`YOUTUBE_REFRESH_TOKEN` を `.env` に保存します。

```bash
python youtube_oauth.py
```

ローカルMP4をYouTubeへアップロードします。

```bash
python youtube_poster.py \
  --video deliverables/reels/structured_reel.mp4 \
  --title "広告クリエイティブ改善メモ #Shorts" \
  --description "概要欄テキスト" \
  --tags 広告 マーケティング Shorts
```

設定手順は [docs/youtube.md](docs/youtube.md) を参照してください。

### テキスト投稿

```bash
python main.py --platform x -t "投稿文"
python main.py --platform threads -t "投稿文"
python main.py --platform instagram --image-url "https://example.com/image.png" -t "投稿文"
python main.py --platform facebook -t "投稿文"
python main.py --platform linkedin -t "投稿文"
python main.py --platform youtube --video deliverables/reels/structured_reel.mp4 --title "タイトル #Shorts" -t "概要欄"
```

### Facebook個人用の手動投稿動画

Facebook個人アカウントはAPIから安定した自動投稿ができないため、手動投稿用の動画だけを毎回保存します。Macローカル実行時はデスクトップの `Facebook個人投稿用` フォルダへ保存します。

- Macローカル動画: `~/Desktop/Facebook個人投稿用/<run_id>_facebook_personal_reel_XX.mp4`
- Macローカルキャプション: `~/Desktop/Facebook個人投稿用/<run_id>_facebook_personal_caption_XX.txt`
- GitHub Actions動画: `deliverables/facebook_manual/<run_id>_facebook_personal_reel_XX.mp4`
- GitHub Actionsキャプション: `deliverables/facebook_manual/<run_id>_facebook_personal_caption_XX.txt`
- GitHub Actions実行時も専用フォルダへ保存します

写真アプリへも取り込みたい場合だけ、Macローカル実行時に次を設定します。

```env
IMPORT_FACEBOOK_MANUAL_TO_PHOTOS=1
```

### 生成物の自動削除

自動投稿で作る画像・動画・公開アセットは放置すると増えるため、日次処理の最初に7日より古い生成物を削除します。

- `deliverables/auto_post/`: 自動投稿用のカルーセル画像、PDF、Reels動画
- `public_state/public/runs/`: SNSが取得する公開アセット
- `public_state/public/manual_tests/`: 手動テスト用の公開アセット
- `deliverables/facebook_manual/`: GitHub Actions上のFacebook個人手動投稿用動画/キャプション
- `~/Desktop/Facebook個人投稿用/`: Macローカル実行時のFacebook個人手動投稿用動画/キャプション

Facebook個人用の動画とキャプションも、作成から7日より古くなれば削除します。保存先を変える場合は `FACEBOOK_MANUAL_DIR`、保持日数を変える場合は次を設定します。

```env
GENERATED_ASSET_RETENTION_DAYS=7
```

手動で確認する場合:

```bash
python cleanup_generated_assets.py --dry-run
```

### Notion API連携

ブラウザ操作ではなくNotion APIでデータベースを読み書きします。

```bash
python notion_api.py schema
python notion_api.py list --limit 10
```

設定手順は [docs/notion.md](docs/notion.md) を参照してください。

### アクセストークン更新

更新可能なSNSトークンを更新し、`.env` へ保存します。

```bash
python token_refresh.py status
python token_refresh.py all
python token_refresh.py youtube --force
```

Threads、Instagram、Facebook、LinkedIn、YouTubeの投稿前にも自動更新を試みます。更新できない場合だけ再認証が必要です。

GitHub Actionsでは投稿がない日でも `token_refresh.py all` を実行し、期限が近いトークンを可能な限り自動更新してGitHub Secretsへ書き戻します。PCの電源が切れていても長期間放置できる構成を優先します。

### Google Sheets API連携

サービスアカウントJSONでGoogle Sheets APIに接続し、スプレッドシートを読み書きします。

```bash
python sheets_api.py meta
python sheets_api.py read --range "Sheet1!A1:D10"
python sheets_api.py append --range "Sheet1!A:D" --values-json '[["2026-06-29","投稿案","未着手"]]'
```

設定手順は [docs/sheets.md](docs/sheets.md) を参照してください。

### GitHub Actions日次自動投稿

```bash
python daily_auto_post.py --prepare
python daily_auto_post.py --execute --slot "12:00"
python daily_auto_post.py --prepare --run-now
```

GitHub Actionsでは日本時間5:00に投稿計画と画像/動画を生成し、媒体ごとの固定時刻に投稿します。1日に処理する広告は、`状態=済み` の最も古いNotionページ1つだけです。手動テストはActionsの `Daily SNS Auto Post` から `run_now=true` で実行できます。

- 日本時間 08:00: LinkedIn
- 日本時間 12:00: X、Instagramフィード、Facebookページ
- 日本時間 18:30: TikTok
- 日本時間 19:00: Instagramリール
- 日本時間 19:30: YouTubeショート
- 日本時間 20:00: Threads

Instagram/LinkedInカルーセルとReels/Shortsなど動画系のキャプションは `広告分析vol.` から始まる共通形式を使います。6個目以降の学びがある場合はキャプションに続きとして入れます。

詳細は [docs/github-actions.md](docs/github-actions.md) を参照してください。

### Meta広告リサーチ

Meta広告ライブラリの広告収集はApifyなしで、Mac上のブラウザ操作として実行します。Codexに「リサーチして」と指示した時だけ、`キーワードDB` の横向きジャンル表と `検索履歴DB` の実績を使って検索語を選び、`広告分析マスターDB` に1広告を直接追加します。作成中の行は黄色にし、投稿完了後に分析情報を追記して色を消します。Notion日次ページは1ページにつき1広告で作成します。

詳細は [docs/meta-ad-library-manual-extractor.md](docs/meta-ad-library-manual-extractor.md) を参照してください。

## ドキュメント

- [docs/project-rules.md](docs/project-rules.md): このプロジェクトの保守ルール
- [docs/environment.md](docs/environment.md): 環境変数とAPI設定
- [docs/oauth.md](docs/oauth.md): OAuth/認証手順
- [docs/notion.md](docs/notion.md): Notion API連携
- [docs/sheets.md](docs/sheets.md): Google Sheets API連携
- [docs/youtube.md](docs/youtube.md): YouTube Shorts投稿設定
- [docs/tiktok.md](docs/tiktok.md): TikTok Direct Post設定
- [docs/templates.md](docs/templates.md): テンプレート構成
- [docs/reel-short-video-template.md](docs/reel-short-video-template.md): Instagram Reels/YouTube Shorts動画テンプレートの固定ルール
- [docs/workflows.md](docs/workflows.md): 実行フロー
- [docs/sns-posting.md](docs/sns-posting.md): 各SNS投稿の仕組み
- [docs/github-actions.md](docs/github-actions.md): GitHub Actions移行とSecrets設定
- [docs/meta-ad-library-manual-extractor.md](docs/meta-ad-library-manual-extractor.md): Meta広告ライブラリ収集と検索DB育成ルール

## 開発ルール

新しい機能を追加したら、必ずドキュメントも更新してください。

- 設計意図
- 使い方
- 必要な依存ライブラリ
- 必要な環境変数
- 実行コマンド
- 生成物の保存先
- SNS投稿までの流れ

チャット履歴がなくなっても、このリポジトリだけ見れば保守・改修できる状態を維持します。

Instagram Reels/YouTube Shortsの動画は [docs/reel-short-video-template.md](docs/reel-short-video-template.md) を正とします。新しいチャットで動画や表紙を作る場合も、この仕様と `reels_generator.py` の `REEL_*` 定数に従ってください。
