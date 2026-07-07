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
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id, quote_sheet_name


TODAY_SHEET = "今日の広告DB"
SCREENSHOT_HEADERS = ("広告スクショ", "広告スクショURL", "スクショURL", "画像URL", "Screenshot URL", "screenshot_url")


def log(message: str) -> None:
    print(f"[fill-ad-screenshots] {message}", flush=True)


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


def screenshot_header(headers: List[str]) -> str:
    for header in SCREENSHOT_HEADERS:
        if header in headers:
            return header
    return ""


def is_open_ad(row: Dict[str, Any]) -> bool:
    status = clean(row.get("ステータス") or row.get("分析状況"))
    return bool(clean(row.get("広告ライブラリURL"))) and status not in {"分析済み", "投稿済み", "完了"} and not clean(row.get("広告分析"))


def extract_creative_image_url(ad_url: str, args: argparse.Namespace) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    launch_options: Dict[str, Any] = {"headless": args.headless}
    if args.chrome_executable:
        launch_options["executable_path"] = args.chrome_executable

    js = """
    () => {
      const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const targetId = new URL(location.href).searchParams.get('id') || '';
      const nodes = Array.from(document.querySelectorAll('div, article, section'));
      const candidates = [];
      for (const node of nodes) {
        const text = norm(node.innerText);
        if (!targetId || !text.includes(targetId) || !/スポンサー広告|Sponsored/.test(text)) continue;
        const rect = node.getBoundingClientRect();
        if (rect.width < 250 || rect.height < 250 || rect.height > 2500) continue;
        const imgs = Array.from(node.querySelectorAll('img')).map((img) => {
          const imgRect = img.getBoundingClientRect();
          return {
            url: img.currentSrc || img.src,
            alt: norm(img.getAttribute('alt')),
            w: Math.round(imgRect.width),
            h: Math.round(imgRect.height),
            x: Math.round(imgRect.x),
            y: Math.round(imgRect.y),
          };
        }).filter((img) => {
          if (!img.url || !/^https?:/.test(img.url)) return false;
          if (img.w < 120 || img.h < 80) return false;
          if (/scontent|fbcdn|akamai|cdn/i.test(img.url) === false) return false;
          return true;
        });
        if (!imgs.length) continue;
        candidates.push({ node, rect, textLength: text.length, imgs });
      }
      candidates.sort((a, b) => {
        const areaA = Math.round(a.rect.width * a.rect.height);
        const areaB = Math.round(b.rect.width * b.rect.height);
        return (a.textLength - b.textLength) || (areaA - areaB);
      });
      const root = candidates[0] || { imgs: [] };
      const imgs = root.imgs;
      imgs.sort((a, b) => (b.w * b.h) - (a.w * a.h));
      return imgs[0] ? imgs[0].url : '';
    }
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1400}, locale="ja-JP")
        try:
            page.goto(ad_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(args.wait_ms)
            return clean(page.evaluate(js))
        finally:
            browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="今日の広告DBにMeta広告クリエイティブ画像URLを補完します。")
    parser.add_argument("--spreadsheet", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_SPREADSHEET))
    parser.add_argument("--sheet-name", default=os.getenv("TODAY_AD_DB_SHEET", TODAY_SHEET))
    parser.add_argument("--chrome-executable", default=os.getenv("CHROME_EXECUTABLE", ""))
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--wait-ms", type=int, default=5000)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--overwrite", action="store_true", help="既存の広告スクショURLも再取得して上書きします。")
    parser.add_argument("--only-url", action="append", default=[], help="指定した広告ライブラリURLだけ処理します。複数指定可。")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    config = load_sheets_config()
    service = build_sheets_service(config)
    headers, rows = load_rows(service, spreadsheet_id, args.sheet_name)
    header = screenshot_header(headers)
    if not header:
        raise RuntimeError("広告スクショ列が見つかりません。")
    column = col_letter(headers.index(header))
    updated = 0
    only_urls = {clean(url) for url in args.only_url if clean(url)}
    for row in rows:
        if not is_open_ad(row):
            continue
        ad_url = clean(row.get("広告ライブラリURL"))
        if only_urls and ad_url not in only_urls:
            continue
        if clean(row.get(header)) and not args.overwrite:
            continue
        row_number = int(row["_row_number"])
        try:
            image_url = extract_creative_image_url(ad_url, args)
        except Exception as error:
            log(f"{row_number}: 画像取得エラー / {error}")
            continue
        if not image_url:
            log(f"{row_number}: 画像URLなし")
            continue
        log(f"{row_number}: 画像URL設定 / {image_url[:120]}")
        if not args.dry_run:
            update_values(service, spreadsheet_id, f"{quote_sheet_name(args.sheet_name)}!{column}{row_number}:{column}{row_number}", [[image_url]])
        updated += 1
    log(f"更新: {updated}件")
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
