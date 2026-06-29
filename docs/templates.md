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
- 上部に分析テキスト
- 下部に広告スクショ
- 左上バッジは黒
- PDFはLinkedIn投稿用

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

現在の仕様:

- 1080 x 1920
- 広告①の画像を使用
- 表紙フォントはNoto太字
- タイトル例: `なぜこの広告は◯ヶ月回っているのか？`
- `◯ヶ月` は黒背景で強調

## 広告ページテンプレート

現在の仕様:

- 左上に `広告①`, `広告②`
- 本文は明朝
- 下部に広告画像
- 白背景

## ビジネスモデルページテンプレート

現在の仕様:

- 画像なし
- 左上に `ビジネスモデル`
- 文章だけ
- 白背景

## 動画BGM

現在は外部音源なしで `deliverables/reels/bgm_upbeat.wav` をローカル生成しています。

本番運用では、権利上問題のないBGMファイルに差し替えるのが望ましいです。

