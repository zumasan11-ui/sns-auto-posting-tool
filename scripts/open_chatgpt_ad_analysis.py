import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from dotenv import load_dotenv
import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, load_sheets_config, read_values
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id, quote_sheet_name


DEFAULT_SHEET = "広告分析マスターDB"
SCREENSHOT_HEADERS = ("広告スクショ", "広告スクショURL", "スクショURL", "画像URL", "Screenshot URL", "screenshot_url")


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
    company = clean(row.get("会社名"))
    service = clean(row.get("サービス名"))
    ad_url = clean(row.get("広告ライブラリURL"))
    return f"""今日の広告

会社名：{company}
サービス名：{service}
広告URL：【このURLは見なくていい。俺が使うための】{ad_url}
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


def capture_ad_card(page: Any, ad_url: str, output_path: Path) -> bool:
    if not ad_url:
        return False
    page.goto(ad_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(5000)
    close_buttons = page.locator("div[role='dialog'] button[aria-label*='閉じる'], div[role='dialog'] button[aria-label*='Close']")
    try:
        if close_buttons.count():
            close_buttons.first.click(timeout=3000)
            page.wait_for_timeout(1000)
    except Exception:
        pass
    clip = page.evaluate(
        """
        () => {
          const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
          const targetId = new URL(location.href).searchParams.get('id') || '';
          const nodes = Array.from(document.querySelectorAll('div, article, section'));
          const candidates = [];
          for (const node of nodes) {
            const text = norm(node.innerText);
            if (targetId && !text.includes(targetId)) continue;
            if (!/スポンサー広告|Sponsored/.test(text)) continue;
            if (!/ライブラリID|Library ID/.test(text)) continue;
            const rect = node.getBoundingClientRect();
            if (rect.width < 280 || rect.height < 300 || rect.width > 900 || rect.height > 1800) continue;
            const imgs = Array.from(node.querySelectorAll('img')).filter((img) => {
              const imgRect = img.getBoundingClientRect();
              return imgRect.width >= 120 && imgRect.height >= 80;
            });
            if (!imgs.length) continue;
            candidates.push({
              x: Math.max(0, rect.x - 8),
              y: Math.max(0, rect.y - 8),
              width: Math.min(window.innerWidth - Math.max(0, rect.x - 8), rect.width + 16),
              height: Math.min(document.documentElement.scrollHeight - Math.max(0, rect.y - 8), rect.height + 16),
              area: rect.width * rect.height,
              textLength: text.length,
            });
          }
          candidates.sort((a, b) => (a.textLength - b.textLength) || (a.area - b.area));
          return candidates[0] || null;
        }
        """
    )
    if not clip:
        return False
    page.screenshot(path=str(output_path), clip=clip)
    return output_path.exists() and output_path.stat().st_size > 0


def capture_assets(row: Dict[str, str], output_dir: Path, chrome_executable: str) -> List[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_file in output_dir.glob("lp_fullpage_part_*.png"):
        stale_file.unlink(missing_ok=True)
    for stale_file in ("ad_creative.png", "ad_card.png", "lp_fullpage.png", "lp_fullpage.pdf"):
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
            ad_url = clean(row.get("広告ライブラリURL"))
            if ad_url:
                ad_card_path = output_dir / "ad_card.png"
                try:
                    if capture_ad_card(page, ad_url, ad_card_path):
                        files.append(ad_card_path)
                    else:
                        log("広告カード全体スクショを取得できませんでした。")
                except Exception as error:
                    log(f"広告カード全体スクショの取得をスキップ: {error}")
            lp_url = clean(row.get("LP URL"))
            if lp_url:
                page.goto(lp_url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(5000)
                png_path = output_dir / "lp_fullpage.png"
                page.screenshot(path=str(png_path), full_page=True)
                try:
                    pdf_path = output_dir / "lp_fullpage.pdf"
                    page.pdf(path=str(pdf_path), print_background=True, format="A4")
                    files.append(pdf_path)
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
    set clickResult to execute active tab of front window javascript "(() => { const findButton = () => document.querySelector('button[data-testid=\"send-button\"]') || Array.from(document.querySelectorAll('button')).find((b) => /送信|Send|プロンプトを送信/i.test(b.getAttribute('aria-label') || b.textContent || '')); const started = Date.now(); while (Date.now() - started < 8000) { const button = findButton(); if (button && !button.disabled && button.getAttribute('aria-disabled') !== 'true') { button.click(); return 'clicked'; } } return 'missing'; })()"
    return clickResult
  on error errMsg
    return "error: " & errMsg
  end try
end tell
'''
    result = subprocess.run(["/usr/bin/osascript"], input=script, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return clean(result.stdout) == "clicked"


def mime_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def attach_files_via_file_input(files: List[Path]) -> bool:
    records = [
        {
            "name": path.name,
            "mime": mime_type_for(path),
            "b64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
        for path in files
        if path.exists() and path.stat().st_size > 0
    ]
    if not records:
        return False
    js_code = """
(() => {
  try {
    const records = __RECORDS__;
    const input = Array.from(document.querySelectorAll('input[type=file]')).find((item) => !item.accept || item.accept === '');
    if (!input) return 'no-input';
    const dt = new DataTransfer();
    for (const record of records) {
      const bin = atob(record.b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      dt.items.add(new File([bytes], record.name, { type: record.mime }));
    }
    input.files = dt.files;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return 'assigned-' + Array.from(input.files).map((file) => file.name).join(',');
  } catch (error) {
    return 'error-' + error.name + '-' + error.message;
  }
})()
""".replace("__RECORDS__", json.dumps(records, ensure_ascii=False))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as temp_file:
        temp_file.write(js_code)
        js_path = temp_file.name
    script = f'''
tell application "Google Chrome"
  activate
  delay 2
  set jsCode to read POSIX file "{js_path}"
  return execute active tab of front window javascript jsCode
end tell
'''
    try:
        result = subprocess.run(
            ["/usr/bin/osascript"],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        output = clean(result.stdout)
        if output.startswith("assigned-"):
            log(f"ChatGPT添付: {output.removeprefix('assigned-')}")
            return True
        log(f"ChatGPT添付失敗: {output or clean(result.stderr)}")
        return False
    finally:
        Path(js_path).unlink(missing_ok=True)


def open_chatgpt(prompt: str, files: List[Path], args: argparse.Namespace) -> None:
    if args.system_chrome:
        subprocess.run(["/usr/bin/open", "-a", "Google Chrome", args.chatgpt_url], check=False)
        if prompt:
            subprocess.run(["/usr/bin/pbcopy"], input=prompt, text=True, check=True)
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
            attach_files_via_file_input(files)
        if args.submit:
            if click_chatgpt_send_button():
                log("通常のChromeでChatGPTへPNGスクショを入れて、送信ボタンをクリックしました。")
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
            log("通常のChromeでChatGPTを開き、PNGスクショ添付を試しました。")
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
