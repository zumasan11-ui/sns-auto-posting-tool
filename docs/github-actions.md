# GitHub Actions移行

このプロジェクトは `.github/workflows/daily-sns-auto-post.yml` で日次自動投稿を実行します。

## 完全自動運用ポリシー

最終目標は、PCの電源が切れていてもGitHub Actionsだけで何ヶ月も放置できる構成です。

- 一度OAuth認証とGitHub Secrets登録を済ませたら、日々の投稿・画像生成・動画生成・公開アセット作成・SNS投稿はGitHub Actionsだけで実行します。
- 手動更新が発生しない構成を最優先にします。
- トークン期限切れや認証更新が必要なものは、可能な限りActions内で自動更新します。
- 更新後のトークンは `GH_SECRETS_TOKEN` を使ってGitHub Secretsへ書き戻します。
- 投稿対象がない日でも、Actions起動時に `python token_refresh.py all` を実行し、期限が近いトークンの更新を試みます。
- 手動操作が必要になるのは、各サービスの仕様上どうしても再OAuth・本人確認・権限再付与が必要な場合だけです。
- 新しいSNSやAPIを追加する場合も、このポリシーを優先し、手動ログイン前提の設計は最後の手段にします。

## 実行スケジュール

- 日本時間 05:00: Notionの対象ページ取得、Statusを `進行中` へ更新、カルーセル/リール生成、公開アセット作成
- 日本時間 07:30 / 12:00 / 16:00 / 19:30: 投稿計画の該当スロットを実投稿
- 手動実行: GitHub Actionsの `Daily SNS Auto Post` から `run_now=true`

GitHub Actionsは長時間待機に向かないため、5:00のジョブで1日の計画を作成し、投稿時刻ごとのcronで続きの処理を行います。計画と公開アセットは `gh-pages` ブランチに保存します。

X、Threads、Facebookページのテキスト投稿は、各スロット内でセクションごとに1分ずつずらします。同じセクションは3媒体へ同じタイミングで投稿し、媒体ごとにはずらしません。

## 投稿対象

Notionデータベースから `Status` が `進行中`、`エラー`、`未投稿` の順で最も古いページを探します。通常は `未投稿` が対象です。失敗時は `エラー` として残るため、次回実行で同じページを再処理できます。

## Secrets運用ルール

- 秘密情報はGitHub Secretsまたはローカル `.env` のみに保存します。
- `.env`、`credentials/`、JSON秘密鍵、トークン類はGit管理しません。
- README、docs、ログ、Actions出力には実値を表示しません。
- `.env.example` にはプレースホルダーだけを書きます。
- APIキー、アクセストークン、クライアントシークレット、JSONキーはマスク・非表示で扱います。
- コミット前に `python3 scripts/secret_scan.py --mode staged` を実行します。ローカルhookは `bash scripts/install_git_hooks.sh` で入れます。

## 必須GitHub Secrets

`.env.example` と同じ値をGitHub Secretsへ登録します。サービスアカウントJSONはファイルではなく、JSON本文を `GOOGLE_SHEETS_CREDENTIALS_JSON` に登録します。

### 共通

- `GH_SECRETS_TOKEN`
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `NOTION_VERSION`
- `GOOGLE_SHEETS_CREDENTIALS_JSON`
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `GOOGLE_SHEETS_DEFAULT_SHEET`
- `PUBLIC_ASSET_BASE_URL`

`GH_SECRETS_TOKEN` は、Actions内で自動更新されたSNSトークンを次回実行用にGitHub Secretsへ書き戻すために使います。`repo` 権限または対象リポジトリのSecrets更新権限を持つトークンを登録してください。値はログへ表示しません。

## トークン自動更新

Actionsは `.env` をSecretsから組み立てた後、毎回 `python token_refresh.py all` を実行します。

対象:

- Threads
- Instagram
- Facebookページ
- LinkedIn
- YouTube

更新できた値は `.env` に保存され、ジョブ最後の `scripts/sync_env_to_github_secrets.py` によりGitHub Secretsへ書き戻されます。

自動更新できないもの:

- XのOAuth 1.0aアクセストークン
- 各サービス側でrefresh token自体が失効した場合
- 権限変更、本人確認、アプリ審査、利用規約同意など人の操作が必須な場合

この場合だけ再認証が必要です。通常運用ではGitHub Actionsだけで更新・投稿を継続します。

### X

- `API_KEY`
- `API_SECRET`
- `ACCESS_TOKEN`
- `ACCESS_TOKEN_SECRET`

### Threads

- `THREADS_APP_ID`
- `THREADS_APP_SECRET`
- `THREADS_REDIRECT_URI`
- `THREADS_USER_ID`
- `THREADS_ACCESS_TOKEN`
- `THREADS_ACCESS_TOKEN_EXPIRES_AT`
- `THREADS_LAST_REFRESHED_AT`

### Instagram / Meta

- `META_APP_ID`
- `META_APP_SECRET`
- `META_REDIRECT_URI`
- `INSTAGRAM_APP_ID`
- `INSTAGRAM_APP_SECRET`
- `INSTAGRAM_REDIRECT_URI`
- `INSTAGRAM_USER_ID`
- `INSTAGRAM_ACCESS_TOKEN`
- `INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT`
- `INSTAGRAM_LAST_REFRESHED_AT`

