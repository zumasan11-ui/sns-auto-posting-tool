import argparse
import html
import json
import re
import sys
from pathlib import Path
from collections import Counter
from typing import Any, Dict, Iterable, List
from urllib.parse import quote_plus, urlparse

import requests
from googleapiclient.errors import HttpError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import append_values, build_sheets_service, get_spreadsheet, load_sheets_config, read_values, update_values
from scripts.search_history import HISTORY_SHEET, update_search_history


DEFAULT_SPREADSHEET = "https://docs.google.com/spreadsheets/d/15mskJs84UE7-CUtwELlCnjw3_DoWpAIYnZUvqiJvrdc/edit"
MASTER_SHEET = "広告分析マスターDB"
DEFAULT_SHEET = MASTER_SHEET
DEFAULT_SEARCH_NAME = "医療脱毛"
DEFAULT_GENRE = ""
DEFAULT_SUB_GENRE = ""
REQUIRED_HEADERS = [
    "検索名",
    "ジャンル",
    "サブジャンル",
    "会社名",
    "サービス名",
    "掲載開始日",
    "掲載期間",
    "広告ライブラリURL",
    "LP URL",
    "広告スクショ",
    "広告分析",
    "コピー",
    "ビジネスモデル",
    "訴求の型",
    "X投稿URL",
    "分析日",
    "分析状況",
    "状態",
    "最終掲載期間",
    "メモ",
]
BAD_NAME_PREFIXES = (
    "この広告には",
    "ドロップダウンを開く",
    "ライブラリID:",
    "掲載開始日:",
    "アクティブ",
    "スポンサー広告",
    "広告の詳細を見る",
)
BAD_NAME_VALUES = {"", "\u200b", "learn more", "詳しくはこちら", "詳細を表示"}
MOJIBAKE_MARKERS = ("ã", "Ã", "Â", "�")
COMPANY_MARKERS = ("株式会社", "有限会社", "合同会社", "医療法人社団", "医療法人", "一般社団法人", "学校法人")
LP_TITLE_CACHE: Dict[str, str] = {}
COMPANY_SEARCH_CACHE: Dict[str, str] = {}


def log(message: str) -> None:
    print(f"[visible-card-append] {message}", flush=True)


def parse_spreadsheet_id(value: str) -> str:
    value = value.strip()
    match = re.search(r"/spreadsheets/d/([^/]+)", value)
    return match.group(1) if match else value


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def find_sheet_and_headers(service: Any, spreadsheet_id: str, requested_sheet: str) -> tuple[str, List[str]]:
    spreadsheet = get_spreadsheet(service, spreadsheet_id)
    titles = [sheet.get("properties", {}).get("title", "") for sheet in spreadsheet.get("sheets", [])]
    candidates = [requested_sheet] if requested_sheet else []
    candidates.extend([title for title in titles if title not in candidates])
    for title in candidates:
        if not title:
            continue
        values = read_values(service, spreadsheet_id, f"{quote_sheet_name(title)}!A1:Z1")
        headers = values[0] if values else []
        if all(header in headers for header in REQUIRED_HEADERS):
            return title, headers
    raise RuntimeError("必要なヘッダーを持つシートが見つかりません。")


def ensure_sheet_headers(service: Any, spreadsheet_id: str, sheet_name: str) -> List[str]:
    spreadsheet = get_spreadsheet(service, spreadsheet_id)
    titles = [sheet.get("properties", {}).get("title", "") for sheet in spreadsheet.get("sheets", [])]
    if sheet_name not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        ).execute()
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:Z1")
    headers = values[0] if values else []
    if not headers:
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:{chr(ord('A') + len(REQUIRED_HEADERS) - 1)}1", [REQUIRED_HEADERS])
        return REQUIRED_HEADERS
    if all(header in headers for header in REQUIRED_HEADERS):
        return headers
    updated = list(headers)
    for header in REQUIRED_HEADERS:
        if header not in updated:
            updated.append(header)
    update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:{chr(ord('A') + len(updated) - 1)}1", [updated])
    return updated


def existing_ad_urls(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str]) -> set[str]:
    index = headers.index("広告ライブラリURL")
    column_letter = chr(ord("A") + index)
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!{column_letter}2:{column_letter}")
    return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}


