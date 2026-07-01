# Reels/Shorts動画テンプレート

Instagram ReelsとYouTube Shortsの動画は、この仕様を正とします。新しいチャットで作る場合も、まずこのファイルと `reels_generator.py` を確認してください。

## 対象

- Instagram Reels
- YouTube Shorts

どちらも同じ縦動画を使うため、表紙も同じテンプレートで生成します。

## 表紙固定ルール

- サイズ: 1080 x 1920
- 背景: 白 `#ffffff`
- 画像: 広告1枚目を使用
- 画像配置: `x=90`, `y=820`, `w=900`, `h=780`
- タイトル: `なぜこの広告は◯ヶ月回っているのか？`
- 実データでは `◯ヶ月` を `3ヶ月` などの期間表記に差し替える
- `なぜこの広告は3ヶ月回っているのか？` のように実期間が入っても同じレイアウトを使う
- フォント: `assets/fonts/NotoSansJP-Bold.ttf`
- 文字色: 黒 `#111111`
- 強調: 期間部分だけ黒い角丸背景に白文字
- 余白: 左右72pxを基準にする
- タイトルは上寄せにしすぎず、画像との距離を詰める
- `広告分析メモ` などの余計なタイトルは入れない

## 現在の座標

`reels_generator.py` の `render_reel_cover_slide()` が正です。

- 1行目: `なぜこの広告は`
  - `y=290`
  - フォントサイズ104
- 強調行: `◯ヶ月`
  - 黒背景 `x=72`, `y=412`, `w=520`, `h=166`
  - フォントサイズ142
- 3行目: `回っているのか？`
  - `y=600`
  - フォントサイズ104
- 広告画像
  - `x=90`, `y=820`, `w=900`, `h=780`

このバランスが、ユーザー確認済みの基準です。

## 動画構成

標準構成:

1. 表紙 1.5秒
2. 広告1 3秒
3. ビジネスモデル 3秒
4. 広告2 3秒
5. ビジネスモデル 3秒
6. 最大で広告5まで繰り返し
7. エンディング 2秒

ページ切り替え:

- 標準は `none`
- フェードは残像が出やすいため標準にしない
- ページめくりは検証用オプションとして残す
- 最大広告数は5つまで

フォント:

- 表紙: `noto`
- 本文: `mincho`

## 動画生成固定ルール

`reels_generator.py` の `REEL_*` 定数が実装上の正です。

- 解像度: 1080 x 1920
- FPS: 30
- 表紙: 1.5秒
- 広告ページ: 3秒
- ビジネスモデルページ: 3秒
- エンディング: 2秒
- 最大広告数: 5
- ページ順: 表紙 -> 広告1 -> ビジネスモデル1 -> 広告2 -> ビジネスモデル2
- 広告ページのラベル: `広告分析①`
- 切り替え: `none`
- Ken Burns: 軽いズームあり
- 白背景は維持
- BGM: 標準で付ける
- ナレーション: なし
- 字幕アニメーション: なし
- サムネ画像: `thumbnail.png` を毎回出力する

## BGM固定ルール

ユーザー確認済みの `deliverables/reels/structured_reel.mp4` から抽出した音声を正規BGM素材として使います。

- 固定素材: `assets/audio/reel_bgm_reference.m4a`
- 元動画: `deliverables/reels/structured_reel.mp4`
- 動画尺に合わせてループ/トリムする
- MP4合成後の音声: AAC 192kbps
- この音源を変更する場合は、ユーザー確認後に素材ファイルごと差し替える

無音で確認したい場合だけ `--no-bgm` を使います。通常運用では使いません。

## サムネ固定ルール

表紙ページをそのまま `thumbnail.png` として保存します。

- CLI生成: `deliverables/reels/thumbnail.png`
- 日次自動投稿: `deliverables/auto_post/<run_id>/reel_XX/thumbnail.png`
- 公開アセット: `public/runs/<run_id>/reel_XX/thumbnail.png`
- YouTube Shorts: 投稿後にYouTube Data APIで `thumbnail.png` を自動設定する
- YouTube Shorts: アップロード直後だけでなく、Shorts処理後の反映漏れを避けるため時間差で複数回 `thumbnail.png` を再設定する
- Instagram Reels: 動画先頭1.5秒に同じ表紙を入れることでサムネ/プレビューに使われる前提にする
- Instagram側で別カバー指定は標準では行わない

## 標準コマンド

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
  --transition none \
  --font-style mincho \
  --cover-font-style noto
```

## 変更するときのルール

表紙、動画テンポ、ページ秒数、切り替え、BGMを変更した場合は、次を必ず更新します。

- `reels_generator.py`
- `docs/reel-short-video-template.md`
- `docs/templates.md`
- `README.md` のコマンド例
- 必要なら `.github/pull_request_template.md`
- BGM変更時は `assets/audio/reel_bgm_reference.m4a` も更新する

出力確認:

- `deliverables/reels/pages/page_00_cover.png` を目視確認
- `deliverables/reels/thumbnail.png` を目視確認
- `deliverables/reels/structured_reel.mp4` を再生確認
- BGMが聞こえることを確認
