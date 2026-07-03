# Google Sheets API連携

このプロジェクトでは、サービスアカウントJSONを使ってGoogle Sheets APIに接続します。

## 必要な.env

```env
GOOGLE_SHEETS_CREDENTIALS_FILE=credentials/google-sheets-service-account.json
GOOGLE_SHEETS_SPREADSHEET_ID=
GOOGLE_SHEETS_DEFAULT_SHEET=Sheet1
```

## サービスアカウントJSON

作成したJSONは、例として次の場所に保存します。

```bash
mkdir -p credentials
```

```text
credentials/google-sheets-service-account.json
```

`credentials/` は `.gitignore` で除外しています。JSONの中身や秘密鍵はREADME、コード、チャットに貼らないでください。

## Google Cloud Console側の設定

1. Google Cloud Consoleで対象プロジェクトを開く
2. Google Sheets APIを有効化する
3. IAMと管理、またはAPIとサービスからサービスアカウントを作成する
4. サービスアカウントキーをJSONで発行する
5. JSONを `GOOGLE_SHEETS_CREDENTIALS_FILE` のパスへ保存する
6. JSON内の `client_email` をコピーする
7. 対象スプレッドシートを開き、`client_email` に共有する
8. 書き込みも行う場合は編集権限を付ける

## スプレッドシートID

スプレッドシートURLが次の形式の場合:

```text
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
```

`/d/` と `/edit` の間が `GOOGLE_SHEETS_SPREADSHEET_ID` です。

## 動作確認

メタ情報を取得します。

```bash
python sheets_api.py meta
```

値を読み取ります。

```bash
python sheets_api.py read --range "Sheet1!A1:D10"
```

行を追記します。

```bash
python sheets_api.py append \
  --range "Sheet1!A:D" \
  --values-json '[["2026-06-29","投稿案","未着手"]]'
```

## 日次自動投稿の保存ルール

日次自動投稿では、Google Sheetsへ1広告につき1行で保存します。

- 広告分析本文と、その次のビジネスモデル本文を同じ行に入れる
- 掲載期間は本文から除いて保存する
- X投稿URLは広告分析側の投稿URLだけを保存する
- ビジネスモデル側のX投稿URLは保存しない
- X投稿URL列は `=HYPERLINK("https://x.com/...","https://x.com/...")` の形式で、URL表示のクリック可能なリンクとして保存する

指定範囲を更新します。

```bash
python sheets_api.py update \
  --range "Sheet1!A1:C1" \
  --values-json '[["日付","内容","ステータス"]]'
```

指定範囲をクリアします。

```bash
python sheets_api.py clear --range "Sheet1!A1:C1"
```

## よくあるエラー

- `サービスアカウントJSONが見つかりません`: `GOOGLE_SHEETS_CREDENTIALS_FILE` のパスが違います。
- `The caller does not have permission`: スプレッドシートがサービスアカウントの `client_email` に共有されていません。
- `Unable to parse range`: シート名や範囲指定が間違っています。シート名に空白がある場合は `'Sheet Name'!A1:D10` のように指定します。
