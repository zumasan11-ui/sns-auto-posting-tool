import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, get_spreadsheet, load_sheets_config, read_values
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id, quote_sheet_name


TODAY_SHEET = "今日の広告DB"
DEFAULT_TARGET = 8
CAROUSEL_PATTERNS = (
    "ad-library-ad-carousel-container",
    "カルーセル",
    "carousel",
    "次のカード",
    "前のカード",
    "次のアイテム",
    "前のアイテム",
    "Next card",
    "Previous card",
    "Next item",
    "Previous item",
)


def log(message: str) -> None:
    print(f"[replace-carousel-ads] {message}", flush=True)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def col_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def sheet_id_for(service: Any, spreadsheet_id: str, sheet_name: str) -> int:
    spreadsheet = get_spreadsheet(service, spreadsheet_id)
    for sheet in spreadsheet.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") == sheet_name:
            return int(properties["sheetId"])
    raise RuntimeError(f"シートが見つかりません: {sheet_name}")


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


def is_carousel_from_row(row: Dict[str, Any]) -> bool:
    media_type = clean(row.get("media_type") or row.get("メディア種別")).lower()
    if "carousel" in media_type or "カルーセル" in media_type:
        return True
    text = " ".join(clean(row.get(key)) for key in ("広告本文", "広告タイトル", "サービス名"))
    return bool(re.search(r"ad-library-ad-carousel-container|カルーセル|carousel|次のアイテム|前のアイテム|Next item|Previous item|(?:カード|Card)\s*\d+\s*(?:\/|／|of)\s*\d+", text, flags=re.I))


def is_carousel_in_browser(url: str, args: argparse.Namespace) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    launch_options: Dict[str, Any] = {"headless": args.headless}
    if args.chrome_executable:
        launch_options["executable_path"] = args.chrome_executable
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1280, "height": 1100}, locale="ja-JP")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(args.wait_ms)
            result = page.evaluate(
                """
                () => {
                  const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const text = norm(document.body ? document.body.innerText : '');
                  const labels = Array.from(document.querySelectorAll('[aria-label], [aria-roledescription], [title], [role], [data-testid]')).map((node) =>
                    norm([node.getAttribute('aria-label'), node.getAttribute('aria-roledescription'), node.getAttribute('title'), node.getAttribute('role'), node.getAttribute('data-testid')].filter(Boolean).join(' '))
                  );
                  const labelText = labels.join(' ');
                  const imageCount = Array.from(document.querySelectorAll('img')).filter((img) => {
                    const rect = img.getBoundingClientRect();
                    return rect.width >= 120 && rect.height >= 80;
                  }).length;
                  const navLikeCount = labels.filter((label) => /次へ|前へ|次のアイテム|前のアイテム|Next|Previous|戻る|進む/i.test(label)).length;
                  return {
                    text,
                    labelText,
                    imageCount,
                    navLikeCount,
                  };
                }
                """
            )
        finally:
            browser.close()

    text = clean(result.get("text", ""))
    label_text = clean(result.get("labelText", ""))
    combined = f"{text} {label_text}"
    if any(pattern.lower() in combined.lower() for pattern in CAROUSEL_PATTERNS):
        return True
    if re.search(r"ad-library-ad-carousel-container|カルーセル|carousel|次のアイテム|前のアイテム|Next item|Previous item|(?:カード|Card)\s*\d+\s*(?:\/|／|of)\s*\d+", combined, flags=re.I):
        return True
    return int(result.get("navLikeCount") or 0) >= 1 and int(result.get("imageCount") or 0) >= 2


def delete_rows(service: Any, spreadsheet_id: str, sheet_name: str, row_numbers: List[int], dry_run: bool) -> None:
    if not row_numbers:
        return
    if dry_run:
        log(f"dry-run: 削除予定行 {row_numbers}")
        return
    sheet_id = sheet_id_for(service, spreadsheet_id, sheet_name)
    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row_number - 1,
                    "endIndex": row_number,
                }
            }
        }
        for row_number in sorted(set(row_numbers), reverse=True)
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def refill_today_db(args: argparse.Namespace) -> None:
    if args.dry_run:
        log("dry-run: 入れ替え後の広告収集は実行しません。")
        return
    cmd = [
        sys.executable,
        "scripts/collect_meta_ads_today.py",
        "--spreadsheet",
        args.spreadsheet,
        "--today-sheet",
        args.sheet_name,
        "--daily-target",
        str(args.daily_target),
        "--search-limit",
        str(args.search_limit),
        "--per-search-max",
        str(args.per_search_max),
        "--scrolls",
        str(args.scrolls),
    ]
    if args.chrome_executable:
        cmd.extend(["--chrome-executable", args.chrome_executable])
    if not args.headless:
        cmd.append("--no-headless")
    subprocess.run(cmd, cwd=ROOT_DIR, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="今日の広告DB内のカルーセル広告を削除し、不足分を再収集します。")
    parser.add_argument("--spreadsheet", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_SPREADSHEET))
    parser.add_argument("--sheet-name", default=os.getenv("TODAY_AD_DB_SHEET", TODAY_SHEET))
    parser.add_argument("--daily-target", type=int, default=int(os.getenv("DAILY_AD_TARGET", DEFAULT_TARGET)))
    parser.add_argument("--search-limit", type=int, default=int(os.getenv("DAILY_SEARCH_LIMIT", "5")))
    parser.add_argument("--per-search-max", type=int, default=int(os.getenv("PER_SEARCH_AD_MAX", "5")))
    parser.add_argument("--scrolls", type=int, default=10)
    parser.add_argument("--chrome-executable", default=os.getenv("CHROME_EXECUTABLE", ""))
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--wait-ms", type=int, default=5000)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--remove-url", action="append", default=[], help="カルーセル扱いで必ず削除する広告ライブラリURL。複数指定可。")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    config = load_sheets_config()
    service = build_sheets_service(config)
    _headers, rows = load_rows(service, spreadsheet_id, args.sheet_name)
    target_rows = [row for row in rows if is_open_ad(row)]
    log(f"確認対象: {len(target_rows)}件")
    forced_remove_urls = {clean(url) for url in args.remove_url if clean(url)}
    carousel_rows: List[int] = []
    for row in target_rows:
        row_number = int(row["_row_number"])
        url = clean(row.get("広告ライブラリURL"))
        try:
            carousel = url in forced_remove_urls or is_carousel_from_row(row) or is_carousel_in_browser(url, args)
            log(f"{row_number}: {'カルーセル' if carousel else '通常'} / {clean(row.get('会社名'))} / {clean(row.get('サービス名'))}")
            if carousel:
                carousel_rows.append(row_number)
        except Exception as error:
            log(f"{row_number}: 判定エラー / {error}")
    delete_rows(service, spreadsheet_id, args.sheet_name, carousel_rows, args.dry_run)
    log(f"削除したカルーセル広告: {len(carousel_rows)}件")
    refill_today_db(args)
    print(json.dumps({"carousel_removed": len(carousel_rows), "rows": carousel_rows}, ensure_ascii=False, indent=2))
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
