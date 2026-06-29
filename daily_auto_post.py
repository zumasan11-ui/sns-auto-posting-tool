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

from carousel_generator import render_slide, save_pdf
from carousel_poster import post_instagram_carousel, post_linkedin_pdf
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
    retrieve_block_children,
    update_page,
)
from reels_generator import (
    ReelSpec,
    build_structured_reel_pages,
    post_instagram_reel,
    write_structured_mp4,
)
from sheets_api import append_values, build_sheets_service, load_sheets_config
from youtube_poster import upload_youtube_short


JST_TZ = "Asia/Tokyo"
STATUS_PROPERTY = os.getenv("NOTION_STATUS_PROPERTY") or "Status"
ERROR_PROPERTY = os.getenv("NOTION_ERROR_PROPERTY") or "エラー内容"
STATE_DIR = Path(os.getenv("AUTO_POST_STATE_DIR", "public_state"))
PUBLIC_ROOT = STATE_DIR / "public"
STATE_FILE = STATE_DIR / "state" / "current.json"
RUNTIME_DIR = Path("deliverables/auto_post")
SLOTS = ("07:30", "12:00", "16:00", "19:30")
TWO_SLOT_TIMES = ("07:30", "19:30")
CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


@dataclass
class AdSection:
    number: int
    text: str
    images: List[Path] = field(default_factory=list)
    companies: List[str] = field(default_factory=list)
    period_text: str = ""


def now_jst() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(JST_TZ))


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


def status_property_payload(status: str) -> Dict[str, Any]:
    return {STATUS_PROPERTY: {"status": {"name": status}}}


def error_property_payload(message: str) -> Dict[str, Any]:
    return {ERROR_PROPERTY: {"rich_text": [{"text": {"content": message[:1900]}}]}}


def update_notion_status(page_id: str, status: str, error: str = "") -> None:
    config = load_notion_config()
    properties = status_property_payload(status)
    if error:
        properties.update(error_property_payload(error))
    update_page(config, page_id, properties=properties)


def oldest_page_by_status(statuses: Sequence[str]) -> Optional[Dict[str, Any]]:
    config = load_notion_config()
    for status in statuses:
        pages = query_database(
            config,
            page_size=1,
            filter_data={"property": STATUS_PROPERTY, "status": {"equals": status}},
            sorts=[{"timestamp": "created_time", "direction": "ascending"}],
        )
        if pages:
            return pages[0]
    return None