def existing_ad_urls_across_sheets(service: Any, spreadsheet_id: str, sheet_headers: Dict[str, List[str]]) -> set[str]:
    urls = set()
    for sheet_name, headers in sheet_headers.items():
        try:
            urls.update(existing_ad_urls(service, spreadsheet_id, sheet_name, headers))
        except Exception as error:
            log(f"既存URL確認をスキップ: {sheet_name} / {error}")
    return urls


def read_sheet_rows(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str]) -> List[Dict[str, Any]]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A2:Z")
    rows: List[Dict[str, Any]] = []
    for row in values:
        rows.append({header: row[index] if index < len(row) else "" for index, header in enumerate(headers)})
    return rows


def read_sheet_headers(service: Any, spreadsheet_id: str, sheet_name: str) -> List[str]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:Z1")
    return values[0] if values else []


def first_value(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def clean_name(value: Any) -> str:
    text = str(value or "").replace("\u200b", "").strip()
    text = re.sub(r"\s+", " ", text)
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return ""
    if text.lower() in BAD_NAME_VALUES:
        return ""
    if any(text.startswith(prefix) for prefix in BAD_NAME_PREFIXES):
        return ""
    if re.fullmatch(r"https?://\S+", text, flags=re.I):
        return ""
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(?:/.*)?", text, flags=re.I):
        return ""
    return text


def normalize_diversity_key(value: Any) -> str:
    text = clean_name(value).casefold()
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"[\s　・･／/|｜\\ー_.,，。、:：!！?？【】「」『』\"'“”‘’\-]", "", text)
    return text


def lp_diversity_key(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = re.sub(r"/+$", "", parsed.path or "")
    return f"{host}{path}".strip("/")


def lp_url(row: Dict[str, Any]) -> str:
    return str(first_value(row, ("LP URL", "lp_url", "link_url", "destination_url")) or "").strip()


def fetch_lp_title(url: str) -> str:
    if not url:
        return ""
    if url in LP_TITLE_CACHE:
        return LP_TITLE_CACHE[url]
    title = ""
    try:
        response = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome Safari"},
            allow_redirects=True,
        )
        content_type = response.headers.get("content-type", "")
        if response.ok and "text/html" in content_type:
            if response.apparent_encoding:
                response.encoding = response.apparent_encoding
            html = response.text[:200000]
            og = re.search(r"<meta[^>]+property=[\"']og:(?:site_name|title)[\"'][^>]+content=[\"']([^\"']+)[\"']", html, re.I)
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            raw = og.group(1) if og else title_match.group(1) if title_match else ""
            title = clean_name(re.sub(r"<[^>]+>", "", raw))
    except requests.RequestException:
        title = ""
    LP_TITLE_CACHE[url] = title
    return title


def split_lp_title(title: str) -> List[str]:
    parts = [clean_name(part) for part in re.split(r"\s*[|｜/／–—-]\s*", title) if clean_name(part)]
    return parts or ([title] if title else [])


def clean_company_candidate(value: Any, service_name: str = "", require_marker: bool = True) -> str:
    text = clean_name(value)
    text = re.sub(r"^(?:運営会社|会社名|事業者名|販売業者|提供会社|運営元|運営者)\s*[:：]?\s*", "", text)
    text = re.split(r"\s*(?:について|公式|会社概要|採用情報|求人|転職|口コミ|評判|なら|とは)\s*", text)[0]
    text = text.strip(" -_/｜|、。,.・")
    if not text or text == service_name:
        return ""
    if require_marker and not any(marker in text for marker in COMPANY_MARKERS):
        return ""
    if len(text) > 80:
        return ""
    return text


def extract_company_candidates(text: str, service_name: str = "") -> List[str]:
    source = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    source = re.sub(r"\s+", " ", source)
    patterns = [
        r"(?:運営会社|会社名|事業者名|販売業者|提供会社|運営元|運営者)\s*[:：]?\s*([^、。|｜\n\r]{2,80})",
        r"((?:株式会社|有限会社|合同会社|医療法人社団|医療法人|一般社団法人|学校法人)[^、。|｜\n\r]{1,60})",
        r"([^、。|｜\n\r]{1,60}(?:株式会社|有限会社|合同会社))",
    ]
    candidates: List[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, source):
            candidate = clean_company_candidate(match.group(1), service_name, require_marker=True)
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def search_company_by_service_name(service_name: str) -> str:
    service_name = clean_name(service_name)
    if not service_name:
        return ""
    if service_name in COMPANY_SEARCH_CACHE:
        return COMPANY_SEARCH_CACHE[service_name]
    queries = [
        f'"{service_name}" 運営会社',
        f'"{service_name}" 会社概要',
        f'"{service_name}" 特定商取引法',
    ]
    for query in queries:
        try:
            response = requests.get(
                f"https://duckduckgo.com/html/?q={quote_plus(query)}",
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome Safari"},
            )
        except requests.RequestException:
            continue
        if not response.ok:
            continue
        for candidate in extract_company_candidates(response.text[:200000], service_name):
            COMPANY_SEARCH_CACHE[service_name] = candidate
            log(f"会社名を検索で推定: {service_name} -> {candidate}")
            return candidate
    COMPANY_SEARCH_CACHE[service_name] = ""
    return ""


