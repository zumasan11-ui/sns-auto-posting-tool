import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, load_sheets_config, read_values, update_values
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id, quote_sheet_name


TODAY_SHEET = "広告分析マスターDB"
SCREENSHOT_HEADERS = ("広告スクショ", "広告スクショURL", "スクショURL", "画像URL", "Screenshot URL", "screenshot_url")
COPY_HEADERS = ("コピー", "広告コピー", "画像コピー", "クリエイティブコピー", "copy")


def log(message: str) -> None:
    print(f"[fill-ad-copy] {message}", flush=True)


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


def first_header(headers: List[str], candidates: tuple[str, ...]) -> str:
    for header in candidates:
        if header in headers:
            return header
    return ""


def first_value(row: Dict[str, Any], headers: tuple[str, ...]) -> str:
    for header in headers:
        value = clean(row.get(header))
        if value:
            return value
    return ""


def is_open_ad(row: Dict[str, Any]) -> bool:
    status = clean(row.get("ステータス") or row.get("分析状況"))
    return bool(clean(row.get("広告ライブラリURL"))) and status not in {"分析済み", "投稿済み", "完了"}


def download_image(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def ocr_with_ocrmac(image_path: Path) -> str:
    try:
        from ocrmac import ocrmac
    except Exception:
        return ""
    try:
        annotations = ocrmac.OCR(str(image_path), language_preference=["ja-JP", "en-US"]).recognize()
    except Exception as error:
        log(f"OCRエラー: {error}")
        return ""
    lines: List[str] = []
    for item in annotations or []:
        if isinstance(item, (list, tuple)) and item:
            text = str(item[0])
        else:
            text = str(item)
        text = text.strip()
        if text:
            lines.append(text)
    return "\n".join(dict.fromkeys(lines))


def ocr_with_vision_swift(image_path: Path) -> str:
    source = ROOT_DIR / "scripts" / "vision_ocr.swift"
    if not source.exists():
        return ""
    binary = Path("/private/tmp/vision_ocr")
    try:
        if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
            env = os.environ.copy()
            env["CLANG_MODULE_CACHE_PATH"] = "/private/tmp/clang-module-cache"
            subprocess.run(
                ["swiftc", str(source), "-o", str(binary)],
                cwd=ROOT_DIR,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        result = subprocess.run(
            [str(binary), str(image_path)],
            cwd=ROOT_DIR,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
    except Exception as error:
        log(f"Vision OCR起動エラー: {error}")
        return ""
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if message:
            log(f"Vision OCRエラー: {message[:300]}")
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return "\n".join(dict.fromkeys(lines))


def extract_copy(image_url: str, work_dir: Path, row_number: int) -> str:
    suffix = ".jpg"
    match = re.search(r"\.(png|jpe?g|webp)(?:$|[?#])", image_url, flags=re.I)
    if match:
        suffix = "." + match.group(1).lower().replace("jpeg", "jpg")
    image_path = download_image(image_url, work_dir / f"row_{row_number}{suffix}")
    return ocr_with_vision_swift(image_path) or ocr_with_ocrmac(image_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="広告分析マスターDBの広告スクショから画像内コピーをOCRし、コピー列へ補完します。")
    parser.add_argument("--spreadsheet", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_SPREADSHEET))
    parser.add_argument("--sheet-name", default=os.getenv("AD_ANALYSIS_MASTER_SHEET", TODAY_SHEET))
    parser.add_argument("--limit", type=int, default=0, help="処理する最大件数。0なら対象すべて。")
    parser.add_argument("--overwrite", action="store_true", help="既存のコピーも再OCRして上書きします。")
    parser.add_argument("--work-dir", default="deliverables/ad_copy_ocr")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    config = load_sheets_config()
    service = build_sheets_service(config)
    headers, rows = load_rows(service, spreadsheet_id, args.sheet_name)
    copy_header = first_header(headers, COPY_HEADERS)
    if not copy_header:
        raise RuntimeError("コピー列が見つかりません。")
    screenshot_header = first_header(headers, SCREENSHOT_HEADERS)
    if not screenshot_header:
        raise RuntimeError("広告スクショ列が見つかりません。")

    copy_column = col_letter(headers.index(copy_header))
    work_dir = Path(args.work_dir)
    updated = 0
    attempted = 0
    for row in rows:
        if args.limit and attempted >= args.limit:
            break
        if not is_open_ad(row):
            continue
        row_number = int(row["_row_number"])
        image_url = first_value(row, SCREENSHOT_HEADERS)
        if not image_url:
            continue
        if clean(row.get(copy_header)) and not args.overwrite:
            continue
        attempted += 1
        try:
            text = extract_copy(image_url, work_dir, row_number).strip()
        except Exception as error:
            log(f"{row_number}: コピー取得エラー / {error}")
            continue
        if not text:
            log(f"{row_number}: OCR結果なし")
            continue
        log(f"{row_number}: コピー設定 / {text.splitlines()[0][:80]}")
        if not args.dry_run:
            update_values(
                service,
                spreadsheet_id,
                f"{quote_sheet_name(args.sheet_name)}!{copy_column}{row_number}:{copy_column}{row_number}",
                [[text]],
                value_input_option="USER_ENTERED",
            )
        updated += 1
    log(f"処理対象: {attempted}件 / 更新: {updated}件")
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
