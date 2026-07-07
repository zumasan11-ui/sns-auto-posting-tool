from __future__ import annotations

import argparse
import re
import sys
from copy import deepcopy
from datetime import datetime
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
    retrieve_block_children,
    retrieve_database,
    update_page,
)
from scripts.prepare_daily_ad_analysis_page import (
    ad_caption_items,
    chunked,
    image_block,
    paragraph,
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
    blocks.append(paragraph("訴求の型："))
    blocks.append(paragraph(""))
    blocks.append(paragraph(""))
    blocks.append(paragraph(""))
    return blocks


def create_split_page(config: Dict[str, str], database: Dict[str, Any], ads: List[Dict[str, str]], title: str) -> Dict[str, Any]:
    properties = {
        title_property_name(database): {"title": [{"text": {"content": title}}]},
        **status_payload(database),
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
    parser = argparse.ArgumentParser(description="旧8広告Notionページを2広告ずつの新規ページへ分割します。")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--chunk-size", type=int, default=2)
    parser.add_argument("--archive-original", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(dotenv_path=".env", override=True)
    config = load_notion_config()
    database = retrieve_database(config)
    blocks = retrieve_block_children(config, args.page_id)
    ads = extract_ads_from_legacy_page(blocks)
    if not ads:
        raise RuntimeError("分割できる広告画像ブロックが見つかりません。")

    created: List[Dict[str, Any]] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for index, start in enumerate(range(0, len(ads), args.chunk_size), start=1):
        chunk = ads[start : start + args.chunk_size]
        title = f"{now} 広告分析 {index}/{(len(ads) + args.chunk_size - 1) // args.chunk_size}"
        if args.dry_run:
            created.append({"dry_run": True, "title": title, "ads": len(chunk)})
        else:
            created.append(create_split_page(config, database, chunk, title))

    archived = False
    if args.archive_original and not args.dry_run:
        update_page(config, args.page_id, archived=True)
        archived = True

    print({"source_ads": len(ads), "created": created, "archived_original": archived})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
