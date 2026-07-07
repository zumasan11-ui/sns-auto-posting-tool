import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from PIL import Image

from carousel_generator import (
    CAROUSEL_COVER_TITLE_TEMPLATE,
    CAROUSEL_MAX_ADS,
    render_carousel_cover_slide,
    render_carousel_ending_slide,
    render_slide,
    render_text_slide,
    save_pdf,
)
from carousel_poster import post_instagram_carousel, post_linkedin_pdf
from cleanup_generated_assets import cleanup_generated_assets
from facebook_manual_export import export_facebook_manual_video
from main import (
    build_client,
    create_facebook_photo_post,
    create_facebook_text_post,
    create_post,
    load_credentials,
    load_facebook_credentials,
    load_threads_credentials,
    request_threads_api,
    validate_post_text,
)
from notion_api import (
    load_notion_config,
    query_database,
    request_notion,
    retrieve_database,
    retrieve_block_children,
    update_page,
)
from reels_generator import (
    REEL_BODY_FONT_STYLE,
    REEL_COVER_DURATION,
    REEL_COVER_FONT_STYLE,
    REEL_COVER_TITLE_TEMPLATE,
    REEL_STRUCTURED_PAGE_DURATION,
    REEL_STRUCTURED_TRANSITION,
    ReelPage,
    ReelSpec,
    build_structured_reel_pages,
    post_instagram_reel,
    save_reel_thumbnail,
    write_structured_mp4,
)
from sheets_api import append_values, build_sheets_service, get_spreadsheet, load_sheets_config
from sheets_api import read_values, update_values
from scripts.sheet_formatting import freeze_row_height
from scripts.prototype_single_ad_post_assets import (
    render_dynamic_cover,
    render_segment_text_slide,
    render_today_ad_slide,
    split_near_periods,
)
from token_refresh import ensure_token_fresh
from youtube_community_export import export_youtube_community_images
from youtube_poster import upload_youtube_short
from tiktok_poster import upload_tiktok_video


JST_TZ = "Asia/Tokyo"
STATUS_PROPERTY = os.getenv("NOTION_STATUS_PROPERTY") or "Status"
ERROR_PROPERTY = os.getenv("NOTION_ERROR_PROPERTY") or "エラー内容"
STATE_DIR = Path(os.getenv("AUTO_POST_STATE_DIR", "public_state"))
PUBLIC_ROOT = STATE_DIR / "public"
STATE_FILE = STATE_DIR / "state" / "current.json"
RUNTIME_DIR = Path("deliverables/auto_post")
TEXT_SLOTS = ("12:00", "18:00")
MEDIA_SLOTS = ("16:00", "20:30")
SLOTS = ("12:00", "16:00", "18:00", "20:30")
TWO_SLOT_TIMES = TEXT_SLOTS
DAILY_MAX_POST_SECTIONS = 2
TEXT_PLATFORMS = ("x", "threads", "facebook")
CAROUSEL_CAPTION = "広告分析"
THREADS_MAX_TEXT_LENGTH = 500
CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
PLATFORM_STATUS_PROPERTIES = ("X", "Threads", "Instagram", "Facebook", "LinkedIn", "YouTube", "TikTok")
TIKTOK_ENABLED = (
    os.getenv("TIKTOK_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
    and bool(os.getenv("TIKTOK_REFRESH_TOKEN") or os.getenv("TIKTOK_ACCESS_TOKEN"))
)
DEFAULT_AD_ANALYSIS_SPREADSHEET_ID = "15mskJs84UE7-CUtwELlCnjw3_DoWpAIYnZUvqiJvrdc"
DEFAULT_AD_ANALYSIS_MASTER_SHEET = "広告分析マスターDB"
DEFAULT_TODAY_AD_DB_SHEET = "今日の広告DB"
NOTION_DATE_PROPERTY = "日付"
NOTION_READY_PROPERTY = os.getenv("NOTION_READY_PROPERTY") or "選択"
NOTION_READY_VALUE = os.getenv("NOTION_READY_VALUE") or "済み"


@dataclass
class AdSection:
    number: int
    text: str
    images: List[Path] = field(default_factory=list)
    companies: List[str] = field(default_factory=list)
    source_caption: str = ""
    period_text: str = ""
    company_name: str = ""
    service_name: str = ""
    ad_library_url: str = ""
    lp_url: str = ""
    sheet_row: Optional[int] = None
    appeal_type: str = ""


def now_jst() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(JST_TZ))


