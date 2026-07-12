from __future__ import annotations

import argparse
import re
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from notion_api import (
    append_block_children,
    create_database_page,
    load_notion_config,
    query_database,
    retrieve_block_children,
    retrieve_database,
    update_page,
)
from scripts.prepare_daily_ad_analysis_page import (
    ad_caption_items,
    ad_metadata_body_blocks,
    chunked,
    image_block,
    paragraph,
    scheduled_date_payload,
    next_available_scheduled_date,
    status_payload,
    title_property_name,
)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def plain_text(items: List[Dict[str, Any]]) -> str:
    return "".join(str(item.get("plain_text", "")) for item in items or [])


def image_url(block: Dict[str, Any]) -> str:
    data = block.get("image", {})
    if data.get("type") == "external":
        return clean((data.get("external") or {}).get("url"))
    if data.get("type") == "file":
        return clean((data.get("file") or {}).get("url"))
    return ""


def parse_caption(caption: str) -> Dict[str, str]:
    lines = [line.strip() for line in str(caption or "").splitlines() if line.strip()]
    company = service = genre = sub_genre = period = ad_url = ""
    if lines:
        if " / " in lines[0]:
            company, service = [part.strip() for part in lines[0].split(" / ", 1)]
        else:
            service = lines[0].strip()
    if len(lines) >= 2 and " / " in lines[1]:
        genre, sub_genre = [part.strip() for part in lines[1].split(" / ", 1)]
    for line in lines:
        if line.startswith("掲載期間"):
            period = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        if "facebook.com/ads/library" in line:
            match = re.search(r"https?://\S+", line)
            ad_url = match.group(0) if match else line.split("広告URL", 1)[-1].strip(" ：:")
    return {
        "会社名": company,
        "サービス名": service,
        "ジャンル": genre,
        "サブジャンル": sub_genre,
        "掲載期間": period,
        "広告ライブラリURL": ad_url,
    }


def extract_ads_from_legacy_page(blocks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    ads: List[Dict[str, str]] = []
    for block in blocks:
        if block.get("type") != "image":
            continue
        data = block.get("image", {})
        url = image_url(block)
        caption = plain_text(data.get("caption") or [])
        if not url:
            continue
        ad = parse_caption(caption)
        ad["広告スクショ"] = url
        ads.append(ad)
    return ads


def page_status_values(page: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for prop in page.get("properties", {}).values():
        prop_type = prop.get("type")
        if prop_type in ("status", "select"):
            value = (prop.get(prop_type) or {}).get("name", "")
            if value:
                values.append(value)
    return values


def page_is_completed(page: Dict[str, Any]) -> bool:
    statuses = page_status_values(page)
    return bool(statuses) and all(status == "完了" for status in statuses)


def ad_blocks_from_extracted(ad: Dict[str, str], number: int) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = [paragraph(f"{number}.")]
    image = image_block(
        ad.get("広告スクショ", ""),
        ad_caption_items(
            ad.get("会社名", ""),
            ad.get("サービス名", ""),
            ad.get("ジャンル", ""),
            ad.get("サブジャンル", ""),
            ad.get("掲載期間", ""),
            ad.get("広告ライブラリURL", ""),
        ),
    )
    if image:
        blocks.append(image)
    blocks.extend(
        ad_metadata_body_blocks(
            ad.get("会社名", ""),
            ad.get("サービス名", ""),
            ad.get("掲載期間", ""),
        )
    )
    blocks.append(paragraph(""))
    blocks.append(paragraph(""))
    blocks.append(paragraph(""))
    return blocks


def create_split_page(
    config: Dict[str, str],
    database: Dict[str, Any],
    ads: List[Dict[str, str]],
    title: str,
    scheduled_start,
    offset_days: int,
) -> Dict[str, Any]:
    properties = {
        title_property_name(database): {"title": [{"text": {"content": title}}]},
        **status_payload(database),
        **scheduled_date_payload(database, offset_days, scheduled_start),
    }
    page = create_database_page(config, properties=properties)
    children: List[Dict[str, Any]] = []
    for index, ad in enumerate(ads, start=1):
        children.extend(ad_blocks_from_extracted(ad, index))
        if index < len(ads):
            children.append(paragraph(""))
            children.append(paragraph(""))
    for block_chunk in chunked(children):
        append_block_children(config, page["id"], block_chunk)
    return {"id": page.get("id"), "url": page.get("url"), "title": title, "ads": len(ads)}


def main() -> int:
    parser = argparse.ArgumentParser(description="複数広告入りNotionページを指定件数ずつの新規ページへ分割します。")
    parser.add_argument("--page-id")
    parser.add_argument("--all", action="store_true", help="データベース内の複数広告ページをまとめて分割します。")
    parser.add_argument("--chunk-size", type=int, default=1)
    parser.add_argument("--include-completed", action="store_true", help="完了済みページも分割対象に含めます。")
    parser.add_argument("--archive-original", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.page_id and not args.all:
        raise RuntimeError("--page-id または --all を指定してください。")

    load_dotenv(dotenv_path=".env", override=True)
    config = load_notion_config()
    database = retrieve_database(config)
    scheduled_start = next_available_scheduled_date(config, database)
    pages = [{"id": args.page_id, "properties": {}}] if args.page_id else query_database(config, page_size=100, fetch_all=True)
    results: List[Dict[str, Any]] = []
    now = datetime.now()
    page_sequence = 0
    for page in pages:
        page_id = page["id"]
        if page_is_completed(page) and not args.include_completed:
            results.append({"page_id": page_id, "created": [], "archived_original": False, "skipped": True, "reason": "completed"})
            continue
        blocks = retrieve_block_children(config, page_id)
        ads = extract_ads_from_legacy_page(blocks)
        if len(ads) <= args.chunk_size:
            results.append({"page_id": page_id, "source_ads": len(ads), "created": [], "archived_original": False, "skipped": True})
            continue

        created: List[Dict[str, Any]] = []
        total_chunks = (len(ads) + args.chunk_size - 1) // args.chunk_size
        for index, start in enumerate(range(0, len(ads), args.chunk_size), start=1):
            page_sequence += 1
            chunk = ads[start : start + args.chunk_size]
            title_time = (now + timedelta(minutes=page_sequence - 1)).strftime("%Y-%m-%d %H:%M")
            title = clean(chunk[0].get("サービス名")) or f"{title_time} 広告分析 {index}/{total_chunks}"
            if args.dry_run:
                created.append({"dry_run": True, "title": title, "ads": len(chunk)})
            else:
                created.append(create_split_page(config, database, chunk, title, scheduled_start, page_sequence - 1))

        archived = False
        if args.archive_original and not args.dry_run:
            update_page(config, page_id, archived=True)
            archived = True
        results.append({"page_id": page_id, "source_ads": len(ads), "created": created, "archived_original": archived})

    print({"pages": results})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
