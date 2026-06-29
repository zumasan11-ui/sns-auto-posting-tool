import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


ENV_FILE = Path(".env")
SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)


def load_sheets_config() -> Dict[str, str]:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    credentials_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "").strip()
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    default_sheet = os.getenv("GOOGLE_SHEETS_DEFAULT_SHEET", "").strip() or "Sheet1"
    missing = []

    if not credentials_path:
        missing.append("GOOGLE_SHEETS_CREDENTIALS_FILE")
    if not spreadsheet_id:
        missing.append("GOOGLE_SHEETS_SPREADSHEET_ID")

    if missing:
        raise RuntimeError(".env に必要な値がありません: " + ", ".join(missing))

    credentials_file = Path(credentials_path).expanduser()
    if not credentials_file.exists():
        raise RuntimeError(f"サービスアカウントJSONが見つかりません: {credentials_file}")

    return {
        "credentials_file": str(credentials_file),
        "spreadsheet_id": spreadsheet_id,
        "default_sheet": default_sheet,
    }


def build_sheets_service(config: Dict[str, str]) -> Any:
    credentials = service_account.Credentials.from_service_account_file(
        config["credentials_file"],
        scopes=SHEETS_SCOPES,
    )
    return build("sheets", "v4", credentials=credentials)


def sheet_range(config: Dict[str, str], range_name: Optional[str]) -> str:
    return range_name.strip() if range_name else config["default_sheet"]


def get_spreadsheet(service: Any, spreadsheet_id: str) -> Dict[str, Any]:
    return service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()


def read_values(service: Any, spreadsheet_id: str, range_name: str) -> List[List[Any]]:
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    return result.get("values", [])


def append_values(
    service: Any,
    spreadsheet_id: str,
    range_name: str,
    values: List[List[Any]],
    value_input_option: str = "USER_ENTERED",
) -> Dict[str, Any]:
    body = {"values": values}
    return (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=value_input_option,
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )


def update_values(
    service: Any,
    spreadsheet_id: str,
    range_name: str,
    values: List[List[Any]],
    value_input_option: str = "USER_ENTERED",
) -> Dict[str, Any]:
    body = {"values": values}
    return (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=value_input_option,
            body=body,
        )
        .execute()
    )


def clear_values(service: Any, spreadsheet_id: str, range_name: str) -> Dict[str, Any]:
    return (
        service.spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_name, body={})
        .execute()
    )


def load_values_arg(value: str) -> List[List[Any]]:
    source = value.strip()
    if source.startswith("@"):
        source = Path(source[1:]).read_text(encoding="utf-8")

    try:
        data = json.loads(source)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"--values-json のJSON形式が不正です: {error}") from error

    if not isinstance(data, list) or any(not isinstance(row, list) for row in data):
        raise RuntimeError('--values-json は [["A1", "B1"], ["A2", "B2"]] 形式にしてください。')

    return data


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Google Sheets APIでスプレッドシートを読み書きします。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("meta", help="スプレッドシートのメタ情報を表示します。")

    read_parser = subparsers.add_parser("read", help="指定範囲の値を読み取ります。")
    read_parser.add_argument("--range", dest="range_name", help="例: Sheet1!A1:D10。省略時は GOOGLE_SHEETS_DEFAULT_SHEET。")

    append_parser = subparsers.add_parser("append", help="指定範囲へ行を追記します。")
    append_parser.add_argument("--range", dest="range_name", help="例: Sheet1!A:D。省略時は GOOGLE_SHEETS_DEFAULT_SHEET。")
    append_parser.add_argument("--values-json", required=True, help='例: \'[["日時","本文"]]\'。@file.json も指定できます。')
    append_parser.add_argument("--raw", action="store_true", help="RAWとして書き込みます。省略時はUSER_ENTERED。")

    update_parser = subparsers.add_parser("update", help="指定範囲の値を更新します。")
    update_parser.add_argument("--range", dest="range_name", required=True, help="例: Sheet1!A1:B2")
    update_parser.add_argument("--values-json", required=True, help='例: \'[["日時","本文"]]\'。@file.json も指定できます。')
    update_parser.add_argument("--raw", action="store_true", help="RAWとして書き込みます。省略時はUSER_ENTERED。")

    clear_parser = subparsers.add_parser("clear", help="指定範囲の値をクリアします。")
    clear_parser.add_argument("--range", dest="range_name", required=True, help="例: Sheet1!A1:B2")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_sheets_config()
    service = build_sheets_service(config)
    spreadsheet_id = config["spreadsheet_id"]

    if args.command == "meta":
        spreadsheet = get_spreadsheet(service, spreadsheet_id)
        print_json(
            {
                "spreadsheetId": spreadsheet.get("spreadsheetId"),
                "spreadsheetUrl": spreadsheet.get("spreadsheetUrl"),
                "properties": spreadsheet.get("properties", {}),
                "sheets": [
                    {
                        "sheetId": sheet.get("properties", {}).get("sheetId"),
                        "title": sheet.get("properties", {}).get("title"),
                        "index": sheet.get("properties", {}).get("index"),
                    }
                    for sheet in spreadsheet.get("sheets", [])
                ],
            }
        )
        return 0

    if args.command == "read":
        values = read_values(service, spreadsheet_id, sheet_range(config, args.range_name))
        print_json(values)
        return 0

    if args.command == "append":
        values = load_values_arg(args.values_json)
        result = append_values(
            service,
            spreadsheet_id,
            sheet_range(config, args.range_name),
            values,
            "RAW" if args.raw else "USER_ENTERED",
        )
        print_json(result)
        return 0

    if args.command == "update":
        values = load_values_arg(args.values_json)
        result = update_values(
            service,
            spreadsheet_id,
            args.range_name,
            values,
            "RAW" if args.raw else "USER_ENTERED",
        )
        print_json(result)
        return 0

    if args.command == "clear":
        print_json(clear_values(service, spreadsheet_id, args.range_name))
        return 0

    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nキャンセルしました。", file=sys.stderr)
        raise SystemExit(130)
    except HttpError as error:
        print(f"\nGoogle Sheets APIエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