def infer_company_name(row: Dict[str, Any], lp_title: str, service_name: str = "", allow_search: bool = False) -> str:
    explicit = clean_name(first_value(row, ("会社名", "company_name", "advertiser_name")))
    if explicit and explicit != service_name and any(marker in explicit for marker in COMPANY_MARKERS):
        return explicit
    if allow_search:
        searched = search_company_by_service_name(service_name)
        if searched:
            return searched
    for candidate in extract_company_candidates(lp_title, service_name):
        return candidate
    return ""


def is_bad_service_name(value: str, company_name: str, row: Dict[str, Any]) -> bool:
    if not value:
        return True
    if value == company_name:
        return False
    category_values = {
        clean_name(first_value(row, ("検索名", "search_name"))),
        clean_name(first_value(row, ("ジャンル", "genre"))),
        clean_name(first_value(row, ("サブジャンル", "sub_genre"))),
    }
    return value in {item for item in category_values if item}


def infer_service_name(row: Dict[str, Any], company_name: str, lp_title: str) -> str:
    for key in ("広告表示名", "ad_display_name", "page_name", "サービス名", "service_name"):
        value = clean_name(row.get(key))
        if value and value != company_name and not is_bad_service_name(value, company_name, row):
            return value[:80]
    return ""


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "動画", "video", "カルーセル", "carousel"}


def is_video_row(row: Dict[str, Any]) -> bool:
    if parse_bool(first_value(row, ("is_video", "isVideo", "動画", "video"))):
        return True
    media_type = str(first_value(row, ("media_type", "mediaType", "メディア種別")) or "").lower()
    if "video" in media_type or "動画" in media_type:
        return True
    text = " ".join(str(row.get(key, "")) for key in ("広告本文", "ad_body", "広告タイトル", "ad_title", "raw_text"))
    return "動画を再生" in text or "Video player" in text


def is_carousel_row(row: Dict[str, Any]) -> bool:
    if parse_bool(first_value(row, ("is_carousel", "isCarousel", "カルーセル", "carousel"))):
        return True
    media_type = str(first_value(row, ("media_type", "mediaType", "メディア種別")) or "").lower()
    if "carousel" in media_type or "カルーセル" in media_type:
        return True
    text = " ".join(str(row.get(key, "")) for key in ("広告本文", "ad_body", "広告タイトル", "ad_title", "raw_text"))
    return bool(re.search(r"カルーセル|carousel|(?:カード|Card)\s*\d+\s*(?:\/|／|of)\s*\d+", text, flags=re.I))


def parse_duration_days(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value or "").replace(",", ""))
    return int(match.group(0)) if match else 0


