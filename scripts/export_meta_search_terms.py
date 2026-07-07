import argparse
import json
import re
import sys
from pathlib import Path
from typing import List

from googleapiclient.errors import HttpError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, load_sheets_config
from scripts.keyword_db import KEYWORD_SHEET, load_keywords
from scripts.search_history import HISTORY_SHEET, load_history, optimize_terms


DEFAULT_SPREADSHEET = "https://docs.google.com/spreadsheets/d/15mskJs84UE7-CUtwELlCnjw3_DoWpAIYnZUvqiJvrdc/edit"


def parse_spreadsheet_id(value: str) -> str:
    value = value.strip()
    match = re.search(r"/spreadsheets/d/([^/]+)", value)
    return match.group(1) if match else value


def load_terms(args: argparse.Namespace) -> List[dict]:
    config = load_sheets_config()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    service = build_sheets_service(config)
    terms = load_keywords(service, spreadsheet_id, args.keyword_sheet)
    if args.optimize_with_history:
        history = load_history(service, spreadsheet_id, args.history_sheet)
        terms = optimize_terms(terms, history, args.cooldown_hours)
    return terms[: args.limit]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="キーワードDBと検索履歴DBからMeta広告収集用の検索名を選びます。")
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET)
    parser.add_argument("--keyword-sheet", default=KEYWORD_SHEET)
    parser.add_argument("--history-sheet", default=HISTORY_SHEET)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--cooldown-hours", type=int, default=24)
    parser.add_argument("--optimize-with-history", action="store_true", default=True)
    parser.add_argument("--no-optimize-with-history", dest="optimize_with_history", action="store_false")
    parser.add_argument("--output-json", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    terms = load_terms(args)
    Path(args.output_json).write_text(json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": len(terms), "terms": terms}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HttpError as error:
        print(f"\nGoogle Sheets APIエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
