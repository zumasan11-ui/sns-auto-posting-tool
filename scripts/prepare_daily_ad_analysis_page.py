import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from notion_api import append_block_children, create_database_page, load_notion_config, retrieve_database
from sheets_api import build_sheets_service, load_sheets_config, read_values, update_values


DEFAULT_SHEET = "今日の広告DB"
DEFAULT_SPREADSHEET_ID = "15mskJs84UE7-CUtwELlCnjw3_DoWpAIYnZUvqiJvrdc"
DEFAULT_AD_COUNT = 2
TITLE_ENV = "NOTION_DAILY_AD_PAGE_TITLE_PROPERTY"
INITIAL_STATUS_ENV = "NOTION_DAILY_AD_PAGE_INITIAL_STATUS"
SCREENSHOT_HEADERS = ("広告スクショ", "広告スクショURL", "スクショURL", "画像URL", "Screenshot URL", "screenshot_url")
PLATFORM_STATUS_PROPERTIES = ("X", "Threads", "Instagram", "Facebook", "LinkedIn", "YouTube")
DATE_PROPERTY = "日付"


def log(message: str) -> None:
    print(f"[daily-ad-page] {message}", flush=True)


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def first_value(row: Dict[str, Any], headers: Iterable[str]) -> str:
    for header in headers:
        value = clean(row.get(header))
        if value:
            return value
    return ""


def row_is_unanalyzed(row: Dict[str, Any]) -> bool:
    status = clean(row.get("ステータス") or row.get("分析状況"))
    return not clean(row.get("広告分析")) and not clean(row.get("ビジネスモデル")) and status not in {"分析済み", "投稿済み", "完了"}


def row_has_ad(row: Dict[str, Any]) -> bool:
    return bool(clean(row.get("広告ライブラリURL")) and clean(row.get("サービス名")))


def load_sheet_rows(sheet_name: str, spreadsheet_id: str) -> tuple[List[str], List[Dict[str, Any]]]:
    config = load_sheets_config()
    service = build_sheets_service(config)
    spreadsheet_id = spreadsheet_id or os.getenv("AD_ANALYSIS_SPREADSHEET_ID", "").strip() or config["spreadsheet_id"]
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:Z")
    if not values:
        return [], []
    headers = [clean(header) for header in values[0]]
    rows: List[Dict[str, Any]] = []
    for row_index, values_row in enumerate(values[1:], start=2):
        row = {header: values_row[index] if index < len(values_row) else "" for index, header in enumerate(headers)}
        row["_sheet_row"] = str(row_index)
        rows.append(row)
    return headers, rows