def ensure_state_branch() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if (STATE_DIR / ".git").exists():
        return
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repo:
        return
    url = f"https://x-access-token:{os.getenv('GITHUB_TOKEN', '')}@github.com/{repo}.git"
    result = subprocess.run(
        ["git", "clone", "--branch", "gh-pages", "--single-branch", url, str(STATE_DIR)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    subprocess.run(["git", "init", str(STATE_DIR)], check=True)
    subprocess.run(["git", "-C", str(STATE_DIR), "checkout", "-b", "gh-pages"], check=True)
    subprocess.run(["git", "-C", str(STATE_DIR), "remote", "add", "origin", url], check=True)


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
            if current:
                current.images.append(image_path)
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

    for section in sections:
        if not section.images and images:
            section.images.append(images[min(section.number - 1, len(images) - 1)])
        section.companies = extract_companies(section.text)
        section.period_text = extract_period_text(section.text)
    return sections


def extract_companies(text: str) -> List[str]:
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
        return "○ヶ月"
    value = match.group(1)
    year_match = re.search(r"([0-9０-９]+)\s*年", value)
    month_match = re.search(r"([0-9０-９]+)\s*ヶ?月", value)
    if year_match:
        return f"{year_match.group(1)}年"
    if month_match:
        return f"{month_match.group(1)}ヶ月"
    return "○ヶ月"


def caption_for(sections: Sequence[AdSection], title: str = "勝ち広告を分析してみました") -> str:
    companies: List[str] = []
    for section in sections:
        for company in section.companies:
            if company not in companies:
                companies.append(company)
    if not companies:
        companies.append("Notionページ内広告")
    return title + "\n" + "\n".join(f"引用元：{company}" for company in companies)


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
    text = re.sub(r"掲載期間[:：]?[^\n]*\n?", "", text)
    return text.strip()


def distribute(items: Sequence[Any], slots: Sequence[str]) -> List[str]:
    if not items:
        return []
    if len(slots) == 1:
        return [slots[0] for _ in items]
    return [slots[index * len(slots) // len(items)] for index, _item in enumerate(items)]


def chunked(values: Sequence[Any], size: int) -> List[List[Any]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def render_carousel_chunk(sections: Sequence[AdSection], output_dir: Path) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    slides = []
    image_urls = []
    for index, section in enumerate(sections, start=1):
        image_path = section.images[0] if section.images else None
        if image_path is None:
            raise RuntimeError("カルーセル生成に使う画像がありません。")
        slide = render_slide(index, len(sections), "広告分析メモ", strip_numbering(section.text), Image.open(image_path))
        slide_path = output_dir / f"slide_{index:02d}.png"
        slide.save(slide_path)
        slides.append(slide)
        image_urls.append(slide_path)
    pdf_path = output_dir / "linkedin_carousel.pdf"
    save_pdf(slides, pdf_path)
    return {"slides": image_urls, "pdf": pdf_path}


def render_reel_chunk(sections: Sequence[AdSection], output_dir: Path) -> Path:
    ad_images = [section.images[0] for section in sections if section.images]
    if not ad_images:
        raise RuntimeError("Reels生成に使う画像がありません。")
    ad_texts = [strip_numbering(section.text) for section in sections]
    business_texts = [section.text for section in sections]
    period = sections[0].period_text or "○ヶ月"
    title = f"なぜこの広告は{period}回っているのか？"
    pages = build_structured_reel_pages(
        ad_images=ad_images,
        ad_texts=ad_texts,
        business_texts=business_texts,
        output_dir=output_dir / "pages",
        cover_title=title,
        max_ads=min(10, len(ad_images)),
        spec=ReelSpec(slide_duration=2.0, fade_duration=0, transition="none"),
    )
    video_path = output_dir / "reel.mp4"
    write_structured_mp4(pages, video_path, ReelSpec(slide_duration=2.0, fade_duration=0, transition="none"))
    return video_path


def copy_public(path: Path, relative_path: Path) -> str:
    target = PUBLIC_ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return public_url(Path("public") / relative_path)


def create_plan(run_now: bool = False) -> Dict[str, Any]:
    page = oldest_page_by_status(("進行中", "エラー", "未投稿"))
    if not page:
        state = {"status": "idle", "message": "未投稿ページがありません。", "created_at": now_jst().isoformat()}
        save_state(state)
        return state

    page_id = page["id"]
    update_notion_status(page_id, "進行中")
    run_id = os.getenv("GITHUB_RUN_ID", now_jst().strftime("%Y%m%d%H%M%S"))
    work_dir = RUNTIME_DIR / run_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    sections = extract_sections(page, work_dir)
    if not sections:
        raise RuntimeError("投稿に使える番号付き本文がありません。")

    asset_prefix = Path("runs") / run_id
    tasks: List[Dict[str, Any]] = []
    text_slots = ["now"] * len(sections) if run_now else distribute(sections, SLOTS)
    for section, slot in zip(sections, text_slots):
        text = strip_numbering(section.text)
        image_url = None
        if section.images:
            image_url = copy_public(section.images[0], asset_prefix / "source" / section.images[0].name)
        for platform in ("x", "threads", "facebook"):
            tasks.append(
                {
                    "id": f"{platform}-{section.number}",
                    "kind": "text",
                    "platform": platform,
                    "slot": slot,
                    "text": text,
                    "image_url": image_url,
                    "status": "pending",
                }
            )

    content_chunks = chunked(sections[:], 10)
    media_slots = ["now"] * len(content_chunks) if run_now else (["19:30"] if len(content_chunks) == 1 else distribute(content_chunks, TWO_SLOT_TIMES))
    for chunk_index, (chunk, slot) in enumerate(zip(content_chunks, media_slots), start=1):
        carousel = render_carousel_chunk(chunk, work_dir / f"carousel_{chunk_index:02d}")
        slide_urls = [
            copy_public(path, asset_prefix / f"carousel_{chunk_index:02d}" / path.name)
            for path in carousel["slides"]
        ]
        if len(slide_urls) == 1:
            slide_urls.append(slide_urls[0])
        pdf_public_url = copy_public(carousel["pdf"], asset_prefix / f"carousel_{chunk_index:02d}" / "linkedin_carousel.pdf")
        reel_path = render_reel_chunk(chunk, work_dir / f"reel_{chunk_index:02d}")
        reel_url = copy_public(reel_path, asset_prefix / f"reel_{chunk_index:02d}" / "reel.mp4")
        caption = caption_for(chunk)
        tasks.extend(
            [
                {
                    "id": f"instagram-carousel-{chunk_index}",
                    "kind": "carousel",
                    "platform": "instagram",
                    "slot": slot,
                    "caption": caption,
                    "image_urls": slide_urls,
                    "status": "pending",
                },
                {
                    "id": f"linkedin-carousel-{chunk_index}",
                    "kind": "linkedin_pdf",
                    "platform": "linkedin",
                    "slot": slot,
                    "caption": caption,
                    "pdf_path": str(carousel["pdf"]),
                    "pdf_url": pdf_public_url,
                    "status": "pending",
                },
                {
                    "id": f"instagram-reel-{chunk_index}",
                    "kind": "reel",
                    "platform": "instagram",
                    "slot": slot,
                    "caption": caption,
                    "video_url": reel_url,
                    "status": "pending",
                },
                {
                    "id": f"youtube-short-{chunk_index}",
                    "kind": "youtube",
                    "platform": "youtube",
                    "slot": slot,
                    "title": "勝ち広告を分析してみました #Shorts",
                    "description": caption,
                    "video_path": str(reel_path),
                    "video_url": reel_url,
                    "status": "pending",
                },
            ]
        )

    state = {
        "status": "planned",
        "page_id": page_id,
        "page_url": page.get("url"),
        "title": get_page_title(page),
        "run_id": run_id,
        "created_at": now_jst().isoformat(),
        "sections": [
            {
                "number": section.number,
                "text": section.text,
                "companies": section.companies,
                "period_text": section.period_text,
            }
            for section in sections
        ],
        "tasks": tasks,
    }
    save_state(state)
    return state


def create_threads_image_post(text: str, image_url: Optional[str]) -> str:
    credentials = load_threads_credentials()
    user_id = credentials["THREADS_USER_ID"]
    access_token = credentials["THREADS_ACCESS_TOKEN"]
    payload = {"text": text, "access_token": access_token}
    if image_url:
        payload.update({"media_type": "IMAGE", "image_url": image_url})
    else:
        payload["media_type"] = "TEXT"
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
    return f"Threads投稿ID: {post_id}"


def create_x_post(text: str, image_url: Optional[str]) -> str:
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
    if kind == "text":
        text = task["text"]
        image_url = task.get("image_url")
        if task["platform"] == "x":
            return create_x_post(text, image_url)
        if task["platform"] == "threads":
            return create_threads_image_post(text, image_url)
        if task["platform"] == "facebook":
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
        return post_linkedin_pdf(pdf_path, task["caption"], "勝ち広告を分析してみました")
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
        return upload_youtube_short(video_path, task["title"], task["description"], ["広告", "マーケティング", "Shorts"])
    raise RuntimeError(f"未対応タスクです: {task}")


def due_tasks(state: Dict[str, Any], slot: str, run_now: bool) -> List[Dict[str, Any]]:
    if run_now:
        return [task for task in state.get("tasks", []) if task.get("status") != "posted"]
    return [
        task
        for task in state.get("tasks", [])
        if task.get("slot") == slot and task.get("status") != "posted"
    ]


def append_sheet_row(state: Dict[str, Any]) -> None:
    sections = state.get("sections", [])
    analysis = "\n\n".join(section.get("text", "") for section in sections)
    business = analysis
    service_name = state.get("title", "広告分析")
    x_urls = [
        task.get("post_url", "")
        for task in state.get("tasks", [])
        if task.get("platform") == "x" and task.get("post_url")
    ]
    genre = infer_genre(business)
    config = load_sheets_config()
    service = build_sheets_service(config)
    append_values(
        service,
        config["spreadsheet_id"],
        config["default_sheet"],
        [[genre, service_name, "\n".join(x_urls), analysis, business]],
    )


def execute_due(slot: str, run_now: bool = False) -> Dict[str, Any]:
    state = load_state()
    if not state or state.get("status") == "idle":
        state = create_plan(run_now=run_now)
    if state.get("status") == "idle":
        return state

    errors: List[str] = []
    for task in due_tasks(state, slot, run_now):
        try:
            task["post_url"] = execute_task(task)
            task["status"] = "posted"
            task["posted_at"] = now_jst().isoformat()
            task["error"] = ""
        except Exception as error:
            task["status"] = "error"
            task["error"] = str(error)
            errors.append(f"{task.get('id')}: {error}")
            break
        finally:
            save_state(state)

    if errors:
        state["status"] = "error"
        update_notion_status(state["page_id"], "エラー", "\n".join(errors))
        save_state(state)
        raise RuntimeError("\n".join(errors))

    if all(task.get("status") == "posted" for task in state.get("tasks", [])):
        append_sheet_row(state)
        update_notion_status(state["page_id"], "完了")
        state["status"] = "completed"
        state["completed_at"] = now_jst().isoformat()
        save_state(state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notion広告分析ページをSNSへ完全自動投稿します。")
    parser.add_argument("--prepare", action="store_true", help="最古の未投稿ページを取得し、アセットと投稿計画を作成します。")
    parser.add_argument("--execute", action="store_true", help="指定スロットの投稿を実行します。")
    parser.add_argument("--slot", default=os.getenv("POST_SLOT", "now"), help="07:30 / 12:00 / 16:00 / 19:30 / now")
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