def format_duration_display(days: Any) -> str:
    day_count = parse_duration_days(days)
    months = max(day_count // 30, 0)
    if day_count < 365:
        return f"{months}ヶ月"
    years = months // 12
    remaining_months = months % 12
    if remaining_months == 0:
        return f"{years}年"
    return f"{years}年{remaining_months}ヶ月"


def normalize_row(row: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    landing_url = lp_url(row)
    lp_title = fetch_lp_title(landing_url) if args.enrich_from_lp else ""
    explicit_company_name = clean_name(first_value(row, ("会社名", "company_name", "advertiser_name")))
    service_name = infer_service_name(row, explicit_company_name, lp_title)
    company_name = infer_company_name(row, lp_title, service_name, args.company_search)
    duration_days = parse_duration_days(first_value(row, ("掲載期間日数", "duration_days", "_durationDays", "掲載期間")))
    return {
        "search_name": str(first_value(row, ("検索名", "search_name")) or args.search_name),
        "genre": str(first_value(row, ("ジャンル", "genre")) or args.genre),
        "sub_genre": str(first_value(row, ("サブジャンル", "sub_genre")) or args.sub_genre),
        "page_name": company_name,
        "service_name": service_name,
        "start_date": str(first_value(row, ("掲載開始日", "start_date")) or ""),
        "duration_days": duration_days,
        "duration_display": format_duration_display(duration_days),
        "ad_snapshot_url": str(first_value(row, ("広告ライブラリURL", "ad_snapshot_url")) or ""),
        "lp_url": landing_url,
        "is_video": is_video_row(row),
        "is_carousel": is_carousel_row(row),
    }


def is_valid_candidate(row: Dict[str, Any]) -> bool:
    if not row["ad_snapshot_url"] or not row["start_date"] or row["duration_days"] in ("", None):
        return False
    if not row["service_name"]:
        return False
    if row.get("is_video"):
        return False
    if row.get("is_carousel"):
        return False
    try:
        host = urlparse(row["lp_url"]).hostname or ""
    except Exception:
        host = ""
    blocked_hosts = {"metastatus.com", "meta.com", "www.meta.com", "facebook.com", "www.facebook.com"}
    if host in blocked_hosts:
        return False
    return True


def is_min_duration_candidate(row: Dict[str, Any], min_duration_days: int) -> bool:
    return is_valid_candidate(row) and int(row["duration_days"] or 0) >= min_duration_days


def build_sheet_row(row: Dict[str, Any], headers: List[str]) -> List[Any]:
    values = {
        "検索名": row["search_name"],
        "分析状況": "未分析",
        "ジャンル": row["genre"],
        "サブジャンル": row["sub_genre"],
        "会社名": row["page_name"],
        "サービス名": row["service_name"],
        "掲載開始日": row["start_date"],
        "掲載期間": row["duration_display"],
        "広告ライブラリURL": row["ad_snapshot_url"],
        "LP URL": row["lp_url"],
        "広告スクショ": str(row.get("screenshot_url", "")),
        "広告分析": "",
        "コピー": "",
        "ビジネスモデル": "",
        "訴求の型": "",
        "X投稿URL": "",
        "分析日": "",
        "分析状況": "未分析",
        "状態": "掲載中",
        "最終掲載期間": "",
        "メモ": "",
    }
    return [values.get(header, "") for header in headers]


def append_visible_row(service: Any, spreadsheet_id: str, sheet_name: str, headers: List[str], row: Dict[str, Any]) -> None:
    append_values(
        service,
        spreadsheet_id,
        f"{quote_sheet_name(sheet_name)}!A:Z",
        [build_sheet_row(row, headers)],
        value_input_option="USER_ENTERED",
    )


def load_rows(path: str) -> List[Dict[str, Any]]:
    source = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(source)
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        data = data["rows"]
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise RuntimeError("入力JSONは [{...}] または {\"rows\": [{...}]} 形式にしてください。")
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="表示済みMeta広告カードの抽出JSONをGoogle Sheetsへ追記します。")
    parser.add_argument("--input-json", required=True, help="抽出JSONファイル。標準入力は - を指定。")
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET)
    parser.add_argument("--sheet-name", default=DEFAULT_SHEET)
    parser.add_argument("--search-name", default=DEFAULT_SEARCH_NAME)
    parser.add_argument("--genre", default=DEFAULT_GENRE)
    parser.add_argument("--sub-genre", default=DEFAULT_SUB_GENRE)
    parser.add_argument("--max-rows", type=int, default=5)
    parser.add_argument("--min-duration-days", type=int, default=90)
    parser.add_argument("--history-sheet", default=HISTORY_SHEET)
    parser.add_argument("--no-history-update", dest="history_update", action="store_false")
    parser.set_defaults(history_update=True)
    parser.add_argument("--enrich-from-lp", action="store_true", default=True)
    parser.add_argument("--no-enrich-from-lp", dest="enrich_from_lp", action="store_false")
    parser.add_argument("--company-search", action="store_true", default=True)
    parser.add_argument("--no-company-search", dest="company_search", action="store_false")
    parser.add_argument("--daily-genre-cap", type=int, default=2, help="分析中広告で同ジャンルを優先的に抑える目安。足りない時は自動で緩和します。")
    parser.add_argument("--daily-company-cap", type=int, default=1, help="分析中広告で同一会社を優先的に抑える目安。足りない時は自動で緩和します。")
    parser.add_argument("--daily-lp-cap", type=int, default=1, help="分析中広告で同一LPを優先的に抑える目安。足りない時は自動で緩和します。")
    parser.add_argument("--daily-service-cap", type=int, default=1, help="分析中広告で同一サービス名を優先的に抑える目安。足りない時は自動で緩和します。")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def existing_diversity_counts(rows: List[Dict[str, Any]]) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str]]:
    genre_counts: Counter[str] = Counter()
    company_counts: Counter[str] = Counter()
    lp_counts: Counter[str] = Counter()
    service_counts: Counter[str] = Counter()
    active_statuses = {"", "未分析", "分析中"}
    for row in rows:
        status = clean_name(row.get("ステータス") or row.get("分析状況"))
        if status not in active_statuses:
            continue
        if clean_name(row.get("広告分析")):
            continue
        genre = clean_name(row.get("ジャンル"))
        company = normalize_diversity_key(row.get("会社名"))
        service = normalize_diversity_key(row.get("サービス名"))
        lp_key = lp_diversity_key(row.get("LP URL"))
        if genre:
            genre_counts[genre] += 1
        if company:
            company_counts[company] += 1
        if service:
            service_counts[service] += 1
        if lp_key:
            lp_counts[lp_key] += 1
    return genre_counts, company_counts, lp_counts, service_counts