def iso_date_jst(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        from zoneinfo import ZoneInfo

        return parsed.astimezone(ZoneInfo(JST_TZ)).strftime("%Y-%m-%d")
    except ValueError:
        return text[:10]


def state_created_today(state: Dict[str, Any]) -> bool:
    state_date = str(state.get("planned_date") or "").strip() or iso_date_jst(state.get("created_at") or state.get("completed_at"))
    return state_date == now_jst().strftime("%Y-%m-%d")


def load_environment() -> None:
    load_dotenv(dotenv_path=".env", override=True)


def plain_text(items: Sequence[Dict[str, Any]]) -> str:
    return "".join(str(item.get("plain_text", "")) for item in items)


def property_plain_value(prop: Dict[str, Any]) -> Any:
    prop_type = prop.get("type")
    data = prop.get(prop_type)
    if prop_type in ("title", "rich_text"):
        return plain_text(data or [])
    if prop_type in ("select", "status"):
        return (data or {}).get("name", "")
    if prop_type == "multi_select":
        return [item.get("name", "") for item in data or []]
    if prop_type == "date":
        return data or {}
    if prop_type == "files":
        return data or []
    if prop_type in ("url", "number", "checkbox", "created_time", "last_edited_time"):
        return data
    return data


def get_page_title(page: Dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            title = property_plain_value(prop)
            if title:
                return str(title)
    return "広告分析"


def page_date_sort_value(page: Dict[str, Any]) -> str:
    prop = page.get("properties", {}).get(NOTION_DATE_PROPERTY, {})
    value = property_plain_value(prop) if prop else None
    if isinstance(value, dict):
        return str(value.get("start") or value.get("end") or "")
    return ""


def page_ready_value(page: Dict[str, Any]) -> str:
    prop = page.get("properties", {}).get(NOTION_READY_PROPERTY, {})
    value = property_plain_value(prop) if prop else ""
    if isinstance(value, str):
        return value
    return ""


def page_is_approved_for_posting(page: Dict[str, Any]) -> bool:
    return page_ready_value(page) == NOTION_READY_VALUE


def database_properties(config: Dict[str, str]) -> Dict[str, Any]:
    return retrieve_database(config).get("properties", {})


def resolve_single_status_property(properties: Dict[str, Any]) -> Optional[tuple[str, str]]:
    configured = STATUS_PROPERTY.strip()
    if configured and configured in properties:
        prop_type = properties[configured].get("type", "status")
        return configured, prop_type
    if configured and configured not in ("Status", "ステータス"):
        raise RuntimeError(f"Notionデータベースにステータスプロパティ {configured} が見つかりません。")
    return None


def platform_status_properties(properties: Dict[str, Any]) -> List[str]:
    return [
        name
        for name in PLATFORM_STATUS_PROPERTIES
        if properties.get(name, {}).get("type") == "status"
    ]


def resolve_status_targets(properties: Dict[str, Any]) -> List[tuple[str, str]]:
    single = resolve_single_status_property(properties)
    if single:
        return [single]
    platform_props = platform_status_properties(properties)
    if platform_props:
        return [(name, "status") for name in platform_props]
    for name, prop in properties.items():
        if prop.get("type") == "status":
            return [(name, "status")]
    for name, prop in properties.items():
        if prop.get("type") == "select" and name.lower() in ("status", "ステータス", "状態"):
            return [(name, "select")]
    raise RuntimeError("Notionデータベースに status 型またはステータス用 select プロパティが見つかりません。")


def property_option_names(prop: Dict[str, Any], prop_type: str) -> List[str]:
    options = (prop.get(prop_type) or {}).get("options", [])
    return [str(option.get("name", "")) for option in options]


def status_value_for(properties: Dict[str, Any], name: str, prop_type: str, status: str) -> str:
    options = property_option_names(properties.get(name, {}), prop_type)
    if status in options:
        return status
    if status == "エラー":
        for fallback in ("エラー", "未投稿", "未着手", "To-do"):
            if fallback in options:
                return fallback
    if status == "未投稿":
        for fallback in ("未投稿", "未着手", "To-do"):
            if fallback in options:
                return fallback
    raise RuntimeError(f"Notionプロパティ {name} にステータス選択肢 {status} がありません。")


def status_property_payload(name: str, prop_type: str, status: str) -> Dict[str, Any]:
    if prop_type == "select":
        return {name: {"select": {"name": status}}}
    return {name: {"status": {"name": status}}}


def error_property_payload(message: str) -> Dict[str, Any]:
    return {ERROR_PROPERTY: {"rich_text": [{"text": {"content": message[:1900]}}]}}


def update_notion_status(page_id: str, status: str, error: str = "") -> None:
    config = load_notion_config()
    db_props = database_properties(config)
    properties: Dict[str, Any] = {}
    for status_name, status_type in resolve_status_targets(db_props):
        value = status_value_for(db_props, status_name, status_type, status)
        properties.update(status_property_payload(status_name, status_type, value))
    if error and ERROR_PROPERTY in db_props:
        properties.update(error_property_payload(error))
    update_page(config, page_id, properties=properties)


def platform_property_for_task(task: Dict[str, Any]) -> Optional[str]:
    platform = task.get("platform")
    if platform == "x":
        return "X"
    if platform == "threads":
        return "Threads"
    if platform == "instagram":
        return "Instagram"
    if platform == "facebook":
        return "Facebook"
    if platform == "linkedin":
        return "LinkedIn"
    if platform == "youtube":
        return "YouTube"
    if platform == "tiktok":
        return "TikTok"
    return None


def update_notion_platform_status(page_id: str, platform_property: str, status: str) -> None:
    config = load_notion_config()
    db_props = database_properties(config)
    prop = db_props.get(platform_property)
    if not prop:
        return
    prop_type = prop.get("type", "status")
    value = status_value_for(db_props, platform_property, prop_type, status)
    update_page(config, page_id, properties=status_property_payload(platform_property, prop_type, value))


def update_task_platform_status(state: Dict[str, Any], task: Dict[str, Any], status: str) -> None:
    page_id = state.get("page_id")
    platform_property = platform_property_for_task(task)
    if not page_id or not platform_property:
        return
    update_notion_platform_status(page_id, platform_property, status)


def planned_page_still_approved(state: Dict[str, Any]) -> bool:
    page_id = state.get("page_id")
    if not page_id:
        return False
    try:
        config = load_notion_config()
        page = request_notion("GET", f"/pages/{str(page_id).replace('-', '')}", config)
        return page_is_approved_for_posting(page)
    except Exception as error:
        print(f"Notion投稿許可の再確認に失敗したため投稿を止めます: {error}", file=sys.stderr)
        return False


def refresh_completed_platform_statuses(state: Dict[str, Any]) -> None:
    page_id = state.get("page_id")
    if not page_id:
        return
    for platform_property in PLATFORM_STATUS_PROPERTIES:
        platform_tasks = [
            task
            for task in state.get("tasks", [])
            if platform_property_for_task(task) == platform_property
        ]
        if platform_tasks and all(task.get("status") == "posted" for task in platform_tasks):
            update_notion_platform_status(page_id, platform_property, "完了")


def oldest_page_by_status(statuses: Sequence[str]) -> Optional[Dict[str, Any]]:
    pages = pages_by_status(statuses, page_size=1)
    return pages[0] if pages else None


def pages_by_status(statuses: Sequence[str], page_size: int = 20) -> List[Dict[str, Any]]:
    config = load_notion_config()
    db_props = database_properties(config)
    ready_prop = db_props.get(NOTION_READY_PROPERTY)
    if not ready_prop or ready_prop.get("type") not in {"select", "status"}:
        print(
            f"Notion投稿許可プロパティ {NOTION_READY_PROPERTY} が見つからないため、投稿対象を取得しません。",
            file=sys.stderr,
        )
        return []
    ready_filter_type = "select" if ready_prop.get("type") == "select" else "status"
    ready_filter = {"property": NOTION_READY_PROPERTY, ready_filter_type: {"equals": NOTION_READY_VALUE}}
    status_targets = resolve_status_targets(db_props)
    matched_pages: List[Dict[str, Any]] = []
    seen_page_ids: set[str] = set()
    for status in statuses:
        filters = []
        for status_name, status_type in status_targets:
            status_filter_type = "select" if status_type == "select" else "status"
            try:
                value = status_value_for(db_props, status_name, status_type, status)
            except RuntimeError:
                continue
            filters.append({"property": status_name, status_filter_type: {"equals": value}})
        if not filters:
            continue
        status_filter = filters[0] if len(filters) == 1 else {"or": filters}
        filter_data = {"and": [status_filter, ready_filter]}
        sorts = (
            [{"property": NOTION_DATE_PROPERTY, "direction": "ascending"}]
            if db_props.get(NOTION_DATE_PROPERTY, {}).get("type") == "date"
            else [{"timestamp": "created_time", "direction": "ascending"}]
        )
        pages = query_database(
            config,
            page_size=page_size,
            filter_data=filter_data,
            sorts=sorts,
        )
        for page in pages:
            page_id = str(page.get("id") or "")
            if not page_id or page_id in seen_page_ids:
                continue
            seen_page_ids.add(page_id)
            matched_pages.append(page)
    return sorted(
        matched_pages,
        key=lambda page: (
            page_date_sort_value(page) or "9999-12-31",
            str(page.get("created_time") or ""),
            get_page_title(page),
        ),
    )


def ensure_state_branch() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if (STATE_DIR / ".git").exists():
        return
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repo:
        return
    url = f"https://x-access-token:{os.getenv('GITHUB_TOKEN', '')}@github.com/{repo}.git"
    subprocess.run(["git", "init", str(STATE_DIR)], check=True)
    subprocess.run(["git", "-C", str(STATE_DIR), "remote", "add", "origin", url], check=True)
    fetch = subprocess.run(
        ["git", "-C", str(STATE_DIR), "fetch", "origin", "gh-pages"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if fetch.returncode == 0:
        subprocess.run(["git", "-C", str(STATE_DIR), "checkout", "-B", "gh-pages", "origin/gh-pages"], check=True)
    else:
        subprocess.run(["git", "-C", str(STATE_DIR), "checkout", "-b", "gh-pages"], check=True)


def save_state(state: Dict[str, Any], push: bool = True) -> None:
    ensure_state_branch()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if push and (STATE_DIR / ".git").exists():
        subprocess.run(["git", "-C", str(STATE_DIR), "config", "user.name", "github-actions"], check=True)
        subprocess.run(["git", "-C", str(STATE_DIR), "config", "user.email", "actions@github.com"], check=True)
        subprocess.run(["git", "-C", str(STATE_DIR), "add", "."], check=True)
        diff = subprocess.run(["git", "-C", str(STATE_DIR), "diff", "--cached", "--quiet"], check=False)
        if diff.returncode != 0:
            subprocess.run(["git", "-C", str(STATE_DIR), "commit", "-m", "Update auto post state"], check=True)
            subprocess.run(["git", "-C", str(STATE_DIR), "push", "-u", "origin", "gh-pages"], check=True)


def load_state() -> Optional[Dict[str, Any]]:
    ensure_state_branch()
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return None


def public_base_url() -> str:
    configured = os.getenv("PUBLIC_ASSET_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        raise RuntimeError("PUBLIC_ASSET_BASE_URL または GITHUB_REPOSITORY が必要です。")
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}"


def public_url(relative_path: Path) -> str:
    return f"{public_base_url()}/{relative_path.as_posix()}"


def block_to_text(block: Dict[str, Any]) -> str:
    block_type = block.get("type")
    data = block.get(block_type, {})
    if block_type in (
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "callout",
    ):
        return plain_text(data.get("rich_text", []))
    if block_type == "to_do":
        return plain_text(data.get("rich_text", []))
    if block_type == "table_row":
        cells = [plain_text(cell) for cell in data.get("cells", [])]
        return " / ".join(cell for cell in cells if cell)
    return ""


def block_caption_text(block: Dict[str, Any]) -> str:
    block_type = block.get("type")
    if block_type not in ("image", "file", "pdf", "video"):
        return ""
    data = block.get(block_type, {})
    return plain_text(data.get("caption", []))


def block_file_url(block: Dict[str, Any]) -> Optional[str]:
    block_type = block.get("type")
    data = block.get(block_type, {})
    if block_type not in ("image", "file", "pdf", "video"):
        return None
    file_type = data.get("type")
    if file_type == "external":
        return (data.get("external") or {}).get("url")
    if file_type == "file":
        return (data.get("file") or {}).get("url")
    return None


def download_file(url: str, output_dir: Path, index: int) -> Path:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        suffix = ".png"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"notion_image_{index:02d}{suffix}"
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    path.write_bytes(response.content)
    if suffix == ".webp":
        converted = path.with_suffix(".png")
        Image.open(path).convert("RGB").save(converted)
        return converted
    return path


def walk_blocks(config: Dict[str, str], block_id: str) -> Iterable[Dict[str, Any]]:
    for block in retrieve_block_children(config, block_id):
        yield block
        if block.get("has_children"):
            yield from walk_blocks(config, block.get("id", ""))


def extract_sections(page: Dict[str, Any], work_dir: Path) -> List[AdSection]:
    config = load_notion_config()
    blocks = list(walk_blocks(config, page["id"]))
    images: List[Path] = []
    image_index = 1
    sections: List[AdSection] = []
    current: Optional[AdSection] = None

    for prop in page.get("properties", {}).values():
        if prop.get("type") == "files":
            for item in prop.get("files", []):
                file_url = (item.get(item.get("type", ""), {}) or {}).get("url")
                if file_url:
                    images.append(download_file(file_url, work_dir / "source", image_index))
                    image_index += 1

    for block in blocks:
        file_url = block_file_url(block)
        if file_url:
            image_path = download_file(file_url, work_dir / "source", image_index)
            image_index += 1
            caption = block_caption_text(block).strip()
            if current:
                current.images.append(image_path)
                if caption:
                    current.source_caption = caption
            else:
                images.append(image_path)
            continue

        text = block_to_text(block).strip()
        if not text:
            continue
        match = re.match(rf"^\s*([{CIRCLED_DIGITS}]|[0-9０-９]+[.)．、])\s*(.*)$", text)
        if match:
            number_token = match.group(1)[0]
            number = CIRCLED_DIGITS.find(number_token) + 1 if number_token in CIRCLED_DIGITS else len(sections) + 1
            current = AdSection(number=number, text=match.group(2).strip())
            sections.append(current)
            continue
        if current:
            current.text = (current.text + "\n" + text).strip()

    if not sections:
        body_text = "\n".join(block_to_text(block).strip() for block in blocks if block_to_text(block).strip())
        sections.append(AdSection(number=1, text=body_text or get_page_title(page)))

    section_images = [path for section in sections for path in section.images]
    fallback_images = section_images or images
    last_image: Optional[Path] = fallback_images[0] if fallback_images else None
    for section in sections:
        if section.images:
            last_image = section.images[0]
        elif last_image and section.number % 2 == 1:
            section.images.append(last_image)
        metadata = extract_section_metadata(section.source_caption + "\n" + section.text)
        caption_company, caption_service = split_source_caption(section.source_caption)
        section.company_name = caption_company or metadata.get("会社名", "")
        section.service_name = caption_service or metadata.get("サービス名", "")
        section.ad_library_url = metadata.get("広告ライブラリURL", "")
        section.lp_url = metadata.get("LP URL", "")
        section.sheet_row = int(metadata["スプレッドシート行"]) if metadata.get("スプレッドシート行", "").isdigit() else None
        source_label = source_label_for(section.company_name, section.service_name)
        section.companies = [source_label] if source_label else extract_companies(section.text)
        section.period_text = extract_period_text(section.source_caption + "\n" + section.text)
        section.appeal_type = extract_appeal_type(section.text)
    return sections


def split_source_caption(caption: str) -> tuple[str, str]:
    first_line = next((line.strip() for line in str(caption or "").splitlines() if line.strip()), "")
    value = re.sub(r"^\s*[0-9０-９]+[.)．、]?\s*", "", first_line).strip()
    if not value:
        return "", ""
    parts = [part.strip() for part in re.split(r"\s*/\s*|\s*／\s*", value, maxsplit=1)]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return value, ""


def source_label_for(company: str, service: str) -> str:
    company = company.strip()
    service = service.strip()
    if company and service:
        return f"{company} / {service}"
    return company or service


def extract_section_metadata(text: str) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([^:：\n]{2,30})\s*[:：]\s*(.*)$", line.strip())
        if not match:
            continue
        key = match.group(1).strip()
        value = match.group(2).strip()
        if key in ("ジャンル", "サブジャンル", "会社名", "サービス名", "掲載期間", "広告ライブラリURL", "広告URL", "LP URL", "スプレッドシート行"):
            if key == "広告URL":
                key = "広告ライブラリURL"
            metadata[key] = value
    return metadata


def extract_companies(text: str) -> List[str]:
    metadata = extract_section_metadata(text)
    company = metadata.get("会社名", "").strip()
    service = metadata.get("サービス名", "").strip()
    if company and service:
        return [f"{company} / {service}"]
    if company:
        return [company]
    if service:
        return [service]
    patterns = [
        r"引用元[:：]\s*([^\n]+)",
        r"会社名[:：]\s*([^\n]+)",
        r"企業名[:：]\s*([^\n]+)",
        r"サービス名[:：]\s*([^\n]+)",
    ]
    companies: List[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = re.split(r"[、,/／]", match)[0].strip()
            if value and value not in companies:
                companies.append(value)
    return companies


def extract_period_text(text: str) -> str:
    match = re.search(r"掲載期間[:：]?\s*([^\n]+)", text)
    if not match:
        return "◯ヶ月"
    value = match.group(1)
    year_match = re.search(r"([0-9０-９]+)\s*年", value)
    month_match = re.search(r"([0-9０-９]+)\s*ヶ?月", value)
    if year_match:
        return f"{year_match.group(1)}年"
    if month_match:
        return f"{month_match.group(1)}ヶ月"
    return "◯ヶ月"


def extract_appeal_type(text: str) -> str:
    for line in str(text or "").splitlines():
        match = re.match(r"^\s*訴求の型\s*[:：]\s*(.*)$", line.strip())
        if match:
            return match.group(1).strip()
    return ""


def extract_post_section(text: str, heading: str) -> str:
    match = re.search(rf"【{re.escape(heading)}】\s*(.*?)(?=\n【|$)", text, re.S)
    return match.group(1).strip() if match else ""


def business_model_text(text: str) -> str:
    return extract_post_section(text, "ビジネスモデル")


def winning_reason_text(text: str) -> str:
    return extract_post_section(text, "なぜこの広告が勝ってるか")


def learning_text(text: str) -> str:
    return extract_post_section(text, "広告の学び")


def section_body_text(section: AdSection) -> str:
    text = strip_numbering(section.text)
    return strip_metadata_lines(text)


def strip_metadata_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*(ジャンル|サブジャンル|会社名|サービス名|掲載期間|広告ライブラリURL|LP URL|スプレッドシート行|広告スクショ|訴求の型)\s*[:：]", line):
            continue
        if re.match(r"^\s*(広告分析|ビジネスモデル分析)\s*[:：]\s*$", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


PLACEHOLDER_POST_TEXTS = {
    "",
    "広告分析",
    "ビジネスモデル分析",
    "ビジネスモデル",
    "訴求の型",
}


def is_meaningful_post_text(text: str) -> bool:
    body = strip_metadata_lines(strip_numbering(str(text or ""))).strip()
    body = re.sub(r"^引用元[:：].*$", "", body, flags=re.M).strip()
    body = re.sub(r"^掲載期間[:：].*$", "", body, flags=re.M).strip()
    normalized = re.sub(r"\s+", "", body)
    if normalized in {re.sub(r'\s+', '', item) for item in PLACEHOLDER_POST_TEXTS}:
        return False
    required_markers = ("広告分析vol", "【ビジネスモデル】", "【なぜこの広告が勝ってるか】", "【広告の学び】")
    return any(marker in body for marker in required_markers) and len(normalized) >= 40


def task_is_meaningful(task: Dict[str, Any]) -> bool:
    kind = str(task.get("kind") or "")
    if kind in {"reel", "youtube", "tiktok"}:
        text = str(task.get("caption") or task.get("description") or task.get("title") or "")
        return "勝ち広告を分析してみましたvol." in text
    return is_meaningful_post_text(task.get("text") or task.get("caption") or task.get("description") or "")


def state_has_meaningful_pending_tasks(state: Dict[str, Any]) -> bool:
    return any(task.get("status") != "posted" and task_is_meaningful(task) for task in state.get("tasks", []))


def state_within_daily_post_limit(state: Dict[str, Any]) -> bool:
    sections = state.get("sections") or []
    if sections and len(sections) > DAILY_MAX_POST_SECTIONS:
        return False
    section_numbers = {
        str(task.get("section_number") or "")
        for task in state.get("tasks", [])
        if task.get("status") != "posted" and task_is_meaningful(task) and task.get("section_number")
    }
    return len(section_numbers) <= DAILY_MAX_POST_SECTIONS


def section_ready(section: AdSection) -> bool:
    return is_meaningful_post_text(section_body_text(section))


def ready_ad_numbers(sections: Sequence[AdSection]) -> set[int]:
    ready = set()
    for section in sections:
        if section_ready(section):
            ready.add(section.number)
    return ready


def source_lines_for(sections: Sequence[AdSection]) -> List[str]:
    lines: List[str] = []
    for section in sections:
        for company in section.companies:
            line = f"引用元：{company}"
            if section.period_text:
                line += f" / 掲載期間：{section.period_text}"
            if line not in lines:
                lines.append(line)
    if not lines:
        lines.append("引用元：Notionページ内広告")
    return lines


def caption_for(sections: Sequence[AdSection], title: str = "勝ち広告を分析してみました") -> str:
    return title + "\n\n" + "\n".join(source_lines_for(sections))


def video_caption_for(section: AdSection) -> str:
    title = f"勝ち広告を分析してみましたvol.{section.number}"
    return caption_for([section], title)


def text_post_for(section: AdSection) -> str:
    text = section_body_text(section)
    return (text + "\n\n" + "\n".join(source_lines_for([section]))).strip()


def youtube_community_post_title(page_title: str, section: AdSection, chunk_index: int) -> str:
    service = section.service_name or (section.companies[0].split(" / ", 1)[1] if section.companies and " / " in section.companies[0] else "")
    suffix = service or f"投稿{chunk_index:02d}"
    return f"{page_title}_vol.{section.number}_{suffix}"


def hyperlink_formula(url: str, label: Optional[str] = None) -> str:
    escaped_url = url.replace('"', '""')
    escaped_label = (label or url).replace('"', '""')
    return f'=HYPERLINK("{escaped_url}","{escaped_label}")'


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def text_for_platform(platform: str, text: str) -> str:
    return text


def infer_genre(business_text: str) -> str:
    rules = [
        ("教育", ("講座", "スクール", "学習", "資格", "受講")),
        ("美容", ("美容", "サロン", "化粧", "スキンケア")),
        ("SaaS", ("SaaS", "月額", "クラウド", "業務効率")),
        ("金融", ("投資", "保険", "ローン", "資産")),
        ("不動産", ("不動産", "住宅", "賃貸", "物件")),
        ("EC", ("通販", "EC", "購入", "商品")),
    ]
    for genre, keywords in rules:
        if any(keyword in business_text for keyword in keywords):
            return genre
    return "広告・マーケティング"


def strip_numbering(text: str) -> str:
    text = re.sub(rf"^\s*([{CIRCLED_DIGITS}]|[0-9０-９]+[.)．、])\s*", "", text.strip())
    text = re.sub(r"掲載期間\s*[:：]?\s*[^\n]*(?:\n|$)", "", text)
    return text.strip()


def distribute(items: Sequence[Any], slots: Sequence[str]) -> List[str]:
    if not items:
        return []
    if len(slots) == 1:
        return [slots[0] for _ in items]
    return [slots[index * len(slots) // len(items)] for index, _item in enumerate(items)]


def offset_minutes_by_slot(slots: Sequence[str]) -> List[int]:
    counts: Dict[str, int] = {}
    offsets: List[int] = []
    for slot in slots:
        offset = counts.get(slot, 0)
        offsets.append(offset)
        counts[slot] = offset + 1
    return offsets


def chunked(values: Sequence[Any], size: int) -> List[List[Any]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def render_carousel_business_slide(index: int, total: int, text: str) -> Image.Image:
    return render_text_slide("ビジネスモデル", strip_metadata_lines(strip_numbering(text)))


def render_single_ad_carousel(section: AdSection, output_dir: Path) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not section.images:
        raise RuntimeError("カルーセル生成に使う広告画像がありません。")

    screenshot = Image.open(section.images[0])
    body = section_body_text(section)
    business = business_model_text(body)
    why = winning_reason_text(body)
    learning = learning_text(body)
    company = section.company_name or (section.companies[0].split(" / ", 1)[0] if section.companies else "")
    service = section.service_name or (section.companies[0].split(" / ", 1)[1] if section.companies and " / " in section.companies[0] else "")

    slides: List[tuple[str, Image.Image]] = [
        ("slide_01_cover.png", render_dynamic_cover(section.period_text or "◯ヶ月", screenshot)),
        ("slide_02_ad.png", render_today_ad_slide(company, service, screenshot, "w5")),
    ]
    for index, chunk in enumerate(split_near_periods(business, 3), start=1):
        slides.append((f"slide_{index + 2:02d}_business.png", render_segment_text_slide("ビジネスモデル", f"{index}/3", chunk, "w5", "top-right")))
    for index, chunk in enumerate(split_near_periods(why, 3), start=1):
        slides.append((f"slide_{index + 5:02d}_why.png", render_segment_text_slide("なぜこの広告が勝ってるか", f"{index}/3", chunk, "w5", "top-right")))
    slides.append(("slide_09_learning.png", render_segment_text_slide("今日の広告の学び", "", learning, "w5", "top-right")))
    slides.append(("slide_10_ending.png", render_carousel_ending_slide()))

    images: List[Image.Image] = []
    paths: List[Path] = []
    for filename, image in slides:
        path = output_dir / filename
        image.save(path)
        images.append(image)
        paths.append(path)

    pdf_path = output_dir / "linkedin_carousel.pdf"
    save_pdf(images, pdf_path)
    return {"slides": paths, "pdf": pdf_path}


def render_carousel_chunk(sections: Sequence[AdSection], output_dir: Path) -> Dict[str, Any]:
    if len(sections) == 1:
        return render_single_ad_carousel(sections[0], output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    slides = []
    image_urls = []
    ad_sections = [section for section in sections if section.number % 2 == 1 and section.images][:CAROUSEL_MAX_ADS]
    if not ad_sections:
        raise RuntimeError("カルーセル生成に使う広告画像がありません。")

    period = sections[0].period_text or "◯ヶ月"
    cover_title = CAROUSEL_COVER_TITLE_TEMPLATE.format(period=period)
    cover = render_carousel_cover_slide(cover_title, Image.open(ad_sections[0].images[0]))
    cover_path = output_dir / "slide_01.png"
    cover.save(cover_path)
    slides.append(cover)
    image_urls.append(cover_path)

    ad_index = 0
    content_sections: List[AdSection] = []
    allowed_ad_numbers = {section.number for section in ad_sections}
    for section in sections:
        if section.number % 2 == 1 and section.number not in allowed_ad_numbers:
            continue
        if section.number % 2 == 0:
            previous_ad_number = section.number - 1
            if previous_ad_number not in allowed_ad_numbers:
                continue
        content_sections.append(section)

    for index, section in enumerate(content_sections, start=2):
        if section.number % 2 == 0:
            slide = render_text_slide("ビジネスモデル", section_body_text(section))
        else:
            ad_index += 1
            image_path = section.images[0] if section.images else None
            if image_path is None:
                raise RuntimeError("カルーセル生成に使う画像がありません。")
            ad_label = CIRCLED_DIGITS[ad_index - 1] if ad_index <= len(CIRCLED_DIGITS) else str(ad_index)
            slide = render_slide(index, 10, f"広告分析{ad_label}", section_body_text(section), Image.open(image_path))
        slide_path = output_dir / f"slide_{index:02d}.png"
        slide.save(slide_path)
        slides.append(slide)
        image_urls.append(slide_path)

    ending = render_carousel_ending_slide()
    ending_path = output_dir / f"slide_{len(slides) + 1:02d}.png"
    ending.save(ending_path)
    slides.append(ending)
    image_urls.append(ending_path)

    pdf_path = output_dir / "linkedin_carousel.pdf"
    save_pdf(slides, pdf_path)
    return {"slides": image_urls, "pdf": pdf_path}


def render_reel_chunk(sections: Sequence[AdSection], output_dir: Path) -> Path:
    if len(sections) == 1:
        carousel = render_single_ad_carousel(sections[0], output_dir / "pages_source")
        reel_spec = ReelSpec(
            slide_duration=2.4,
            fade_duration=0,
            transition=REEL_STRUCTURED_TRANSITION,
            ending_duration=2.0,
        )
        video_slide_paths = carousel["slides"][:-1]
        pages = [
            ReelPage(path, 1.5 if index == 0 else 2.4)
            for index, path in enumerate(video_slide_paths)
        ]
        save_reel_thumbnail(pages, output_dir)
        video_path = output_dir / "reel.mp4"
        write_structured_mp4(pages, video_path, reel_spec)
        return video_path

    ad_sections = [section for section in sections if section.number % 2 == 1]
    business_sections = [section for section in sections if section.number % 2 == 0]
    ad_images = [section.images[0] for section in ad_sections if section.images]
    if not ad_images:
        raise RuntimeError("Reels生成に使う画像がありません。")
    ad_texts = [section_body_text(section) for section in ad_sections]
    business_texts = [section_body_text(section) for section in business_sections] or ad_texts
    period = sections[0].period_text or "◯ヶ月"
    title = REEL_COVER_TITLE_TEMPLATE.format(period=period)
    reel_spec = ReelSpec(
        slide_duration=REEL_STRUCTURED_PAGE_DURATION,
        fade_duration=0,
        transition=REEL_STRUCTURED_TRANSITION,
    )
    pages = build_structured_reel_pages(
        ad_images=ad_images,
        ad_texts=ad_texts,
        business_texts=business_texts,
        output_dir=output_dir / "pages",
        cover_title=title,
        max_ads=min(5, len(ad_images)),
        font_style=REEL_BODY_FONT_STYLE,
        cover_font_style=REEL_COVER_FONT_STYLE,
        cover_duration=REEL_COVER_DURATION,
        spec=reel_spec,
    )
    save_reel_thumbnail(pages, output_dir)
    video_path = output_dir / "reel.mp4"
    write_structured_mp4(pages, video_path, reel_spec)
    return video_path


def copy_public(path: Path, relative_path: Path) -> str:
    target = PUBLIC_ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return public_url(Path("public") / relative_path)


def create_plan(run_now: bool = False) -> Dict[str, Any]:
    cleanup_generated_assets()
    existing_state = load_state()
    if existing_state and existing_state.get("status") == "completed" and state_created_today(existing_state):
        existing_state["message"] = "本日分のNotionページは処理済みです。次の済みページは翌日の5:00以降に処理します。"
        save_state(existing_state)
        return existing_state
    if (
        existing_state
        and existing_state.get("status") == "planned"
        and existing_state.get("page_id")
        and state_has_meaningful_pending_tasks(existing_state)
        and state_within_daily_post_limit(existing_state)
    ):
        existing_state["status"] = "planned"
        save_state(existing_state)
        return existing_state

    candidate_pages = pages_by_status(("未投稿", "未着手"), page_size=20)
    if not candidate_pages:
        state = {"status": "idle", "message": "未投稿ページがありません。", "created_at": now_jst().isoformat()}
        save_state(state)
        return state

    run_id = os.getenv("GITHUB_RUN_ID", now_jst().strftime("%Y%m%d%H%M%S"))
    selected_page: Optional[Dict[str, Any]] = None
    selected_sections: List[AdSection] = []
    work_dir = RUNTIME_DIR / run_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    skipped_not_ready = []
    for page in candidate_pages:
        page_work_dir = work_dir / page["id"].replace("-", "")
        sections = extract_sections(page, page_work_dir)
        ready_numbers = ready_ad_numbers(sections)
        if ready_numbers:
            selected_page = page
            selected_sections = [
                section
                for section in sections
                if section.number in ready_numbers
            ][:DAILY_MAX_POST_SECTIONS]
            break
        skipped_not_ready.append(get_page_title(page))
    if not selected_page:
        state = {
            "status": "idle",
            "message": "投稿できる分析入力済みページがありません。",
            "skipped_not_ready": skipped_not_ready,
            "created_at": now_jst().isoformat(),
        }
        save_state(state)
        return state

    page = selected_page
    page_id = page["id"]
    update_notion_status(page_id, "進行中")
    sections = selected_sections
    if not sections:
        raise RuntimeError("投稿に使える入力済み本文がありません。")

    asset_prefix = Path("runs") / run_id
    tasks: List[Dict[str, Any]] = []
    text_slots = ["now"] * len(sections) if run_now else distribute(sections, TEXT_SLOTS)
    text_offsets = [0] * len(sections) if run_now else offset_minutes_by_slot(text_slots)
    for section, slot, offset_minutes in zip(sections, text_slots, text_offsets):
        text = text_post_for(section)
        image_url = None
        image_path = None
        if section.images:
            image_path = str(section.images[0])
            image_url = copy_public(section.images[0], asset_prefix / "source" / section.images[0].name)
        for platform in TEXT_PLATFORMS:
            tasks.append(
                {
                    "id": f"{platform}-{section.number}",
                    "kind": "text",
                    "platform": platform,
                    "section_number": section.number,
                    "slot": slot,
                    "slot_offset_minutes": offset_minutes,
                    "text": text_for_platform(platform, text),
                    "image_url": image_url,
                    "image_path": image_path,
                    "status": "pending",
                }
            )

    content_chunks = [[section] for section in sections]
    media_slots = ["now"] * len(sections) if run_now else distribute(sections, MEDIA_SLOTS)
    for chunk_index, (chunk, slot) in enumerate(zip(content_chunks, media_slots), start=1):
        carousel = render_carousel_chunk(chunk, work_dir / f"carousel_{chunk_index:02d}")
        section = chunk[0]
        carousel_caption = section_body_text(section) + "\n\n" + "\n".join(source_lines_for([section]))
        youtube_community_dir = export_youtube_community_images(
            carousel["slides"],
            chunk_index=chunk_index,
            post_title=youtube_community_post_title(get_page_title(page), section, chunk_index),
            caption=carousel_caption,
        )
        slide_urls = [
            copy_public(path, asset_prefix / f"carousel_{chunk_index:02d}" / path.name)
            for path in carousel["slides"]
        ]
        if len(slide_urls) == 1:
            slide_urls.append(slide_urls[0])
        pdf_public_url = copy_public(carousel["pdf"], asset_prefix / f"carousel_{chunk_index:02d}" / "linkedin_carousel.pdf")
        reel_path = render_reel_chunk(chunk, work_dir / f"reel_{chunk_index:02d}")
        video_caption = video_caption_for(section)
        facebook_manual_paths = export_facebook_manual_video(
            reel_path,
            run_id=run_id,
            chunk_index=chunk_index,
            caption=video_caption,
        )
        thumbnail_path = reel_path.parent / "thumbnail.png"
        reel_url = copy_public(reel_path, asset_prefix / f"reel_{chunk_index:02d}" / "reel.mp4")
        thumbnail_url = copy_public(thumbnail_path, asset_prefix / f"reel_{chunk_index:02d}" / "thumbnail.png")
        tasks.extend(
            [
                {
                    "id": f"instagram-carousel-{chunk_index}",
                    "kind": "carousel",
                    "platform": "instagram",
                    "section_number": section.number,
                    "slot": slot,
                    "caption": carousel_caption,
                    "image_urls": slide_urls,
                    "youtube_community_dir": str(youtube_community_dir) if youtube_community_dir else "",
                    "status": "pending",
                },
                {
                    "id": f"linkedin-carousel-{chunk_index}",
                    "kind": "linkedin_pdf",
                    "platform": "linkedin",
                    "section_number": section.number,
                    "slot": slot,
                    "caption": carousel_caption,
                    "title": CAROUSEL_CAPTION,
                    "pdf_path": str(carousel["pdf"]),
                    "pdf_url": pdf_public_url,
                    "status": "pending",
                },
                {
                    "id": f"instagram-reel-{chunk_index}",
                    "kind": "reel",
                    "platform": "instagram",
                    "section_number": section.number,
                    "slot": slot,
                    "caption": video_caption,
                    "video_url": reel_url,
                    "status": "pending",
                },
                {
                    "id": f"youtube-short-{chunk_index}",
                    "kind": "youtube",
                    "platform": "youtube",
                    "section_number": section.number,
                    "slot": slot,
                    "title": f"勝ち広告を分析してみましたvol.{section.number} #Shorts",
                    "description": video_caption,
                    "video_path": str(reel_path),
                    "video_url": reel_url,
                    "facebook_manual_video_path": str(facebook_manual_paths["video"]),
                    "facebook_manual_caption_path": str(facebook_manual_paths["caption"]),
                    "thumbnail_path": str(thumbnail_path),
                    "thumbnail_url": thumbnail_url,
                    "status": "pending",
                },
            ]
        )
        if TIKTOK_ENABLED:
            tasks.append(
                {
                    "id": f"tiktok-video-{chunk_index}",
                    "kind": "tiktok",
                    "platform": "tiktok",
                    "section_number": section.number,
                    "slot": slot,
                    "caption": video_caption,
                    "video_path": str(reel_path),
                    "video_url": reel_url,
                    "status": "pending",
                }
            )

    state = {
        "status": "planned",
        "page_id": page_id,
        "page_url": page.get("url"),
        "title": get_page_title(page),
        "run_id": run_id,
        "planned_date": now_jst().strftime("%Y-%m-%d"),
        "created_at": now_jst().isoformat(),
        "sections": [
            {
                "number": section.number,
                "text": section.text,
                "companies": section.companies,
                "period_text": section.period_text,
                "company_name": section.company_name,
                "service_name": section.service_name,
                "ad_library_url": section.ad_library_url,
                "lp_url": section.lp_url,
                "sheet_row": section.sheet_row,
                "appeal_type": section.appeal_type,
            }
            for section in sections
        ],
        "tasks": tasks,
    }
    save_state(state)
    return state


def split_threads_text(text: str, limit: int = THREADS_MAX_TEXT_LENGTH) -> List[str]:
    normalized = text.strip()
    if len(normalized) <= limit:
        return [normalized]
    chunks: List[str] = []
    remaining = normalized
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < max(1, limit // 2):
            cut = remaining.rfind("。", 0, limit + 1)
        if cut < max(1, limit // 2):
            cut = limit
        chunk = remaining[:cut].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].strip()
    return chunks


def create_threads_container(
    user_id: str,
    access_token: str,
    text: str,
    image_url: Optional[str] = None,
    reply_to_id: Optional[str] = None,
) -> str:
    payload = {"text": text, "access_token": access_token}
    if image_url:
        payload.update({"media_type": "IMAGE", "image_url": image_url})
    else:
        payload["media_type"] = "TEXT"
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    container = request_threads_api("POST", f"/{user_id}/threads", payload)
    creation_id = container.get("id")
    if not creation_id:
        raise RuntimeError(f"Threads投稿コンテナIDを取得できませんでした: {container}")
    published = request_threads_api(
        "POST",
        f"/{user_id}/threads_publish",
        {"creation_id": creation_id, "access_token": access_token},
    )
    post_id = published.get("id")
    if not post_id:
        raise RuntimeError(f"Threads投稿IDを取得できませんでした: {published}")
    return post_id


def create_threads_image_post(text: str, image_url: Optional[str]) -> str:
    credentials = load_threads_credentials()
    user_id = credentials["THREADS_USER_ID"]
    access_token = credentials["THREADS_ACCESS_TOKEN"]
    chunks = split_threads_text(text)
    root_id = ""
    previous_id: Optional[str] = None
    for index, chunk in enumerate(chunks):
        post_id = create_threads_container(
            user_id,
            access_token,
            chunk,
            image_url if index == 0 else None,
            previous_id,
        )
        if not root_id:
            root_id = post_id
        previous_id = post_id
    return f"Threads投稿ID: {root_id}" + (f"（返信{len(chunks) - 1}件）" if len(chunks) > 1 else "")


def create_x_post(text: str, image_url: Optional[str], image_path: Optional[str] = None) -> str:
    credentials = load_credentials()
    validation_error = validate_post_text(text, "x")
    if validation_error:
        raise RuntimeError(validation_error)
    client = build_client(credentials)
    if not image_url:
        return create_post(client, text)

    import tweepy

    auth = tweepy.OAuth1UserHandler(
        credentials["API_KEY"],
        credentials["API_SECRET"],
        credentials["ACCESS_TOKEN"],
        credentials["ACCESS_TOKEN_SECRET"],
    )
    api = tweepy.API(auth)
    media_path = Path(image_path) if image_path and Path(image_path).exists() else None
    if media_path is None:
        tmp = RUNTIME_DIR / "x_media"
        tmp.mkdir(parents=True, exist_ok=True)
        media_path = download_file(image_url, tmp, int(time.time()) % 100000)
    media = api.media_upload(str(media_path))
    response = client.create_tweet(text=text, media_ids=[media.media_id_string])
    tweet_id = response.data.get("id") if response.data else None
    if not tweet_id:
        raise RuntimeError(f"X投稿IDを取得できませんでした: {response}")
    return f"https://x.com/i/web/status/{tweet_id}"


def execute_task(task: Dict[str, Any]) -> str:
    kind = task["kind"]
    platform = task.get("platform")
    if platform in {"threads", "instagram", "facebook", "linkedin", "youtube", "tiktok"}:
        ensure_token_fresh(platform, strict=False)
    if kind == "text":
        text = task["text"]
        image_url = task.get("image_url")
        if platform == "x":
            return create_x_post(text, image_url, task.get("image_path"))
        if platform == "threads":
            return create_threads_image_post(text, image_url)
        if platform == "facebook":
            credentials = load_facebook_credentials()
            if image_url:
                return create_facebook_photo_post(credentials, image_url, text)
            return create_facebook_text_post(credentials, text)
    if kind == "carousel":
        return post_instagram_carousel(task["image_urls"][:10], task["caption"])
    if kind == "linkedin_pdf":
        pdf_path = Path(task["pdf_path"])
        if not pdf_path.exists():
            response = requests.get(task["pdf_url"], timeout=60)
            response.raise_for_status()
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(response.content)
        return post_linkedin_pdf(pdf_path, task["caption"], task.get("title", "広告分析"))
    if kind == "reel":
        return post_instagram_reel(task["video_url"], task["caption"])
    if kind == "youtube":
        video_path = Path(task["video_path"])
        if not video_path.exists():
            video_url = task["video_url"]
            response = requests.get(video_url, timeout=120)
            response.raise_for_status()
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(response.content)
        thumbnail_path = Path(task["thumbnail_path"]) if task.get("thumbnail_path") else None
        if thumbnail_path and not thumbnail_path.exists() and task.get("thumbnail_url"):
            response = requests.get(task["thumbnail_url"], timeout=60)
            response.raise_for_status()
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            thumbnail_path.write_bytes(response.content)
        return upload_youtube_short(
            video_path,
            task["title"],
            task["description"],
            ["広告", "マーケティング", "Shorts"],
            thumbnail_path=thumbnail_path if thumbnail_path and thumbnail_path.exists() else None,
        )
    if kind == "tiktok":
        video_path = Path(task["video_path"])
        if not video_path.exists():
            video_url = task["video_url"]
            response = requests.get(video_url, timeout=120)
            response.raise_for_status()
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(response.content)
        return upload_tiktok_video(video_path, task["caption"])
    raise RuntimeError(f"未対応タスクです: {task}")


def due_tasks(state: Dict[str, Any], slot: str, run_now: bool) -> List[Dict[str, Any]]:
    if run_now:
        return [
            task
            for task in state.get("tasks", [])
            if task.get("status") != "posted" and task_is_meaningful(task)
        ]
    tasks = [
        task
        for task in state.get("tasks", [])
        if task.get("slot") == slot
        and task.get("status") != "posted"
        and task_is_meaningful(task)
    ]
    return sorted(
        tasks,
        key=lambda task: (
            int(task.get("slot_offset_minutes") or 0),
            0 if task.get("kind") == "text" else 1,
            str(task.get("id", "")),
        ),
    )


def wait_for_slot_offset(task: Dict[str, Any], started_at: float, run_now: bool) -> None:
    if run_now:
        return
    offset_minutes = int(task.get("slot_offset_minutes") or 0)
    if offset_minutes <= 0:
        return
    target_elapsed = offset_minutes * 60
    remaining = target_elapsed - (time.monotonic() - started_at)
    if remaining > 0:
        time.sleep(remaining)


def sheet_id_for(service: Any, spreadsheet_id: str, sheet_name: str) -> int:
    spreadsheet = get_spreadsheet(service, spreadsheet_id)
    for sheet in spreadsheet.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") == sheet_name:
            return int(properties["sheetId"])
    raise RuntimeError(f"シートが見つかりません: {sheet_name}")


def column_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def ensure_headers(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str], required_headers: Sequence[str]) -> List[str]:
    changed = False
    next_headers = list(headers)
    for header in required_headers:
        if header not in next_headers:
            next_headers.append(header)
            changed = True
    if changed:
        end = column_letter(len(next_headers) - 1)
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:{end}1", [next_headers])
    return next_headers


def row_to_header_map(row: List[Any], headers: List[str]) -> Dict[str, Any]:
    return {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}


def build_row_for_headers(values: Dict[str, Any], headers: List[str]) -> List[Any]:
    return [values.get(header, "") for header in headers]


def sort_master_by_genre(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str], row_count: int) -> None:
    if row_count <= 2 or "ジャンル" not in headers:
        return
    sheet_id = sheet_id_for(service, spreadsheet_id, sheet_name)
    sort_specs = [{"dimensionIndex": headers.index("ジャンル"), "sortOrder": "ASCENDING"}]
    if "サブジャンル" in headers:
        sort_specs.append({"dimensionIndex": headers.index("サブジャンル"), "sortOrder": "ASCENDING"})
    if "サービス名" in headers:
        sort_specs.append({"dimensionIndex": headers.index("サービス名"), "sortOrder": "ASCENDING"})
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "sortRange": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": row_count,
                            "startColumnIndex": 0,
                            "endColumnIndex": len(headers),
                        },
                        "sortSpecs": sort_specs,
                    }
                }
            ]
        },
    ).execute()


def delete_sheet_rows(service: Any, spreadsheet_id: str, sheet_name: str, row_numbers: Sequence[int]) -> None:
    if not row_numbers:
        return
    sheet_id = sheet_id_for(service, spreadsheet_id, sheet_name)
    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row_number - 1,
                    "endIndex": row_number,
                }
            }
        }
        for row_number in sorted({int(row) for row in row_numbers if int(row) >= 2}, reverse=True)
    ]
    if requests:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def append_sheet_row(state: Dict[str, Any]) -> None:
    sections = state.get("sections", [])
    x_urls_by_section = {
        int(str(task.get("id", "")).split("-", 1)[1]): task.get("post_url", "")
        for task in state.get("tasks", [])
        if task.get("platform") == "x" and task.get("post_url") and str(task.get("id", "")).startswith("x-")
    }
    sections_by_number = {int(section.get("number", 0)): section for section in sections}
    completed_ads: List[Dict[str, Any]] = []
    for section in sections:
        number = int(section.get("number", 0))
        analysis = section_body_text(AdSection(number=number, text=str(section.get("text", ""))))
        business = business_model_text(analysis)
        genre = infer_genre(business or analysis)
        x_url = x_urls_by_section.get(number, "")
        completed_ads.append(
            {
                "number": number,
                "genre": genre,
                "service_name": section.get("service_name", ""),
                "company_name": section.get("company_name", ""),
                "ad_library_url": section.get("ad_library_url", ""),
                "sheet_row": section.get("sheet_row"),
                "analysis": analysis,
                "business": business,
                "appeal_type": section.get("appeal_type", ""),
                "x_url": hyperlink_formula(x_url) if x_url else "",
            }
        )
    if not completed_ads:
        return
    config = load_sheets_config()
    service = build_sheets_service(config)
    today_sheet = os.getenv("TODAY_AD_DB_SHEET", DEFAULT_TODAY_AD_DB_SHEET)
    master_sheet = os.getenv("AD_ANALYSIS_MASTER_SHEET", DEFAULT_AD_ANALYSIS_MASTER_SHEET)
    spreadsheet_id = os.getenv("AD_ANALYSIS_SPREADSHEET_ID", DEFAULT_AD_ANALYSIS_SPREADSHEET_ID)
    today_values = read_values(service, spreadsheet_id, f"{quote_sheet_name(today_sheet)}!A1:AZ")
    master_values = read_values(service, spreadsheet_id, f"{quote_sheet_name(master_sheet)}!A1:AZ")
    today_headers = today_values[0] if today_values else []
    master_headers = master_values[0] if master_values else today_headers
    master_headers = ensure_headers(
        service,
        spreadsheet_id,
        master_sheet,
        master_headers,
        ["訴求の型", "分析日", "状況", "最終掲載期間"],
    )
    today_headers = ensure_headers(
        service,
        spreadsheet_id,
        today_sheet,
        today_headers,
        ["訴求の型"],
    )
    if {"広告ライブラリURL", "広告分析", "ビジネスモデル", "訴求の型"}.difference(set(today_headers)):
        raise RuntimeError(f"{today_sheet} に必要なヘッダーがありません。")
    if {"広告ライブラリURL", "広告分析", "ビジネスモデル", "訴求の型"}.difference(set(master_headers)):
        raise RuntimeError(f"{master_sheet} に必要なヘッダーがありません。")

    today_index = {header: index for index, header in enumerate(today_headers)}
    url_to_row = {}
    rows_by_number = {}
    for row_number, row in enumerate(today_values[1:], start=2):
        rows_by_number[row_number] = row
        url_index = today_index["広告ライブラリURL"]
        if url_index < len(row) and row[url_index]:
            url_to_row[str(row[url_index]).strip()] = row_number

    master_rows: List[List[Any]] = []
    delete_rows: List[int] = []
    today_updates: Dict[int, Dict[str, Any]] = {}
    for ad in completed_ads:
        target_row = int(ad["sheet_row"]) if str(ad.get("sheet_row") or "").isdigit() else None
        if not target_row and ad["ad_library_url"]:
            target_row = url_to_row.get(str(ad["ad_library_url"]).strip())
        if not target_row or target_row not in rows_by_number:
            continue

        current = row_to_header_map(rows_by_number[target_row], today_headers)
        updates = {
            "分析状況": "分析済み",
            "会社名": ad["company_name"] or current.get("会社名", ""),
            "サービス名": ad["service_name"] or current.get("サービス名", ""),
            "広告分析": ad["analysis"],
            "ビジネスモデル": ad["business"],
            "訴求の型": ad["appeal_type"],
            "X投稿URL": ad["x_url"],
            "分析日": now_jst().strftime("%Y-%m-%d"),
            "状況": "掲載中",
        }
        if not current.get("ジャンル") and ad.get("genre"):
            updates["ジャンル"] = ad["genre"]
        current.update({key: value for key, value in updates.items() if value not in (None, "")})
        today_updates[target_row] = updates
        master_rows.append(build_row_for_headers(current, master_headers))
        delete_rows.append(target_row)

    for row_number, updates in today_updates.items():
        for header, value in updates.items():
            if header not in today_index or value in (None, ""):
                continue
            column = chr(ord("A") + today_index[header])
            update_values(service, spreadsheet_id, f"{quote_sheet_name(today_sheet)}!{column}{row_number}:{column}{row_number}", [[value]])

    if master_rows:
        append_values(service, spreadsheet_id, f"{quote_sheet_name(master_sheet)}!A:AZ", master_rows)
        refreshed_master = read_values(service, spreadsheet_id, f"{quote_sheet_name(master_sheet)}!A1:AZ")
        sort_master_by_genre(service, spreadsheet_id, master_sheet, master_headers, len(refreshed_master))
        delete_sheet_rows(service, spreadsheet_id, today_sheet, delete_rows)
        freeze_row_height(service, spreadsheet_id, master_sheet, pixel_size=20)
        freeze_row_height(service, spreadsheet_id, today_sheet, pixel_size=20)


