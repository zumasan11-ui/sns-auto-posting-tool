import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, load_sheets_config, read_values, update_values
from scripts.append_meta_visible_rows_to_sheet import (
    DEFAULT_SPREADSHEET,
    fetch_lp_title,
    infer_company_name,
    parse_spreadsheet_id,
    quote_sheet_name,
)


TODAY_SHEET = "今日の広告DB"


def log(message: str) -> None:
    print(f"[normalize-today-ad-names] {message}", flush=True)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def col_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def load_rows(service: Any, spreadsheet_id: str, sheet_name: str) -> tuple[List[str], List[Dict[str, Any]]]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:AZ")
    if not values:
        return [], []
    headers = [clean(header) for header in values[0]]
    rows: List[Dict[str, Any]] = []
    for row_number, row in enumerate(values[1:], start=2):
        item = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        item["_row_number"] = row_number
        rows.append(item)
    return headers, rows


def is_open_ad(row: Dict[str, Any]) -> bool:
    status = clean(row.get("ステータス") or row.get("分析状況"))
    return bool(clean(row.get("広告ライブラリURL"))) and status not in {"分析済み", "投稿済み", "完了"} and not clean(row.get("広告分析"))


def service_name_from_text(text: str) -> str:
    lines = [clean(line) for line in text.splitlines() if clean(line)]
    ignored = {
        "アクティブ",
        "Active",
        "広告の詳細を見る",
        "概要詳細を見る",
        "スポンサー広告",
        "Sponsored",
    }
    for index, line in enumerate(lines):
        if line in {"スポンサー広告", "Sponsored"} and index > 0:
            candidate = lines[index - 1]
            if candidate and candidate not in ignored and len(candidate) <= 80:
                return candidate
    patterns = [
        r"(?:広告の詳細を見る|概要詳細を見る)\s+(.{2,80}?)\s+スポンサー広告",
        r"(.{2,80}?)\s+スポンサー広告",
    ]
    one_line = clean(text)
    for pattern in patterns:
        match = re.search(pattern, one_line)
        if match:
            return clean(match.group(1))
    return ""


def fetch_service_name(url: str, args: argparse.Namespace) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    launch_options: Dict[str, Any] = {"headless": args.headless}
    if args.chrome_executable:
        launch_options["executable_path"] = args.chrome_executable
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1280, "height": 1200}, locale="ja-JP")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(args.wait_ms)
            text = page.locator("body").inner_text(timeout=10000)
        finally:
            browser.close()
    return service_name_from_text(text)


def update_row(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str], row_number: int, updates: Dict[str, Any], dry_run: bool) -> None:
    for header, value in updates.items():
        if header not in headers:
            continue
        column = col_letter(headers.index(header))
        log(f"{row_number}: {header} -> {value}")
        if not dry_run:
            update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!{column}{row_number}:{column}{row_number}", [[value]])


def normalize_rows(args: argparse.Namespace) -> int:
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    config = load_sheets_config()
    service = build_sheets_service(config)
    headers, rows = load_rows(service, spreadsheet_id, args.sheet_name)
    changed = 0
    for row in rows:
        if not is_open_ad(row):
            continue
        row_number = int(row["_row_number"])
        url = clean(row.get("広告ライブラリURL"))
        lp_url = clean(row.get("LP URL"))
        try:
            service_name = fetch_service_name(url, args)
        except Exception as error:
            log(f"{row_number}: サービス名取得エラー / {error}")
            service_name = clean(row.get("サービス名"))
        lp_title = fetch_lp_title(lp_url)
        company_name = infer_company_name(
            {
                "会社名": "",
                "LP URL": lp_url,
                "サービス名": service_name,
            },
            lp_title,
            service_name,
            allow_search=True,
        )
        updates = {}
        if service_name and service_name != clean(row.get("サービス名")):
            updates["サービス名"] = service_name
        if company_name != clean(row.get("会社名")):
            updates["会社名"] = company_name
        if updates:
            changed += 1
            update_row(service, spreadsheet_id, args.sheet_name, headers, row_number, updates, args.dry_run)
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="今日の広告DBのサービス名と会社名をMeta表示名基準で補正します。")
    parser.add_argument("--spreadsheet", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_SPREADSHEET))
    parser.add_argument("--sheet-name", default=os.getenv("TODAY_AD_DB_SHEET", TODAY_SHEET))
    parser.add_argument("--chrome-executable", default=os.getenv("CHROME_EXECUTABLE", ""))
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--wait-ms", type=int, default=5000)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    changed = normalize_rows(args)
    log(f"更新対象: {changed}件")
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
