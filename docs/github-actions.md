# GitHub Actions移行

このプロジェクトは `.github/workflows/daily-sns-auto-post.yml` で日次自動投稿を実行します。

## 実行スケジュール

- 日本時間 05:00: Notionの対象ページ取得、Statusを `進行中` へ更新、カルーセル/リール生成、公開アセット作成
- 日本時間 07:30 / 12:00 / 16:00 / 19:30: 投稿計画の該当スロットを実投稿
- 手動実行: GitHub Actionsの `Daily SNS Auto Post` から `run_now=true`

GitHub Actionsは長時間待機に向かないため、5:00のジョブで1日の計画を作成し、投稿時刻ごとのcronで続きの処理を行います。計画と公開アセットは `gh-pages` ブランチに保存します。

## 投稿対象

Notionデータベースから `Status` が `進行中`、`エラー`、`未投稿` の順で最も古いページを探します。通常は `未投稿` が対象です。失敗時は `エラー` として残るため、次回実行で同じページを再処理できます。

## 必須GitHub Secrets

`.env.example` と同じ値をGitHub Secretsへ登録します。サービスアカウントJSONはファイルではなく、JSON本文を `GOOGLE_SHEETS_CREDENTIALS_JSON` に登録します。

最低限必要なSecrets:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `GOOGLE_SHEETS_CREDENTIALS_JSON`
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `PUBLIC_ASSET_BASE_URL`
- X: `API_KEY`, `API_SECRET`, `ACCESS_TOKEN`, `ACCESS_TOKEN_SECRET`
- Threads: `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`
- Instagram/Meta: `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`, `META_APP_ID`, `META_APP_SECRET`
- Facebook: `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_ACCESS_TOKEN`, `FACEBOOK_USER_ACCESS_TOKEN`
- LinkedIn: `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_REFRESH_TOKEN`, `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_PERSON_URN`
- YouTube: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`, `YOUTUBE_REDIRECT_URI`

任意Secrets:

- `NOTION_STATUS_PROPERTY`: 既定値 `Status`
- `NOTION_ERROR_PROPERTY`: 既定値 `エラー内容`
- 各 `*_EXPIRES_AT`, `*_LAST_REFRESHED_AT`: トークン更新判定用

## GitHub Pages

Instagram/Threads/Facebookの画像・動画投稿には、Meta側から取得できる公開HTTPS URLが必要です。このため、生成画像/動画を `gh-pages` ブランチへ保存し、GitHub Pagesで公開します。

リポジトリ作成後、Pagesのソースを `gh-pages` ブランチに設定してください。`PUBLIC_ASSET_BASE_URL` は次の形式です。

```env
PUBLIC_ASSET_BASE_URL=https://OWNER.github.io/REPOSITORY
```

## 手動テスト

Actions画面から `Daily SNS Auto Post` を選び、`Run workflow` で `run_now=true` を指定します。これにより対象ページの取得、生成、全SNS投稿、Sheets追記、Notion更新まで即時実行します。

ローカルで生成部分だけ確認する場合:

```bash
python daily_auto_post.py --prepare
```

ローカルで即時投稿まで実行する場合:

```bash
python daily_auto_post.py --prepare --run-now
```