def execute_due(slot: str, run_now: bool = False) -> Dict[str, Any]:
    state = load_state()
    if not state or state.get("status") == "idle":
        state = create_plan(run_now=run_now)
    if state.get("status") == "completed":
        return state
    if state.get("status") == "idle":
        return state
    if not planned_page_still_approved(state):
        state["status"] = "idle"
        state["message"] = f"Notionの{NOTION_READY_PROPERTY}が{NOTION_READY_VALUE}ではないため投稿を停止しました。"
        save_state(state)
        return state
    if not state_within_daily_post_limit(state):
        state["status"] = "idle"
        state["message"] = f"投稿計画が1日上限{DAILY_MAX_POST_SECTIONS}本を超えているため停止しました。"
        save_state(state)
        return state

    errors: List[str] = []
    started_at = time.monotonic()
    for task in due_tasks(state, slot, run_now):
        try:
            wait_for_slot_offset(task, started_at, run_now)
            update_task_platform_status(state, task, "進行中")
            task["post_url"] = execute_task(task)
            task["status"] = "posted"
            task["posted_at"] = now_jst().isoformat()
            task["error"] = ""
            refresh_completed_platform_statuses(state)
        except Exception as error:
            task["status"] = "error"
            task["error"] = str(error)
            errors.append(f"{task.get('id')}: {error}")
            try:
                update_task_platform_status(state, task, "エラー")
            except Exception as status_error:
                print(f"Notion status update skipped: {status_error}", file=sys.stderr)
        finally:
            save_state(state)

    if errors:
        state["status"] = "error"
        save_state(state)
        raise RuntimeError("\n".join(errors))

    if all(task.get("status") == "posted" for task in state.get("tasks", [])):
        append_sheet_row(state)
        refresh_completed_platform_statuses(state)
        state["status"] = "completed"
        state["completed_at"] = now_jst().isoformat()
        save_state(state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notion広告分析ページをSNSへ完全自動投稿します。")
    parser.add_argument("--prepare", action="store_true", help="最古の未投稿ページを取得し、アセットと投稿計画を作成します。")
    parser.add_argument("--execute", action="store_true", help="指定スロットの投稿を実行します。")
    parser.add_argument("--slot", default=os.getenv("POST_SLOT", "now"), help="12:00 / 16:00 / 18:00 / 20:30 / now")
    parser.add_argument("--run-now", action="store_true", help="テスト用に全タスクを即時実行します。")
    return parser.parse_args()


def main() -> int:
    load_environment()
    args = parse_args()
    if args.prepare:
        state = create_plan(run_now=args.run_now)
        print(json.dumps({"status": state.get("status"), "tasks": len(state.get("tasks", []))}, ensure_ascii=False))
    if args.execute or args.run_now:
        state = execute_due(args.slot, run_now=args.run_now)
        print(json.dumps({"status": state.get("status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"エラー: {error}", file=sys.stderr)
        raise SystemExit(1)
