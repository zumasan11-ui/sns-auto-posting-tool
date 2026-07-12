import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SPREADSHEET_ID = "15mskJs84UE7-CUtwELlCnjw3_DoWpAIYnZUvqiJvrdc"


def run(cmd: list[str]) -> None:
    print("[today-research-to-notion] " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT_DIR, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="今日の広告DBを指定件数に整え、Notion日次分析ページを作成します。")
    parser.add_argument("--spreadsheet", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", "") or DEFAULT_SPREADSHEET_ID)
    parser.add_argument("--today-sheet", default=os.getenv("TODAY_AD_DB_SHEET", "今日の広告DB"))
    parser.add_argument("--count", type=int, default=int(os.getenv("DAILY_AD_ANALYSIS_COUNT", "1")))
    parser.add_argument("--search-limit", type=int, default=int(os.getenv("DAILY_SEARCH_LIMIT", "5")))
    parser.add_argument("--per-search-max", type=int, default=int(os.getenv("PER_SEARCH_AD_MAX", "5")))
    parser.add_argument("--scrolls", type=int, default=10)
    parser.add_argument("--chrome-executable", default=os.getenv("CHROME_EXECUTABLE", ""))
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
    args = parse_args()
    spreadsheet = args.spreadsheet or os.getenv("AD_ANALYSIS_SPREADSHEET_ID", "") or DEFAULT_SPREADSHEET_ID

    replace_cmd = [
        sys.executable,
        "scripts/replace_today_carousel_ads.py",
        "--spreadsheet",
        spreadsheet,
        "--sheet-name",
        args.today_sheet,
        "--daily-target",
        str(args.count),
        "--search-limit",
        str(args.search_limit),
        "--per-search-max",
        str(args.per_search_max),
        "--scrolls",
        str(args.scrolls),
    ]
    if args.chrome_executable:
        replace_cmd.extend(["--chrome-executable", args.chrome_executable])
    if not args.headless:
        replace_cmd.append("--no-headless")
    if args.dry_run:
        replace_cmd.append("--dry-run")
    run(replace_cmd)

    screenshot_cmd = [
        sys.executable,
        "scripts/fill_today_ad_screenshots.py",
        "--spreadsheet",
        spreadsheet,
        "--sheet-name",
        args.today_sheet,
    ]
    if args.chrome_executable:
        screenshot_cmd.extend(["--chrome-executable", args.chrome_executable])
    if not args.headless:
        screenshot_cmd.append("--no-headless")
    if args.dry_run:
        screenshot_cmd.append("--dry-run")
    run(screenshot_cmd)

    copy_cmd = [
        sys.executable,
        "scripts/fill_today_ad_copy.py",
        "--spreadsheet",
        spreadsheet,
        "--sheet-name",
        args.today_sheet,
        "--limit",
        str(args.count),
    ]
    if args.dry_run:
        copy_cmd.append("--dry-run")
    run(copy_cmd)

    format_cmd = [
        sys.executable,
        "scripts/sheet_formatting.py",
        "--spreadsheet",
        spreadsheet,
        "--sheet",
        args.today_sheet,
        "--pixel-size",
        "20",
    ]
    if not args.dry_run:
        run(format_cmd)

    page_cmd = [
        sys.executable,
        "scripts/prepare_daily_ad_analysis_page.py",
        "--spreadsheet-id",
        spreadsheet,
        "--sheet-name",
        args.today_sheet,
        "--count",
        str(args.count),
    ]
    if args.dry_run:
        page_cmd.append("--dry-run")
    run(page_cmd)

    if not args.dry_run:
        run([sys.executable, "scripts/format_notion_ad_metadata.py"])
        repair_cmd = [
            sys.executable,
            "scripts/repair_notion_ad_images.py",
        ]
        if args.chrome_executable:
            repair_cmd.extend(["--chrome-executable", args.chrome_executable])
        if not args.headless:
            repair_cmd.append("--no-headless")
        run(repair_cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
