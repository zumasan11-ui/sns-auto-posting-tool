# TikTok自動投稿設定

既存のInstagram Reels用MP4をそのままTikTokへ投稿します。動画生成は追加しません。

## 採用方式

TikTok Content Posting APIのDirect Postを使います。

- 既存の `reel.mp4` を `FILE_UPLOAD` でTikTokへ送信する
- 追加の動画生成はしない
- 投稿キャプションは動画系と同じキャプションを使う
- 投稿枠は `18:30`

Direct Postは `video.publish` スコープが必要です。TikTok公式ドキュメント上、未監査クライアントの投稿はprivate表示に制限されます。公開投稿まで自動化するには、テスト後にアプリ監査が必要です。

## Developer Portal設定

対象アプリ:

`https://developers.tiktok.com/app/7659752538647971857/pending`

必要な設定:

- Login Kitを追加
- Content Posting APIを追加
- Web用設定をオンにする
- Redirect URIを登録する
- 必要スコープを追加する
- Content Posting APIでDirect Postを有効化する
- 必要に応じてURL propertiesへ公開URL/ドメインを登録する

Redirect URIの制約:

- `https://` から始まる絶対URL
- クエリパラメータなし
- `#` フラグメントなし
- Developer Portal上のLogin Kit設定に登録したURIと、OAuth時の `TIKTOK_REDIRECT_URI` が完全一致すること

推奨Redirect URI:

```text
https://<GitHub Pagesのドメイン>/tiktok/callback
```

このプロジェクトでは初回OAuth時、Redirect先に表示されたURLから `code` をコピーして `tiktok_oauth.py --code` に渡します。コールバック処理用の常駐サーバーは追加しません。

## 必要スコープ

```text
user.info.basic,video.publish
```

`video.publish` はDirect Postに必要です。公開範囲制限を解除するには、Content Posting API / Direct Post / `video.publish` の審査が必要です。

## 初回OAuth

`.env` に最低限これを入れます。

```env
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=https://<登録済みRedirect URI>
TIKTOK_SCOPES=user.info.basic,video.publish
TIKTOK_EXPECTED_USERNAME=dyb36jfv1f6y
```

認証URLを表示:

```bash
python tiktok_oauth.py --print-url
```

表示されたURLをブラウザで開き、TikTokで許可します。Redirect先URLに含まれる `code` をコピーして交換します。
ここで許可するTikTokアカウントが投稿先になります。現在は `@dyb36jfv1f6y` 以外ならトークン保存も投稿も停止します。

```bash
python tiktok_oauth.py --code "取得したcode"
```

成功すると `.env` に次が保存されます。

- `TIKTOK_ACCESS_TOKEN`
- `TIKTOK_ACCESS_TOKEN_EXPIRES_AT`
- `TIKTOK_REFRESH_TOKEN`
- `TIKTOK_REFRESH_TOKEN_EXPIRES_AT`
- `TIKTOK_OPEN_ID`
- `TIKTOK_SCOPE`
- `TIKTOK_LAST_REFRESHED_AT`
- `TIKTOK_AUTHORIZED_USERNAME`

## GitHub Secrets

必須:

- `TIKTOK_CLIENT_KEY`
- `TIKTOK_CLIENT_SECRET`
- `TIKTOK_REDIRECT_URI`
- `TIKTOK_SCOPES`
- `TIKTOK_REFRESH_TOKEN`
- `TIKTOK_EXPECTED_USERNAME`

自動更新で書き戻される値:

- `TIKTOK_ACCESS_TOKEN`
- `TIKTOK_ACCESS_TOKEN_EXPIRES_AT`
- `TIKTOK_REFRESH_TOKEN_EXPIRES_AT`
- `TIKTOK_OPEN_ID`
- `TIKTOK_SCOPE`
- `TIKTOK_LAST_REFRESHED_AT`
- `TIKTOK_AUTHORIZED_USERNAME`

任意:

- `TIKTOK_PRIVACY_LEVEL`: 既定値 `SELF_ONLY`
- `TIKTOK_ENABLED`: 既定値 `true`。一時停止する場合は `false`

## 手動で必要な作業

Codex/コード側でできない、または本人操作が必要なもの:

1. Developer PortalでWeb用設定をオンにする
2. Redirect URIをLogin Kitへ登録する
3. `user.info.basic` と `video.publish` を有効化する
4. Content Posting APIでDirect Postを有効化する
5. TikTokログイン画面で本人アカウント連携を許可する
6. 公開投稿にしたい場合、Direct Post / `video.publish` の審査を申請する
7. 審査用に必要な利用目的、画面録画、投稿フロー説明、利用規約/プライバシーポリシーURLを入力する

## 実装ファイル

- `tiktok_oauth.py`: 初回OAuth URL生成とcode交換
- `tiktok_poster.py`: TikTok Direct Postアップロード
- `token_refresh.py`: TikTok access token更新
- `daily_auto_post.py`: 既存Reels MP4をTikTok投稿タスクへ追加