def select_ads(rows: List[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    selected = []
    seen_urls = set()
    for row in rows:
        url = clean(row.get("広告ライブラリURL"))
        if not row_has_ad(row) or not row_is_unanalyzed(row) or url in seen_urls:
            continue
        seen_urls.add(url)
        selected.append(row)
        if len(selected) >= count:
            break
    return selected


def rich_text(content: str) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": {"content": content[:2000]}}] if content else []


def rich_text_item(content: str, url: str = "") -> Dict[str, Any]:
    text: Dict[str, Any] = {"content": content[:2000]}
    if url:
        text["link"] = {"url": url}
    return {"type": "text", "text": text}


def paragraph(text: str) -> Dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def rich_paragraph(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": items}}


def heading(text: str) -> Dict[str, Any]:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": rich_text(text)}}


def image_block(url: str, caption_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not url or not re.match(r"^https?://", url):
        return None
    return {
        "object": "block",
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": url},
            "caption": caption_items,
        },
    }


def ad_caption_items(company: str, service: str, genre: str, sub_genre: str, period: str, ad_url: str) -> List[Dict[str, Any]]:
    lines = [f"{company} / {service}".strip(" /")]
    if genre or sub_genre:
        lines.append(" / ".join(part for part in (genre, sub_genre) if part))
    if period:
        lines.append(f"掲載期間：{period}")
    items = [rich_text_item("\n".join(line for line in lines if line))]
    if ad_url:
        items.append(rich_text_item("\n広告URL："))
        items.append(rich_text_item(ad_url, ad_url))
    return items


def ad_blocks(ad: Dict[str, Any], ad_number: int) -> List[Dict[str, Any]]:
    company = clean(ad.get("会社名"))
    service = clean(ad.get("サービス名"))
    genre = clean(ad.get("ジャンル"))
    sub_genre = clean(ad.get("サブジャンル"))
    period = clean(ad.get("掲載期間"))
    ad_url = clean(ad.get("広告ライブラリURL"))
    screenshot_url = first_value(ad, SCREENSHOT_HEADERS)
    blocks: List[Dict[str, Any]] = []
    blocks.append(paragraph(f"{ad_number}."))
    image = image_block(screenshot_url, ad_caption_items(company, service, genre, sub_genre, period, ad_url)) if screenshot_url else None
    if image:
        blocks.append(image)
    else:
        blocks.append(paragraph("広告スクショ：未登録（スプシに広告スクショURL列があれば自動貼付できます）"))
    blocks.append(paragraph("訴求の型："))
    blocks.append(paragraph(""))
    blocks.append(paragraph(""))
    blocks.append(paragraph(""))
    return blocks


def chunked(values: List[Dict[str, Any]], size: int = 80) -> List[List[Dict[str, Any]]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def title_property_name(database: Dict[str, Any]) -> str:
    configured = os.getenv(TITLE_ENV, "").strip()
    if configured:
        return configured
    for name, prop in database.get("properties", {}).items():
        if prop.get("type") == "title":
            return name
    raise RuntimeError("Notionデータベースに title プロパティが見つかりません。")


def scheduled_date_payload(database: Dict[str, Any], offset_days: int = 0) -> Dict[str, Any]:
    if database.get("properties", {}).get(DATE_PROPERTY, {}).get("type") != "date":
        return {}
    scheduled = date.today() + timedelta(days=offset_days)
    return {DATE_PROPERTY: {"date": {"start": scheduled.isoformat()}}}


def status_payload(database: Dict[str, Any]) -> Dict[str, Any]:
    status_name = os.getenv("NOTION_STATUS_PROPERTY", "Status").strip()
    status_value = os.getenv(INITIAL_STATUS_ENV, "未着手").strip()
    prop = database.get("properties", {}).get(status_name)
    payload: Dict[str, Any] = {}
    targets = [(status_name, prop)] if prop else [
        (name, database.get("properties", {}).get(name))
        for name in PLATFORM_STATUS_PROPERTIES
        if database.get("properties", {}).get(name, {}).get("type") in ("status", "select")
    ]
    for name, target_prop in targets:
        if not target_prop:
            continue
        prop_type = target_prop.get("type", "status")
        options = (target_prop.get(prop_type, {}) or {}).get("options", [])
        option_names = {clean(option.get("name")) for option in options if isinstance(option, dict)}
        if option_names and status_value not in option_names:
            fallback = "未着手" if "未着手" in option_names else ""
            if not fallback:
                log(f"Notionステータス候補に {status_value} がないため {name} は未設定にします。")
                continue
            log(f"Notionステータス候補に {status_value} がないため {name} は {fallback} にします。")
            status_value_for_prop = fallback
        else:
            status_value_for_prop = status_value
        if prop_type == "select":
            payload[name] = {"select": {"name": status_value_for_prop}}
        elif prop_type == "status":
            payload[name] = {"status": {"name": status_value_for_prop}}
    return payload


def create_daily_page(ads: List[Dict[str, Any]], dry_run: bool = False) -> Dict[str, Any]:
    config = load_notion_config()
    database = retrieve_database(config)
    created_label = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"{created_label} 広告分析"
    properties = {
        title_property_name(database): {"title": [{"text": {"content": title}}]},
        **status_payload(database),
        **scheduled_date_payload(database),
    }
    children: List[Dict[str, Any]] = []
    for index, ad in enumerate(ads, start=1):
        children.extend(ad_blocks(ad, index))
        if index < len(ads):
            children.append(paragraph(""))
            children.append(paragraph(""))
    if dry_run:
        return {"dry_run": True, "title": title, "ads": len(ads), "properties": properties, "children": children}
    page = create_database_page(config, properties=properties)
    for chunk in chunked(children):
        append_block_children(config, page["id"], chunk)
    return {"id": page.get("id"), "url": page.get("url"), "title": title, "ads": len(ads)}


def mark_ads_inserted_to_notion(sheet_name: str, spreadsheet_id: str, headers: List[str], ads: List[Dict[str, Any]]) -> None:
    if "ステータス" not in headers and "分析状況" not in headers:
        return
    config = load_sheets_config()
    service = build_sheets_service(config)
    status_header = "ステータス" if "ステータス" in headers else "分析状況"
    column = chr(ord("A") + headers.index(status_header))
    for ad in ads:
        row_number = clean(ad.get("_sheet_row"))
        if row_number.isdigit():
            update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!{column}{row_number}:{column}{row_number}", [["Notion投入済み"]])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="今日の広告DBの未分析広告から、Notion分析ページを作成します。")
    parser.add_argument("--sheet-name", default=os.getenv("TODAY_AD_DB_SHEET", DEFAULT_SHEET))
    parser.add_argument("--spreadsheet-id", default=os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID))
    parser.add_argument("--count", type=int, default=int(os.getenv("DAILY_AD_ANALYSIS_COUNT", DEFAULT_AD_COUNT)))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    headers, rows = load_sheet_rows(args.sheet_name, args.spreadsheet_id)
    ads = select_ads(rows, args.count)
    log(f"未分析広告候補: {len(ads)}件 / 要求: {args.count}件")
    if not ads:
        log("Notionページは作成しません。")
        return 0
    result = create_daily_page(ads, dry_run=args.dry_run)
    if not args.dry_run:
        mark_ads_inserted_to_notion(args.sheet_name, args.spreadsheet_id, headers, ads)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"エラー: {error}", file=sys.stderr)
        raise SystemExit(1)
