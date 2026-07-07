import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, get_spreadsheet, load_sheets_config
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id


DEFAULT_SHEETS = ("今日の広告DB", "広告分析マスターDB")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def log(message: str) -> None:
    print(f"[sheet-formatting] {message}", flush=True)


def sheet_properties_for(service: Any, spreadsheet_id: str, sheet_name: str) -> dict[str, Any]:
    spreadsheet = get_spreadsheet(service, spreadsheet_id)
    target = clean(sheet_name)
    for sheet in spreadsheet.get("sheets", []):
        properties = sheet.get("properties", {})
        if clean(properties.get("title")) == target:
            return properties
    raise RuntimeError(f"シートが見つかりません: {sheet_name}")


def sheet_id_for(service: Any, spreadsheet_id: str, sheet_name: str) -> int:
    return int(sheet_properties_for(service, spreadsheet_id, sheet_name)["sheetId"])


def freeze_row_height(service: Any, spreadsheet_id: str, sheet_name: str, pixel_size: int = 20, max_rows: int = 0) -> None:
    properties = sheet_properties_for(service, spreadsheet_id, sheet_name)
    sheet_id = int(properties["sheetId"])
    row_count = int((properties.get("gridProperties") or {}).get("rowCount") or 1000)
    end_index = max_rows if max_rows > 0 else row_count
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": 0,
                            "endIndex": end_index,
                        },
                        "properties": {"pixelSize": pixel_size},
                        "fields": "pixelSize",
                    }
                }
            ]
        },
    ).execute()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="指定シートの行高さを固定します。")
    parser.add_argument("--spreadsheet", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_SPREADSHEET))
    parser.add_argument("--sheet", action="append", default=[], help="対象シート名。複数指定可。省略時は今日DBとマスターDB。")
    parser.add_argument("--pixel-size", type=int, default=20)
    parser.add_argument("--max-rows", type=int, default=1000)
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    service = build_sheets_service(load_sheets_config())
    sheets = args.sheet or list(DEFAULT_SHEETS)
    for sheet_name in sheets:
        freeze_row_height(service, spreadsheet_id, sheet_name, args.pixel_size, args.max_rows)
        log(f"{sheet_name}: 行高さ {args.pixel_size}px 固定")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nキャンセルしました。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
