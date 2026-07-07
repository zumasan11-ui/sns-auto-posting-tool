import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, load_sheets_config, read_values, update_values
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id, quote_sheet_name


MASTER_SHEET = "広告分析マスターDB"
STATUS_HEADERS = ["状況", "最終掲載期間"]
STOPPED_PATTERNS = (
    "この広告は現在掲載されていません",
    "この広告は現在配信されていません",
    "この広告は掲載されていません",
    "この広告は配信されていません",
    "This ad is inactive",
    "This ad is no longer active",
    "This ad is not currently running",
    "Inactive",
)
ACTIVE_PATTERNS = ("アクティブ", "Active", "開始日", "Started running", "Library ID", "ライブラリID")


def log(message: str) -> None:
    print(f"[ad-status-check] {message}", flush=True)


def today_jst() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Tokyo"))


def today_text() -> str:
    return today_jst().strftime("%Y-%m-%d")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def resolve_sheet_name(service: Any, spreadsheet_id: str, requested_name: str) -> str:
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [sheet.get("properties", {}).get("title", "") for sheet in spreadsheet.get("sheets", [])]
    if requested_name in titles:
        return requested_name
    stripped = requested_name.strip()
    for title in titles:
        if title.strip() == stripped:
            return title
    return requested_name


def col_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def parse_date(value: Any) -> Optional[datetime]:
    text = clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            pass
    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def parse_duration_days(start_date: Any, end_date: datetime) -> int:
    start = parse_date(start_date)
    if not start:
        return 0
    return max((end_date.replace(tzinfo=None) - start).days, 0)


def format_duration(days: int) -> str:
    months = max(days // 30, 0)
    if days < 365:
        return f"{months}ヶ月"
    years = months // 12
    remaining_months = months % 12
    return f"{years}年{remaining_months}ヶ月" if remaining_months else f"{years}年"


def ensure_status_headers(service: Any, spreadsheet_id: str, sheet_name: str) -> List[str]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:AZ1")
    headers = [clean(value) for value in values[0]] if values else []
    changed = False
    for header in STATUS_HEADERS:
        if header not in headers:
            headers.append(header)
            changed = True
    if changed:
        end = col_letter(len(headers) - 1)
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:{end}1", [headers])
    return headers


def load_master_rows(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str]) -> List[Dict[str, Any]]:
    end = col_letter(max(len(headers) - 1, 0))
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A2:{end}")
    rows = []
    for row_number, row in enumerate(values, start=2):
        item = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        item["_row_number"] = row_number
        rows.append(item)
    return rows


def select_rows(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    today = today_jst().replace(tzinfo=None)

    def sort_key(row: Dict[str, Any]) -> tuple:
        analysis_date = parse_date(row.get("分析日"))
        return (analysis_date or datetime.min, int(row.get("_row_number", 0)))

    candidates = []
    for row in rows:
        if not clean(row.get("広告ライブラリURL")):
            continue
        if clean(row.get("状況") or row.get("状態")) == "掲載停止":
            continue
        analysis_date = parse_date(row.get("分析日"))
        if not analysis_date:
            continue
        if (today - analysis_date).days < 30:
            continue
        candidates.append(row)
    return sorted(candidates, key=sort_key)[:limit]


def check_ad_url(url: str, args: argparse.Namespace) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    launch_options: Dict[str, Any] = {"headless": args.headless}
    if args.chrome_executable:
        launch_options["executable_path"] = args.chrome_executable
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1280, "height": 1000}, locale="ja-JP")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(args.wait_ms)
            text = page.locator("body").inner_text(timeout=10000)
        finally:
            browser.close()
    normalized = clean(text)
    if any(pattern in normalized for pattern in STOPPED_PATTERNS):
        return "掲載停止"
    if any(pattern in normalized for pattern in ACTIVE_PATTERNS):
        return "掲載中"
    raise RuntimeError("掲載状態を判定できませんでした。")


def update_row(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str], row_number: int, updates: Dict[str, Any]) -> None:
    for header, value in updates.items():
        if header not in headers:
            continue
        column = col_letter(headers.index(header))
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!{column}{row_number}:{column}{row_number}", [[value]])


def check_rows(args: argparse.Namespace) -> Dict[str, Any]:
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    config = load_sheets_config()
    service = build_sheets_service(config)
    sheet_name = resolve_sheet_name(service, spreadsheet_id, args.sheet_name)
    headers = ensure_status_headers(service, spreadsheet_id, sheet_name)
    rows = load_master_rows(service, spreadsheet_id, sheet_name, headers)
    targets = select_rows(rows, args.limit)
    checked = stopped = errors = 0
    details = []
    today = today_jst()
    for row in targets:
        row_number = int(row["_row_number"])
        url = clean(row.get("広告ライブラリURL"))
        try:
            status = check_ad_url(url, args)
            days = parse_duration_days(row.get("掲載開始日"), today)
            updates = {
                "状況": status,
            }
            if status == "掲載停止":
                stopped += 1
                updates["最終掲載期間"] = format_duration(days)
            if not args.dry_run:
                update_row(service, spreadsheet_id, sheet_name, headers, row_number, updates)
            checked += 1
            details.append({"row": row_number, "url": url, "status": status})
            log(f"{row_number}: {status} / {url}")
        except Exception as error:
            errors += 1
            message = str(error)[:500]
            details.append({"row": row_number, "url": url, "error": message})
            log(f"{row_number}: エラー / {message}")
    return {"checked": checked, "stopped": stopped, "errors": errors, "details": details}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="広告分析マスターDBの広告が現在も掲載中か月次確認します。")
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET)
    parser.add_argument("--sheet-name", default=MASTER_SHEET)
    parser.add_argument("--limit", type=int, default=int(__import__("os").getenv("AD_STATUS_CHECK_LIMIT", "50")))
    parser.add_argument("--chrome-executable", default=__import__("os").getenv("CHROME_EXECUTABLE", ""))
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--wait-ms", type=int, default=5000)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    result = check_rows(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
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