def diversify_candidate_rows(
    rows: List[Dict[str, Any]],
    existing_urls: set[str],
    existing_today_rows: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[List[Dict[str, Any]], List[tuple[str, str]]]:
    added: List[Dict[str, Any]] = []
    skipped: List[tuple[str, str]] = []
    seen_urls: set[str] = set()
    blocked_rows: List[Dict[str, Any]] = []
    genre_counts, company_counts, lp_counts, service_counts = existing_diversity_counts(existing_today_rows)

    def can_add(row: Dict[str, Any], enforce_genre: bool, enforce_company: bool, enforce_lp: bool, enforce_service: bool) -> tuple[bool, str]:
        genre = clean_name(row.get("genre"))
        company_key = normalize_diversity_key(row.get("page_name"))
        service_key = normalize_diversity_key(row.get("service_name"))
        lp_key = lp_diversity_key(row.get("lp_url"))
        if enforce_genre and args.daily_genre_cap > 0 and genre and genre_counts[genre] >= args.daily_genre_cap:
            return False, "ジャンル上限"
        if enforce_company and args.daily_company_cap > 0 and company_key and company_counts[company_key] >= args.daily_company_cap:
            return False, "会社重複回避"
        if enforce_lp and args.daily_lp_cap > 0 and lp_key and lp_counts[lp_key] >= args.daily_lp_cap:
            return False, "LP重複回避"
        if enforce_service and args.daily_service_cap > 0 and service_key and service_counts[service_key] >= args.daily_service_cap:
            return False, "サービス名重複回避"
        return True, ""

    for row in rows:
        url = row["ad_snapshot_url"]
        if url in seen_urls:
            skipped.append((url, "入力内重複"))
            continue
        seen_urls.add(url)
        if url in existing_urls:
            skipped.append((url, "既存行と重複"))
            continue
        blocked_rows.append(row)

    passes = [
        (True, True, True, True, "分散条件一致"),
        (False, True, True, True, "ジャンル上限を緩和"),
        (False, False, True, True, "会社上限を緩和"),
        (False, False, False, True, "LP上限を緩和"),
        (False, False, False, False, "サービス名上限を緩和"),
    ]
    selected_urls: set[str] = set()
    last_block_reason: Dict[str, str] = {}
    for enforce_genre, enforce_company, enforce_lp, enforce_service, pass_label in passes:
        for row in blocked_rows:
            url = row["ad_snapshot_url"]
            if url in selected_urls or len(added) >= args.max_rows:
                continue
            ok, reason = can_add(row, enforce_genre, enforce_company, enforce_lp, enforce_service)
            if not ok:
                last_block_reason[url] = reason
                continue
            selected_urls.add(url)
            added.append(row)
            genre = clean_name(row.get("genre"))
            company_key = normalize_diversity_key(row.get("page_name"))
            service_key = normalize_diversity_key(row.get("service_name"))
            lp_key = lp_diversity_key(row.get("lp_url"))
            if genre:
                genre_counts[genre] += 1
            if company_key:
                company_counts[company_key] += 1
            if service_key:
                service_counts[service_key] += 1
            if lp_key:
                lp_counts[lp_key] += 1
            if pass_label != "分散条件一致":
                log(f"分散条件を緩和して追加: {pass_label} / {row['page_name']} / {row['service_name']}")
        if len(added) >= args.max_rows:
            break

    for row in blocked_rows:
        url = row["ad_snapshot_url"]
        if url not in selected_urls:
            skipped.append((url, last_block_reason.get(url, "最大件数超過")))
    return added, skipped


def run_append(args: argparse.Namespace) -> int:
    source_rows = load_rows(args.input_json)
    video_count = sum(1 for row in source_rows if is_video_row(row))
    carousel_count = sum(1 for row in source_rows if is_carousel_row(row))
    rows = [normalize_row(row, args) for row in source_rows]
    before_quality = len(rows)
    valid_rows = [row for row in rows if is_valid_candidate(row)]
    rows = [row for row in valid_rows if int(row["duration_days"] or 0) >= args.min_duration_days]
    rows.sort(key=lambda row: int(row["duration_days"] or 0), reverse=True)
    log(f"入力候補: {before_quality}件 / 動画広告: {video_count}件 / カルーセル広告: {carousel_count}件 / 品質フィルタ後: {len(valid_rows)}件 / {args.min_duration_days}日以上: {len(rows)}件")

    config = load_sheets_config()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    config["spreadsheet_id"] = spreadsheet_id
    service = build_sheets_service(config)
    headers = ensure_sheet_headers(service, spreadsheet_id, args.sheet_name)
    sheet_name = args.sheet_name
    master_headers = read_sheet_headers(service, spreadsheet_id, MASTER_SHEET)
    existing = existing_ad_urls_across_sheets(
        service,
        spreadsheet_id,
        {
            sheet_name: headers,
            MASTER_SHEET: master_headers if "広告ライブラリURL" in master_headers else [],
        },
    )
    log(f"既存広告ライブラリURL: {len(existing)}件")

    existing_today_rows = read_sheet_rows(service, spreadsheet_id, sheet_name, headers)
    added, skipped = diversify_candidate_rows(rows, existing, existing_today_rows, args)
    for row in added:
        url = row["ad_snapshot_url"]
        log(f"追加候補: {row['page_name']} / {row['service_name']} / {row['start_date']} / {row['duration_display']}({row['duration_days']}日) / {url}")
        if not args.dry_run:
            append_visible_row(service, spreadsheet_id, sheet_name, headers, row)

    if args.dry_run:
        log("dry-runのためSheetsへ追記していません。")
    log(f"追加: {len(added)}件 / スキップ: {len(skipped)}件")
    for url, reason in skipped[:20]:
        log(f"スキップ: {reason} / {url}")
    if args.history_update and not args.dry_run:
        duplicate_skip_count = sum(1 for _url, reason in skipped if "重複" in reason)
        result = "成功" if rows else "ヒットなし"
        note = "" if added else ("90日以上の広告なし" if not rows else "90日以上広告はあるが新規追加なし")
        update_search_history(
            service,
            spreadsheet_id,
            args.search_name,
            args.genre,
            args.sub_genre,
            found_count=len(source_rows),
            hit_count_90_plus=len(rows),
            added_count=len(added),
            duplicate_skip_count=duplicate_skip_count,
            result=result,
            note=note,
            sheet_name=args.history_sheet,
        )
        log(f"検索履歴DB更新: {args.search_name} / {result} / 90日以上={len(rows)} / 追加={len(added)} / 重複={duplicate_skip_count}")
    return 0


def update_history_error(args: argparse.Namespace, error: Exception) -> None:
    if not getattr(args, "history_update", False) or getattr(args, "dry_run", False):
        return
    try:
        config = load_sheets_config()
        spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
        config["spreadsheet_id"] = spreadsheet_id
        service = build_sheets_service(config)
        update_search_history(
            service,
            spreadsheet_id,
            args.search_name,
            args.genre,
            args.sub_genre,
            found_count=0,
            hit_count_90_plus=0,
            added_count=0,
            duplicate_skip_count=0,
            result="エラー",
            note=str(error)[:500],
            sheet_name=args.history_sheet,
        )
        log(f"検索履歴DB更新: {args.search_name} / エラー")
    except Exception as history_error:
        log(f"検索履歴DBのエラー記録をスキップ: {history_error}")


def main() -> int:
    args = parse_args()
    try:
        return run_append(args)
    except Exception as error:
        update_history_error(args, error)
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nキャンセルしました。", file=sys.stderr)
        raise SystemExit(130)
    except HttpError as error:
        print(f"\nGoogle Sheets APIエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
