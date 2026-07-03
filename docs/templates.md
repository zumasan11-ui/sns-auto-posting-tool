# テンプレート構成

## フォント

ローカルフォント:

- `assets/fonts/NotoSansJP-Regular.ttf`
- `assets/fonts/NotoSansJP-Bold.ttf`

macOS標準フォントも利用します。

- ヒラギノ角ゴシック
- ヒラギノ丸ゴ
- ヒラギノ明朝

## Instagram/LinkedInカルーセル

実装:

- `carousel_generator.py`

入力:

- 広告スクショ
- 分析文

出力:

- `deliverables/carousel_test/slide_01.png` ... `slide_10.png`
- `deliverables/carousel_test/linkedin_carousel.pdf`

仕様:

- 1080 x 1350
- 1枚目は表紙
- 本文ページは `広告分析①` -> `ビジネスモデル` の順に交互配置
- 最後はプロフィール誘導
- 広告1件なら4枚、広告2件なら6枚、広告3件なら8枚、広告4件なら10枚
- 最大は広告4件、合計10枚
- 広告分析ページは上部に分析テキスト、下部に広告スクショ
- ビジネスモデルページは文章だけ
- 左上バッジは黒
- 本文は通常サイズで収まる場合は既存の見た目を維持する
- 長文で画像や枠に被る場合だけ文字サイズを下げ、上下余白が均等になるよう中央寄せする
- PDFはLinkedIn投稿用

## カルーセル表紙/最後

表紙:

- タイトル例: `なぜこの広告は◯ヶ月回っているのか？`
- 実データでは `◯ヶ月` を `1年` などの掲載期間へ置き換える
- 広告1枚目の画像を使用する

最後:

- `続きはプロフィールへ`
- `毎日広告分析を発信中`
- InstagramカルーセルとLinkedIn PDFの両方に入れる

## Instagram Reelsページ

実装:

- `reels_generator.py`

出力:

- `deliverables/reels/pages/page_00_cover.png`
- `deliverables/reels/pages/page_01_ad.png`
- `deliverables/reels/pages/page_01_business.png`
- `deliverables/reels/structured_reel.mp4`

ページ構成:

1. 表紙
2. 広告ページ
3. ビジネスモデルページ
4. 広告ページ
5. ビジネスモデルページ

最大:

- 広告5枚
- 表紙1枚
- 広告ページ5枚
- ビジネスモデルページ5枚
- エンディング1枚

## 表紙テンプレート

役割:

- 1秒から1.5秒だけ表示するサムネ風ページ
- Instagram ReelsとYouTube Shortsで共通利用する

現在の仕様:

- 1080 x 1920
- 広告①の画像を使用
- 表紙フォントはNoto太字
- タイトル例: `なぜこの広告は◯ヶ月回っているのか？`
- `◯ヶ月` は黒背景で強調
- 標準表示時間は1.5秒
- 標準ページ切り替えは `none`

固定ルール:

- 詳細な座標、フォントサイズ、動画秒数、BGM、変更時の確認項目は [reel-short-video-template.md](reel-short-video-template.md) を正とする
- 新しいチャットで同じ品質を再現する場合は、まず [reel-short-video-template.md](reel-short-video-template.md) と `reels_generator.py` の `REEL_*` 定数を確認する

## 広告ページテンプレート

現在の仕様:

- 左上に `広告分析①`, `広告分析②`
- 本文は明朝
- 下部に広告画像
- 白背景
- 標準表示時間は3秒

## ビジネスモデルページテンプレート

現在の仕様:

- 画像なし
- 左上に `ビジネスモデル`
- 文章だけ
- 白背景

## 動画BGM

現在はMixkitの無料BGM一覧から毎回ランダムに1曲取得し、標準でMP4へ合成します。取得に失敗した場合は `assets/audio/mixkit_fallback/` の保存済みBGMからランダムに選びます。

BGMとサムネの固定仕様は [reel-short-video-template.md](reel-short-video-template.md) を正とします。

## 動画サムネ

表紙ページをそのまま `thumbnail.png` として保存します。

- YouTube Shorts投稿時は `thumbnail.png` を自動設定する
- Instagram Reels投稿時は動画先頭1.5秒の表紙をサムネ/プレビューとして使う
