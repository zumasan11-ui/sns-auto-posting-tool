# YouTube Shorts投稿設定

このツールは YouTube Data API v3 とOAuth 2.0を使って、ローカルMP4をYouTubeへアップロードします。

## 必要な環境変数

```env
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=http://localhost:8080/callback
YOUTUBE_REFRESH_TOKEN=
```

`YOUTUBE_REFRESH_TOKEN` は `youtube_oauth.py` で初回認証した時に `.env` へ保存されます。

## Google Cloud Console設定

対象プロジェクト:

- `sns-auto-post-500812`
- https://console.cloud.google.com/apis/dashboard?project=sns-auto-post-500812

手順:

1. Google Cloud Consoleでプロジェクトを作成、または `sns-auto-post-500812` を選択する
2. 「APIとサービス」>「ライブラリ」で `YouTube Data API v3` を検索して有効化する
3. 「APIとサービス」>「OAuth同意画面」を開く
4. User Typeを選び、アプリ名、サポートメール、デベロッパー連絡先を入力する
5. スコープに `https://www.googleapis.com/auth/youtube.upload` を追加する
6. 公開ステータスがテスト中の場合は、投稿に使うGoogleアカウントをテストユーザーへ追加する
7. 「認証情報」>「認証情報を作成」>「OAuthクライアントID」を選ぶ
8. アプリケーションの種類は `ウェブ アプリケーション` を選ぶ
9. 承認済みのリダイレクトURIに `http://localhost:8080/callback` を追加する
10. 作成されたクライアントIDとクライアントシークレットを `.env` に保存する

## 初回認証

`.env` に以下を入れます。

```env
YOUTUBE_CLIENT_ID=作成したOAuthクライアントID
YOUTUBE_CLIENT_SECRET=作成したOAuthクライアントシークレット
YOUTUBE_REDIRECT_URI=http://localhost:8080/callback
YOUTUBE_REFRESH_TOKEN=
```

初回認証スクリプトを実行します。

```bash
python youtube_oauth.py
```

ブラウザでGoogleアカウントを選び、YouTubeアップロード権限を許可すると、`YOUTUBE_REFRESH_TOKEN` が `.env` に保存されます。

refresh tokenが取得できない場合は、同じGoogleアカウントで過去に同意済みの可能性があります。Googleアカウントのサードパーティ連携から該当アプリのアクセスを解除し、もう一度 `python youtube_oauth.py` を実行してください。

## YouTube Shorts投稿

`youtube_poster.py` から直接投稿できます。

```bash
python youtube_poster.py \
  --video deliverables/reels/structured_reel.mp4 \
  --title "広告クリエイティブ改善メモ #Shorts" \
  --description "概要欄テキスト" \
  --tags 広告 マーケティング Shorts \
  --privacy-status public
```

生成物投稿フローの入口からも投稿できます。

```bash
python carousel_poster.py youtube \
  --video deliverables/reels/structured_reel.mp4 \
  --title "広告クリエイティブ改善メモ #Shorts" \
  --description "概要欄テキスト" \
  --tags 広告 マーケティング Shorts
```

`main.py` のSNS投稿フローから投稿する場合は、`-t` が概要欄になります。

```bash
python main.py --platform youtube \
  --video deliverables/reels/structured_reel.mp4 \
  --title "広告クリエイティブ改善メモ #Shorts" \
  --tags 広告 マーケティング Shorts \
  -t "概要欄テキスト"
```

投稿に成功すると、`https://www.youtube.com/shorts/...` のURLを出力します。

## Shortsとして投稿するための注意

YouTube Data APIに「Shortsとして投稿する」専用フラグはありません。Shortsとして扱われるには、動画自体がYouTube Shortsの条件を満たす必要があります。

- MP4を指定する
- 縦長動画にする
- 3分以内を目安にする
- タイトルまたは概要欄に `#Shorts` を入れる

このツールは、タイトルまたは概要欄に `#Shorts` がない場合、概要欄へ自動で `#Shorts` を追加します。タグには `Shorts` を自動追加します。