### Facebookページ

- `FACEBOOK_PAGE_ID`
- `FACEBOOK_APP_ID`
- `FACEBOOK_APP_SECRET`
- `FACEBOOK_REDIRECT_URI`
- `FACEBOOK_PAGE_ACCESS_TOKEN`
- `FACEBOOK_USER_ACCESS_TOKEN`
- `FACEBOOK_USER_ACCESS_TOKEN_EXPIRES_AT`
- `FACEBOOK_PAGE_ACCESS_TOKEN_EXPIRES_AT`
- `FACEBOOK_LAST_REFRESHED_AT`

### LinkedIn

- `LINKEDIN_CLIENT_ID`
- `LINKEDIN_CLIENT_SECRET`
- `LINKEDIN_REDIRECT_URI`
- `LINKEDIN_SCOPES`
- `LINKEDIN_ACCESS_TOKEN`
- `LINKEDIN_ACCESS_TOKEN_EXPIRES_AT`
- `LINKEDIN_REFRESH_TOKEN`
- `LINKEDIN_REFRESH_TOKEN_EXPIRES_AT`
- `LINKEDIN_LAST_REFRESHED_AT`
- `LINKEDIN_PERSON_URN`

### YouTube

- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REDIRECT_URI`
- `YOUTUBE_REFRESH_TOKEN`
- `YOUTUBE_ACCESS_TOKEN`
- `YOUTUBE_ACCESS_TOKEN_EXPIRES_AT`
- `YOUTUBE_LAST_REFRESHED_AT`

### 任意Secrets

- `NOTION_STATUS_PROPERTY`: 既定値 `Status`
- `NOTION_ERROR_PROPERTY`: 既定値 `エラー内容`

## 登録手順

GitHub CLIでまとめて登録する場合:

```bash
bash scripts/github_bootstrap.sh sns-auto-posting-tool
```

既存リポジトリへ個別登録する場合:

```bash
gh secret set SECRET_NAME --body "secret-value"
```

Google SheetsのサービスアカウントJSONは、ファイルをコミットせずSecretへ本文を登録します。

```bash
gh secret set GOOGLE_SHEETS_CREDENTIALS_JSON < credentials/google-sheets-service-account.json
```

登録済みSecret名を確認する場合:

```bash
gh secret list
```

値は表示しません。表示・ログ出力しないでください。

## GitHub Pages

Instagram/Threads/Facebookの画像・動画投稿には、Meta側から取得できる公開HTTPS URLが必要です。このため、生成画像/動画を `gh-pages` ブランチへ保存し、GitHub Pagesで公開します。

リポジトリ作成後、Pagesのソースを `gh-pages` ブランチに設定してください。`PUBLIC_ASSET_BASE_URL` は次の形式です。

```env
PUBLIC_ASSET_BASE_URL=https://OWNER.github.io/REPOSITORY
```

## Reels/Shorts動画テンプレート

GitHub Actionsの日次自動投稿でも、Instagram ReelsとYouTube Shortsの動画は [reel-short-video-template.md](reel-short-video-template.md) の固定ルールを使います。

- 表紙は1.5秒
- 広告ページとビジネスモデルページは各3秒
- 表紙フォントは `noto`
- 本文フォントは `mincho`
- 切り替えは `none`
- BGMは `assets/audio/reel_bgm_reference.m4a` の固定音源
- `thumbnail.png` を毎回公開アセットとして保存
- YouTube Shorts投稿時は `thumbnail.png` を時間差で複数回自動設定
- Instagram Reelsは動画先頭1.5秒の表紙をサムネ/プレビューとして使う
- 最大広告数は5つまで

実装上の正は `reels_generator.py` の `REEL_*` 定数です。Actions側の生成処理は `daily_auto_post.py` からその定数を読み込みます。

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

## 動作確認手順

1. `python3 scripts/secret_scan.py --mode tracked` が成功することを確認する
2. `gh secret list` で必要なSecret名が揃っていることを確認する
3. GitHub Pagesの `PUBLIC_ASSET_BASE_URL` がブラウザで開けることを確認する
4. Actionsの `Daily SNS Auto Post` を `run_now=true` で手動実行する
5. 実行後、Notionの各媒体ステータスが `完了` または原因付きの `エラー` になることを確認する
6. `gh-pages` ブランチの `state/current.json` に投稿済みタスクが保存されることを確認する
7. Actionsログに秘密情報が表示されていないことを確認する

## 漏洩時の対応

Git履歴、README、docs、ログ、Actions出力にAPIキー・アクセストークン・クライアントシークレット・JSONキーが出た場合は漏洩扱いにします。

1. 該当サービス側でトークンやキーを即失効する
2. 新しい値を再発行する
3. GitHub Secretsとローカル `.env` を新しい値へ差し替える
4. `git filter-repo` またはBFGで履歴から削除する
5. force push後に、GitHub Actionsログ、`main`、`gh-pages` を再スキャンする
6. 再発行後にActions手動実行で投稿確認する
