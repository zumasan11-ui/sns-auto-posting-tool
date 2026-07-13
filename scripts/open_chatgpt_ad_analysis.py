import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from dotenv import load_dotenv
import requests
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, load_sheets_config, read_values
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id, quote_sheet_name


DEFAULT_SHEET = "広告分析マスターDB"
SCREENSHOT_HEADERS = ("広告スクショ", "広告スクショURL", "スクショURL", "画像URL", "Screenshot URL", "screenshot_url")
LP_CHUNK_MAX_HEIGHT = 3200


def log(message: str) -> None:
    print(f"[chatgpt-ad-analysis] {message}", flush=True)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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


def load_sheet_row(spreadsheet_id: str, sheet_name: str, row_number: int) -> Dict[str, str]:
    service = build_sheets_service(load_sheets_config())
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:AZ{row_number}")
    if not values or row_number > len(values):
        raise RuntimeError(f"{sheet_name} の {row_number} 行目が見つかりません。")
    headers = [clean(header) for header in values[0]]
    row = values[row_number - 1]
    return {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}


def load_sheet_row_by_service(spreadsheet_id: str, sheet_name: str, service_name: str) -> tuple[int, Dict[str, str]]:
    service = build_sheets_service(load_sheets_config())
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:AZ")
    if not values:
        raise RuntimeError(f"{sheet_name} が空です。")
    headers = [clean(header) for header in values[0]]
    needle = clean(service_name).casefold()
    for row_number, row in enumerate(values[1:], start=2):
        item = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        if clean(item.get("サービス名")).casefold() == needle:
            return row_number, item
    for row_number, row in enumerate(values[1:], start=2):
        item = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        if needle in clean(item.get("サービス名")).casefold():
            return row_number, item
    raise RuntimeError(f"サービス名に一致する行が見つかりません: {service_name}")


def safe_name(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥_-]+", "_", clean(value)).strip("_")
    return name[:80] or "ad"


def first_value(row: Dict[str, str], headers: tuple[str, ...]) -> str:
    for header in headers:
        value = clean(row.get(header))
        if value:
            return value
    return ""


def build_prompt(row: Dict[str, str], files: List[Path]) -> str:
    service = clean(row.get("サービス名"))
    company = clean(row.get("会社名"))
    file_lines = "\n".join(f"- {path.name}" for path in files)
    return f"""この会社とサービスのことを短くわかりやすく解説して。

会社名：{company}
サービス名：{service}

添付ファイル：
{file_lines}

広告とLPも参考にして、専門用語をなるべく使わずに説明して。
"""


def download_image(url: str, output_path: Path) -> bool:
    if not re.match(r"^https?://", url):
        return False
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
    }
    with requests.get(url, headers=headers, stream=True, timeout=45) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type:
            parsed_ext = Path(urlparse(url).path).suffix.lower()
            if parsed_ext not in {".png", ".jpg", ".jpeg", ".webp"}:
                return False
        with output_path.open("wb") as file:
            shutil.copyfileobj(response.raw, file)
    return output_path.exists() and output_path.stat().st_size > 0


def split_tall_image(path: Path, max_height: int = LP_CHUNK_MAX_HEIGHT) -> List[Path]:
    image = Image.open(path)
    if image.height <= max_height:
        return [path]
    chunks: List[Path] = []
    for index, top in enumerate(range(0, image.height, max_height), start=1):
        bottom = min(top + max_height, image.height)
        chunk_path = path.with_name(f"{path.stem}_part_{index:02d}{path.suffix}")
        image.crop((0, top, image.width, bottom)).save(chunk_path)
        chunks.append(chunk_path)
    return chunks


