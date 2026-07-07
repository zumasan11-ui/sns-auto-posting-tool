import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PlaywrightでMeta広告ライブラリを検索し、表示済みカード抽出JSONを保存します。")
    parser.add_argument("--search-name", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--extractor", default="scripts/meta_ad_library_visible_cards_extractor.js")
    parser.add_argument("--scrolls", type=int, default=10)
    parser.add_argument("--min-duration-days", type=int, default=90)
    parser.add_argument("--max-rows", type=int, default=20)
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--chrome-executable", default="")
    return parser.parse_args()


def system_chrome_path() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return ""


def build_url(search_name: str) -> str:
    from urllib.parse import urlencode

    params = {
        "active_status": "active",
        "ad_type": "all",
        "country": "JP",
        "is_targeted_country": "false",
        "media_type": "all",
        "q": search_name,
        "search_type": "keyword_unordered",
    }
    return "https://www.facebook.com/ads/library/?" + urlencode(params)


def main() -> int:
    args = parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。`pip install -r requirements.txt` と `python -m playwright install chromium` を実行してください。") from error

    extractor = Path(args.extractor).read_text(encoding="utf-8")
    launch_options: Dict[str, Any] = {"headless": args.headless}
    chrome = args.chrome_executable or system_chrome_path()
    if chrome:
        launch_options["executable_path"] = chrome

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1200}, locale="ja-JP")
        try:
            page.goto(build_url(args.search_name), wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(7000)
            for _ in range(args.scrolls):
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(1800)
            page.evaluate(
                "(payload) => { window.META_AD_EXTRACTOR_OPTIONS = payload.options; eval(payload.extractor); }",
                {
                    "options": {
                        "searchName": args.search_name,
                        "minDurationDays": args.min_duration_days,
                        "maxRows": args.max_rows,
                        "excludeVideoAds": True,
                        "copyToClipboard": False,
                    },
                    "extractor": extractor,
                },
            )
            result = page.evaluate("() => window.META_AD_EXTRACTOR_LAST_RESULT || { rows: [] }")
        finally:
            browser.close()

    rows = result.get("rows", []) if isinstance(result, dict) else []
    Path(args.output_json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"searchName": args.search_name, "rows": len(rows), "outputJson": args.output_json}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"エラー: {error}", file=sys.stderr)
        raise SystemExit(1)
