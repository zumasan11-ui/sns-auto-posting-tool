# OAuth/認証手順

## Threads

スクリプト:

```bash
python threads_oauth.py
```

必要な `.env`:

```env
THREADS_APP_ID=
THREADS_APP_SECRET=
THREADS_REDIRECT_URI=http://localhost:8765/callback
```

流れ:

1. `.env` にThreadsアプリ情報を入れる
2. `python threads_oauth.py` を実行
3. ブラウザで認可
4. コールバックを受ける
5. `THREADS_ACCESS_TOKEN` を取得

## LinkedIn

スクリプト:

```bash
python linkedin_oauth.py
```

必要な `.env`:

```env
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=http://localhost:3000/callback
LINKEDIN_SCOPES=openid profile w_member_social offline_access
```

流れ:

1. LinkedIn Developerでアプリを作成
2. Redirect URIを `.env` と合わせる
3. `python linkedin_oauth.py` を実行
4. ブラウザで認可
5. `LINKEDIN_ACCESS_TOKEN` を保存
6. 取得できた場合は `LINKEDIN_REFRESH_TOKEN` も保存
7. 必要なら `LINKEDIN_PERSON_URN` も保存

## YouTube

スクリプト:

```bash
python youtube_oauth.py
```

必要な `.env`:

```env
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=http://localhost:8080/callback
```

流れ:

1. Google Cloud Consoleで YouTube Data API v3 を有効化
2. OAuth同意画面を設定
3. OAuthクライアントIDを作成
4. Redirect URIを `.env` と合わせる
5. `python youtube_oauth.py` を実行
6. ブラウザで認可
7. `YOUTUBE_REFRESH_TOKEN` を `.env` に保存

詳細は [youtube.md](youtube.md) を参照してください。

## Instagram Business Account ID

スクリプト:

```bash
python instagram_setup.py
```

用途:

- Facebookページに紐づくInstagram Business Account IDを取得
- `.env` の `INSTAGRAM_USER_ID` に保存

必要な権限:

- `instagram_basic`
- `instagram_content_publish`
- Facebookページ関連のGraph API権限

## Meta OAuth（Instagram/Facebookページ）

InstagramとFacebookページをまとめて投稿可能にする場合:

```bash
python meta_oauth.py
```

必要な `.env`:

```env
META_APP_ID=
META_APP_SECRET=
META_REDIRECT_URI=http://localhost:8766/callback
```

取得・保存する値:

- `INSTAGRAM_USER_ID`
- `INSTAGRAM_ACCESS_TOKEN`
- `FACEBOOK_PAGE_ID`
- `FACEBOOK_PAGE_ACCESS_TOKEN`
- `FACEBOOK_USER_ACCESS_TOKEN`

Facebook LoginのリダイレクトURIには `.env` の `META_REDIRECT_URI` と同じURLを登録してください。

## アクセストークン自動更新

スクリプト:

```bash
python token_refresh.py status
python token_refresh.py all
```

特定サービスだけ更新:

```bash
python token_refresh.py threads --force
python token_refresh.py instagram --force
python token_refresh.py facebook --force
python token_refresh.py linkedin --force
python token_refresh.py youtube --force
```

対応:

- Threads: 長期アクセストークンを `th_refresh_token` で更新
- Instagram: Metaアプリ情報がある場合は長期User Tokenを再交換
- Facebook: User TokenからPage Access Tokenを再取得
- LinkedIn: `LINKEDIN_REFRESH_TOKEN` からAccess Tokenを更新
- YouTube: `YOUTUBE_REFRESH_TOKEN` からAccess Tokenを更新

更新後の値と期限は `.env` に保存されます。更新できない場合だけ、各OAuthスクリプトやMeta/LinkedIn側で再認証してください。
