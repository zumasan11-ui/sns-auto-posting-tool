# 環境変数とAPI設定

環境変数は `.env` に置きます。雛形は `.env.example` です。

## 共通

```bash
pip install -r requirements.txt
```

主な依存:

- `tweepy`: X投稿
- `python-dotenv`: `.env` 読み込み
- `requests`: Meta/LinkedIn API
- `pillow`: 画像生成
- `numpy`: 動画フレーム生成
- `imageio-ffmpeg`: ffmpegバイナリ取得
- `google-auth`: Google OAuth認証
- `google-auth-oauthlib`: YouTube初回OAuth認証
- `google-api-python-client`: YouTube Data API v3 / Google Sheets API

## X

必要な値:

```env
API_KEY=
API_SECRET=
ACCESS_TOKEN=
ACCESS_TOKEN_SECRET=
```

用途:

- `main.py --platform x`
- X予約投稿

注意:

- X Developer Portalでアプリ権限をRead and writeにする
- アクセストークンもRead and write権限で再発行する

## Threads

必要な値:

```env
THREADS_USER_ID=
THREADS_ACCESS_TOKEN=
THREADS_ACCESS_TOKEN_EXPIRES_AT=
THREADS_LAST_REFRESHED_AT=
THREADS_APP_ID=
THREADS_APP_SECRET=
THREADS_REDIRECT_URI=http://localhost:8765/callback
```

用途:

- `main.py --platform threads`
- `threads_oauth.py`

## Instagram

必要な値:

```env
INSTAGRAM_USER_ID=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT=
INSTAGRAM_LAST_REFRESHED_AT=
META_APP_ID=
META_APP_SECRET=
```

用途:

- Instagram画像投稿
- Instagramカルーセル投稿
- Instagram Reels投稿

注意:

- 投稿対象はInstagram Business/Creatorアカウント
- Instagram Graph APIでは、画像URLや動画URLは公開HTTPS URLである必要がある
- ローカルファイルを直接APIに渡すことはできない

## Facebookページ

必要な値:

```env
FACEBOOK_PAGE_ID=
FACEBOOK_PAGE_ACCESS_TOKEN=
FACEBOOK_USER_ACCESS_TOKEN=
FACEBOOK_USER_ACCESS_TOKEN_EXPIRES_AT=
FACEBOOK_PAGE_ACCESS_TOKEN_EXPIRES_AT=
FACEBOOK_LAST_REFRESHED_AT=
```

用途:

- `main.py --platform facebook`

## LinkedIn

必要な値:

```env
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=http://localhost:3000/callback
LINKEDIN_SCOPES=openid profile w_member_social offline_access
LINKEDIN_ACCESS_TOKEN=
LINKEDIN_ACCESS_TOKEN_EXPIRES_AT=
LINKEDIN_REFRESH_TOKEN=
LINKEDIN_REFRESH_TOKEN_EXPIRES_AT=
LINKEDIN_LAST_REFRESHED_AT=
LINKEDIN_PERSON_URN=
```

用途:

- `main.py --platform linkedin`
- LinkedIn PDF投稿
- `linkedin_oauth.py`

## YouTube

必要な値:

```env
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=http://localhost:8080/callback
YOUTUBE_REFRESH_TOKEN=
YOUTUBE_ACCESS_TOKEN=
YOUTUBE_ACCESS_TOKEN_EXPIRES_AT=
YOUTUBE_LAST_REFRESHED_AT=
```

用途:

- YouTube Shorts投稿
- `youtube_oauth.py`
- `youtube_poster.py`
- `main.py --platform youtube`

注意:

- Google Cloud Consoleで YouTube Data API v3 を有効化する
- OAuthスコープは `https://www.googleapis.com/auth/youtube.upload`
- 初回認証で取得した `YOUTUBE_REFRESH_TOKEN` を使って、次回以降は自動でアクセストークンを更新する
- 詳細は [youtube.md](youtube.md) を参照

## アクセストークン自動更新

手動で全サービスを更新する場合:

```bash
python token_refresh.py all
```

期限状態だけ見る場合:

```bash
python token_refresh.py status
```

特定サービスだけ強制更新する場合:

```bash
python token_refresh.py threads --force
python token_refresh.py instagram --force
python token_refresh.py facebook --force
python token_refresh.py linkedin --force
python token_refresh.py youtube --force
```

投稿前には `main.py` / `carousel_poster.py` / `youtube_poster.py` から自動更新を試みます。更新できない場合は既存トークンで続行し、期限切れで投稿できない時だけ再認証します。

注意:

- XのOAuth 1.0aアクセストークンは、このプロジェクトの構成では自動更新対象外です。
- Instagram/Facebookの長期トークン更新には `META_APP_ID` と `META_APP_SECRET` が必要です。
- Facebookページトークン更新には `FACEBOOK_USER_ACCESS_TOKEN`、または同等権限を持つ `INSTAGRAM_ACCESS_TOKEN` が必要です。
- LinkedInは `LINKEDIN_REFRESH_TOKEN` が `.env` にある場合だけ自動更新できます。ない場合は `linkedin_oauth.py` で再認証してください。
- YouTubeは `YOUTUBE_REFRESH_TOKEN` からアクセストークンを更新します。

## Notion

必要な値:

```env
NOTION_TOKEN=
NOTION_DATABASE_ID=38d2637669c68001bac3d716e8beb2f1
NOTION_VERSION=2022-06-28
```

用途:

- Notionデータベースのスキーマ取得
- Notionデータベースのページ一覧取得
- Notionデータベースへのページ作成
- Notionページのプロパティ更新

注意:

- 対象データベースにNotionインテグレーションを招待する
- `NOTION_TOKEN` は `.env` にのみ保存し、コードやドキュメントに実値を書かない
- 詳細は [notion.md](notion.md) を参照

## Google Sheets

必要な値:

```env
GOOGLE_SHEETS_CREDENTIALS_FILE=credentials/google-sheets-service-account.json
GOOGLE_SHEETS_SPREADSHEET_ID=
GOOGLE_SHEETS_DEFAULT_SHEET=Sheet1
```

用途:

- スプレッドシートのメタ情報取得
- 指定範囲の読み取り
- 行の追記
- 指定範囲の更新/クリア

注意:

- サービスアカウントJSONは `credentials/` 配下に置く
- 対象スプレッドシートをサービスアカウントの `client_email` に共有する
- JSONやスプレッドシートIDはコードに直書きしない
- 詳細は [sheets.md](sheets.md) を参照

## GitHub Actions

必要な値:

```env
PUBLIC_ASSET_BASE_URL=https://your-github-user.github.io/your-repository
NOTION_STATUS_PROPERTY=Status
NOTION_ERROR_PROPERTY=エラー内容
```

GitHub Actionsでは `.env` の代わりにGitHub Secretsを使用します。`GOOGLE_SHEETS_CREDENTIALS_FILE` は使わず、サービスアカウントJSON本文を `GOOGLE_SHEETS_CREDENTIALS_JSON` Secretへ登録します。

詳細は [github-actions.md](github-actions.md) を参照してください。
