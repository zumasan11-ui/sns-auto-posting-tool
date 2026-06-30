# Notion API連携

このツールはブラウザ操作ではなく、Notion APIでデータベースを読み書きします。

## 必要な環境変数

```env
NOTION_TOKEN=
NOTION_DATABASE_ID=your_notion_database_id_here
NOTION_VERSION=2022-06-28
```

`NOTION_TOKEN` はNotionのインテグレーションシークレットです。コードやREADMEには実値を書かないでください。

## Notion側の設定

1. Notion Developersで内部インテグレーションを作成する
2. Integration tokenを `.env` の `NOTION_TOKEN` に保存する
3. 対象データベースページを開く
4. 右上の共有メニューから、作成したインテグレーションを招待する
5. データベースIDを `.env` の `NOTION_DATABASE_ID` に保存する

今回の対象:

```env
NOTION_DATABASE_ID=your_notion_database_id_here
```

## スキーマ確認

まずプロパティ名と型を確認します。

```bash
python notion_api.py schema
```

Notion APIの書き込みはプロパティ型に合わせたJSONが必要です。最初に `schema` でプロパティ名を確認してから `create` / `update` を使います。

## ページ一覧取得

```bash
python notion_api.py list --limit 10
```

フィルタやソートを使う場合:

```bash
python notion_api.py list \
  --filter-json '{"property":"Status","status":{"equals":"Ready"}}' \
  --sorts-json '[{"property":"Scheduled At","direction":"ascending"}]'
```

JSONファイルから渡すこともできます。

```bash
python notion_api.py list --filter-json @notion_filter.json
```

## ページ作成

例:

```bash
python notion_api.py create \
  --properties-json '{
    "Name": {
      "title": [
        {"text": {"content": "YouTube Shorts投稿テスト"}}
      ]
    },
    "Status": {
      "status": {"name": "Draft"}
    }
  }' \
  --body "投稿本文のメモ"
```

プロパティ名や型はデータベース側の設計に依存します。上の `Name` / `Status` は例です。

## ページ更新

```bash
python notion_api.py update \
  --page-id PAGE_ID \
  --properties-json '{
    "Status": {
      "status": {"name": "Posted"}
    },
    "Post URL": {
      "url": "https://www.youtube.com/shorts/..."
    }
  }'
```

アーカイブ:

```bash
python notion_api.py update --page-id PAGE_ID --archive
```

復元:

```bash
python notion_api.py update --page-id PAGE_ID --restore
```

## SNS投稿フローとの使い分け

現時点では `notion_api.py` はNotion DBの汎用読み書きCLIです。SNS投稿実行そのものは既存の `main.py` / `carousel_poster.py` / `youtube_poster.py` が担当します。

次の段階で、Notion DBのステータスが `Ready` の行を取得し、投稿成功後に `Posted` と投稿URLを書き戻す自動投稿フローへ接続します。