def capture_assets(row: Dict[str, str], output_dir: Path, chrome_executable: str) -> List[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_file in output_dir.glob("lp_fullpage_part_*.png"):
        stale_file.unlink(missing_ok=True)
    for stale_file in ("ad_creative.png", "lp_fullpage.png", "lp_fullpage.pdf"):
        (output_dir / stale_file).unlink(missing_ok=True)
    files: List[Path] = []
    screenshot_url = first_value(row, SCREENSHOT_HEADERS)
    if screenshot_url:
        ad_image = output_dir / "ad_creative.png"
        try:
            if download_image(screenshot_url, ad_image):
                files.append(ad_image)
            else:
                log("広告スクショURLを画像として保存できませんでした。")
        except Exception as error:
            log(f"広告スクショの保存をスキップ: {error}")

    launch_options: Dict[str, Any] = {"headless": True}
    if chrome_executable:
        launch_options["executable_path"] = chrome_executable
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1800}, locale="ja-JP")
        try:
            lp_url = clean(row.get("LP URL"))
            if lp_url:
                page.goto(lp_url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(5000)
                png_path = output_dir / "lp_fullpage.png"
                page.screenshot(path=str(png_path), full_page=True)
                files.extend(split_tall_image(png_path))
                try:
                    pdf_path = output_dir / "lp_fullpage.pdf"
                    page.pdf(path=str(pdf_path), print_background=True, format="A4")
                except Exception as error:
                    log(f"LP PDFの作成をスキップ: {error}")
        finally:
            browser.close()
    return files


def click_chatgpt_send_button() -> bool:
    script = r'''
tell application "Google Chrome"
  activate
  try
    set clickResult to execute active tab of front window javascript "(() => { const buttons = Array.from(document.querySelectorAll('button')); const button = document.querySelector('button[data-testid=\"send-button\"]') || buttons.find((b) => /送信|Send/i.test(b.getAttribute('aria-label') || b.textContent || '')); if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return 'missing'; button.click(); return 'clicked'; })()"
    return clickResult
  on error errMsg
    return "error: " & errMsg
  end try
end tell
'''
    result = subprocess.run(["/usr/bin/osascript"], input=script, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return clean(result.stdout) == "clicked"


def open_chatgpt(prompt: str, files: List[Path], args: argparse.Namespace) -> None:
    if args.system_chrome:
        subprocess.run(["/usr/bin/pbcopy"], input=prompt, text=True, check=True)
        subprocess.run(["/usr/bin/open", "-a", "Google Chrome", args.chatgpt_url], check=False)
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'tell application "Google Chrome" to activate',
                "-e",
                'delay 0.5',
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            check=False,
        )
        if files:
            png_files = [path for path in files if path.suffix.lower() == ".png"]
            for png_file in png_files:
                png_path = str(png_file.resolve()).replace('"', '\\"')
                script = f"""tell application "Google Chrome" to activate
delay 0.5
set imageData to (read POSIX file "{png_path}" as «class PNGf»)
set the clipboard to imageData
delay 0.2
tell application "System Events" to keystroke "v" using command down
delay 2
"""
                subprocess.run(["/usr/bin/osascript"], input=script, text=True, check=False)
        if args.submit:
            if click_chatgpt_send_button():
                log("通常のChromeでChatGPTへプロンプトとPNGスクショを入れて、送信ボタンをクリックしました。")
            else:
                subprocess.run(
                    [
                        "/usr/bin/osascript",
                        "-e",
                        'tell application "Google Chrome" to activate',
                        "-e",
                        "delay 1",
                        "-e",
                        'tell application "System Events" to key code 36',
                    ],
                    check=False,
                )
                log("送信ボタンを直接クリックできなかったため、Enterで送信を試しました。")
        else:
            log("通常のChromeでChatGPTを開き、プロンプト貼り付けとPNGスクショ添付を試しました。")
        return

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    user_data_dir = Path(args.chatgpt_profile).expanduser()
    user_data_dir.mkdir(parents=True, exist_ok=True)
    launch_options: Dict[str, Any] = {
        "headless": False,
        "viewport": {"width": 1440, "height": 1100},
        "locale": "ja-JP",
    }
    if args.chrome_executable:
        launch_options["executable_path"] = args.chrome_executable
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(user_data_dir), **launch_options)
        page = context.new_page()
        page.goto(args.chatgpt_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)
        if "auth" in page.url or "login" in page.url:
            log("ChatGPTにログインしてください。ログイン後、このスクリプトは最大5分待ちます。")
            try:
                page.wait_for_url(re.compile(r"https://chatgpt\\.com/.*"), timeout=300000)
            except PlaywrightTimeoutError:
                log("ログイン待ちがタイムアウトしました。プロンプトと素材は作成済みです。")
                return
        file_inputs = page.locator("input[type=file]")
        if files:
            try:
                file_inputs.first.set_input_files([str(path) for path in files], timeout=15000)
                page.wait_for_timeout(5000)
            except Exception as error:
                log(f"ファイル自動添付は失敗しました: {error}")
                log("生成済みファイルを手動で添付してください。")
        editor = page.locator("textarea, div[contenteditable='true']").last
        try:
            editor.fill(prompt, timeout=15000)
        except Exception:
            page.keyboard.insert_text(prompt)
        if args.submit:
            sent = False
            for selector in (
                "button[data-testid='send-button']",
                "button[aria-label*='送信']",
                "button[aria-label*='Send']",
            ):
                try:
                    button = page.locator(selector).last
                    button.wait_for(state="visible", timeout=15000)
                    button.click(timeout=15000)
                    sent = True
                    break
                except Exception:
                    continue
            if not sent:
                try:
                    page.keyboard.press("Enter")
                    sent = True
                except Exception:
                    pass
            if sent:
                log("ChatGPTへ送信しました。回答生成が始まっているはずです。")
            else:
                log("送信ボタンを押せませんでした。画面上で送信だけ手動で押してください。")
        else:
            log("ChatGPTの新規チャットに素材とプロンプトを準備しました。内容を確認して送信してください。")
        page.wait_for_timeout(args.keep_open_seconds * 1000)
        context.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="マスターDBの広告行から素材を作り、ChatGPT新規チャットへ持ち込みます。")
    parser.add_argument("--spreadsheet", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_SPREADSHEET))
    parser.add_argument("--sheet-name", default=os.getenv("AD_ANALYSIS_MASTER_SHEET", DEFAULT_SHEET))
    parser.add_argument("--row", type=int)
    parser.add_argument("--service-name", help="サービス名で対象行を探します。")
    parser.add_argument("--output-dir", default="deliverables/chatgpt_ad_analysis")
    parser.add_argument("--chrome-executable", default=os.getenv("CHROME_EXECUTABLE", system_chrome_path()))
    parser.add_argument("--chatgpt-profile", default="deliverables/chatgpt_profile")
    parser.add_argument("--chatgpt-url", default="https://chatgpt.com/")
    parser.add_argument("--keep-open-seconds", type=int, default=3600)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--system-chrome", action="store_true", help="通常のChromeでChatGPTを開き、プロンプトをクリップボードへコピーします。")
    parser.add_argument("--submit", action="store_true", help="ChatGPTへプロンプトを入力後、自動で送信します。")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    if args.service_name:
        row_number, row = load_sheet_row_by_service(spreadsheet_id, args.sheet_name, args.service_name)
    elif args.row:
        row_number = args.row
        row = load_sheet_row(spreadsheet_id, args.sheet_name, row_number)
    else:
        raise RuntimeError("--row か --service-name のどちらかを指定してください。")
    output_dir = Path(args.output_dir) / f"row_{row_number}_{safe_name(row.get('サービス名', ''))}"
    files = capture_assets(row, output_dir, args.chrome_executable)
    prompt = build_prompt(row, files)
    prompt_path = output_dir / "chatgpt_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    log(f"素材出力: {output_dir}")
    log(f"プロンプト: {prompt_path}")
    if args.prepare_only:
        return 0
    open_chatgpt(prompt, files, args)
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
