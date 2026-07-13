import argparse
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

from sheets_api import build_sheets_service, load_sheets_config, read_values
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, parse_spreadsheet_id, quote_sheet_name


DEFAULT_SHEET = "広告分析マスターDB"


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


def safe_name(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥_-]+", "_", clean(value)).strip("_")
    return name[:80] or "ad"


def build_prompt(row: Dict[str, str], files: List[Path]) -> str:
    service = clean(row.get("サービス名"))
    company = clean(row.get("会社名"))
    copy = clean(row.get("コピー"))
    genre = clean(row.get("ジャンル"))
    sub_genre = clean(row.get("サブジャンル"))
    period = clean(row.get("掲載期間"))
    ad_url = clean(row.get("広告ライブラリURL"))
    lp_url = clean(row.get("LP URL"))
    file_lines = "\n".join(f"- {path.name}" for path in files)
    return f"""この広告とLPを見て、SNS投稿用に深く広告分析して。

前提情報：
- 会社名：{company}
- サービス名：{service}
- ジャンル：{genre}
- サブジャンル：{sub_genre}
- 掲載期間：{period}
- 広告ライブラリURL：{ad_url}
- LP URL：{lp_url}
- 画像内コピー：{copy or "未取得"}

添付ファイル：
{file_lines}

まず以下の構成で出して。

【広告分析vol.】

{service}


◼️ターゲット予想



◼️なぜこの広告は勝っているのか



◼️学んだこと


その後、必要なら追加で質問するから、このチャットで深掘りできるようにして。
"""


def capture_assets(row: Dict[str, str], output_dir: Path, chrome_executable: str) -> List[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright が未インストールです。") from error

    output_dir.mkdir(parents=True, exist_ok=True)
    files: List[Path] = []
    targets = [
        ("ad_library", clean(row.get("広告ライブラリURL"))),
        ("lp", clean(row.get("LP URL"))),
    ]
    launch_options: Dict[str, Any] = {"headless": True}
    if chrome_executable:
        launch_options["executable_path"] = chrome_executable
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1800}, locale="ja-JP")
        try:
            for label, url in targets:
                if not url:
                    continue
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(5000)
                    png_path = output_dir / f"{label}_fullpage.png"
                    page.screenshot(path=str(png_path), full_page=True)
                    files.append(png_path)
                    if label == "lp":
                        pdf_path = output_dir / f"{label}_fullpage.pdf"
                        page.pdf(path=str(pdf_path), print_background=True, format="A4")
                        files.append(pdf_path)
                except Exception as error:
                    log(f"{label} の取得をスキップ: {error}")
        finally:
            browser.close()
    return files


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
                png_path = str(png_file).replace('"', '\\"')
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
            log("通常のChromeでChatGPTへプロンプトとPNGスクショを入れて送信しました。")
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
    parser.add_argument("--row", type=int, required=True)
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
    row = load_sheet_row(spreadsheet_id, args.sheet_name, args.row)
    output_dir = Path(args.output_dir) / f"row_{args.row}_{safe_name(row.get('サービス名', ''))}"
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
