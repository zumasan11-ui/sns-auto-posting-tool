from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sheets_api import append_values, read_values, update_values


HISTORY_SHEET = "検索履歴DB"
HISTORY_HEADERS = [
    "検索名",
    "ジャンル",
    "サブジャンル",
    "最終検索日",
    "使用回数",
    "発見広告数",
    "90日以上候補数",
    "勝ち広告率",
    "新規追加数",
    "重複スキップ数",
    "失敗回数",
    "状態",
    "優先度",
    "メモ",
    "最終追加日",
    "最終ヒット日",
    "次回検索予定日",
]
FOCUS_GENRES = {"人材・転職", "情報商材・スクール", "美容・美容医療", "D2C・通販", "SaaS・BtoB", "金融"}
ACTIVE_STATUS = "有効"
PAUSED_STATUS = "一時停止"
EXCLUDED_STATUS = "除外"
PRIORITY_SCORES = {"高": 300, "中": 150, "低": 0, "除外": -10000, "": 150}


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Tokyo")).replace(hour=0, minute=0, second=0, microsecond=0)


def today_text() -> str:
    return today().strftime("%Y-%m-%d")


def clean(value: Any) -> str:
    return str(value or "").strip()


def to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except ValueError:
        return 0


def parse_datetime(value: Any) -> Optional[datetime]:
    text = clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def parse_date(value: Any) -> Optional[datetime]:
    parsed = parse_datetime(value)
    if parsed:
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def date_sort_value(value: Any, empty_value: str = "0000-00-00") -> str:
    parsed = parse_date(value)
    if not parsed:
        return empty_value
    return parsed.strftime("%Y-%m-%d")


def days_since(value: Any, missing_value: int = 3650) -> int:
    parsed = parse_date(value)
    if not parsed:
        return missing_value
    return max((today().date() - parsed.date()).days, 0)


def ensure_history_sheet(service: Any, spreadsheet_id: str, sheet_name: str = HISTORY_SHEET) -> List[str]:
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [sheet.get("properties", {}).get("title", "") for sheet in spreadsheet.get("sheets", [])]
    if sheet_name not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        ).execute()
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:ZZ1")
    headers = values[0] if values else []
    if not headers:
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:{column_letter(len(HISTORY_HEADERS) - 1)}1", [HISTORY_HEADERS])
        return HISTORY_HEADERS
    updated = list(headers)
    for header in HISTORY_HEADERS:
        if header not in updated:
            updated.append(header)
    if updated != headers:
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:{column_letter(len(updated) - 1)}1", [updated])
    return updated


def column_letter(index: int) -> str:
    index += 1
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def load_history(service: Any, spreadsheet_id: str, sheet_name: str = HISTORY_SHEET) -> Dict[str, Dict[str, Any]]:
    headers = ensure_history_sheet(service, spreadsheet_id, sheet_name)
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A2:{column_letter(len(headers) - 1)}")
    history: Dict[str, Dict[str, Any]] = {}
    for row_number, row in enumerate(values, start=2):
        if not row:
            continue
        record = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        search_name = clean(record.get("検索名"))
        if not search_name:
            continue
        record["_row_number"] = row_number
        history[search_name.casefold()] = record
    return history


def infer_next_status_priority(
    existing: Dict[str, Any],
    hit_count_90_plus: int,
    added_count: int,
    duplicate_skip_count: int,
    failed: bool,
) -> tuple[str, str]:
    status = clean(existing.get("状態")) or ACTIVE_STATUS
    priority = clean(existing.get("優先度")) or "中"
    failure_count = to_int(existing.get("失敗回数")) + (1 if failed else 0)
    total_added = to_int(existing.get("新規追加数")) + added_count
    total_duplicates = to_int(existing.get("重複スキップ数")) + duplicate_skip_count

    if status == EXCLUDED_STATUS or priority == "除外":
        return EXCLUDED_STATUS, "除外"
    if failed and failure_count >= 3:
        return PAUSED_STATUS, "低"
    if failed:
        return ACTIVE_STATUS, "低"
    if added_count >= 5:
        return ACTIVE_STATUS, "高"
    if added_count > 0:
        return ACTIVE_STATUS, "中"
    return ACTIVE_STATUS, "低"


def next_search_date(result: str, hit_count_90_plus: int, added_count: int) -> str:
    base = today()
    if result == "エラー":
        return (base + timedelta(days=30)).strftime("%Y-%m-%d")
    if added_count >= 5:
        return (base + timedelta(days=7)).strftime("%Y-%m-%d")
    if added_count >= 1:
        return (base + timedelta(days=30)).strftime("%Y-%m-%d")
    if hit_count_90_plus >= 1:
        return (base + timedelta(days=90)).strftime("%Y-%m-%d")
    return (base + timedelta(days=180)).strftime("%Y-%m-%d")


def history_score(term: Dict[str, str], record: Optional[Dict[str, Any]], cooldown_hours: int = 24) -> float:
    if not record:
        return 10_000
    status = clean(record.get("状態")) or ACTIVE_STATUS
    priority = clean(record.get("優先度")) or "中"
    if status in {EXCLUDED_STATUS, PAUSED_STATUS} or priority == "除外":
        return -100_000
    scheduled = parse_date(record.get("次回検索予定日"))
    if scheduled and scheduled > today():
        return -100_000

    days_since_search = days_since(record.get("最終検索日"))
    days_since_hit = days_since(record.get("最終ヒット日"))
    use_count = to_int(record.get("使用回数"))
    found_count = to_int(record.get("発見広告数"))
    hit_count = to_int(record.get("90日以上候補数"))
    added_count = to_int(record.get("新規追加数"))
    duplicate_count = to_int(record.get("重複スキップ数"))
    failure_count = to_int(record.get("失敗回数"))
    priority_bonus = PRIORITY_SCORES.get(priority, 100)
    try:
        winning_rate = float(str(record.get("勝ち広告率") or "0").replace("%", "")) / (100 if "%" in str(record.get("勝ち広告率")) else 1)
    except ValueError:
        winning_rate = hit_count / found_count if found_count else 0
    focus_bonus = 80 if clean(term.get("genre")) in FOCUS_GENRES else 0
    hit_rate_bonus = min(added_count * 12, 240) + min(hit_count * 2, 120) + min(found_count, 100) + winning_rate * 180 + focus_bonus
    freshness_bonus = min(days_since_search, 365) * 1.5 + min(days_since_hit, 365) * 0.8
    fatigue_penalty = use_count * 8 + duplicate_count * 5 + failure_count * 80
    return priority_bonus + hit_rate_bonus + freshness_bonus - fatigue_penalty


def sort_tuple(term: Dict[str, str], record: Optional[Dict[str, Any]]) -> tuple:
    if not record:
        return (0, 0, -10_000, clean(term.get("genre")), clean(term.get("search_name")))
    scheduled = parse_date(record.get("次回検索予定日"))
    last_search = parse_date(record.get("最終検索日"))
    return (
        1 if scheduled else 0,
        1 if last_search else 0,
        -history_score(term, record),
        clean(term.get("genre")),
        clean(term.get("search_name")),
    )


def exclusion_reason(term: Dict[str, str], record: Optional[Dict[str, Any]]) -> str:
    if not record:
        return ""
    status = clean(record.get("状態")) or ACTIVE_STATUS
    priority = clean(record.get("優先度")) or "中"
    if status in {PAUSED_STATUS, EXCLUDED_STATUS}:
        return f"状態={status}"
    if status != ACTIVE_STATUS:
        return f"状態が有効ではない: {status}"
    if priority == "除外":
        return "優先度=除外"
    scheduled = parse_date(record.get("次回検索予定日"))
    if scheduled and scheduled > today():
        return f"次回検索予定日が未来: {scheduled.strftime('%Y-%m-%d')}"
    return ""


def explain_term_selection(
    terms: List[Dict[str, str]],
    history: Dict[str, Dict[str, Any]],
    selected_terms: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    selected_keys = {clean(term.get("search_name")).casefold() for term in selected_terms}
    optimized_keys = {clean(term.get("search_name")).casefold() for term in optimize_terms(terms, history)}
    explanations: List[Dict[str, str]] = []
    for term in terms:
        key = clean(term.get("search_name")).casefold()
        if key in selected_keys:
            continue
        record = history.get(key)
        reason = exclusion_reason(term, record)
        if not reason and key in optimized_keys:
            reason = "検索件数上限のため今回は後回し"
        if reason:
            explanations.append(
                {
                    "search_name": clean(term.get("search_name")),
                    "genre": clean(term.get("genre")),
                    "reason": reason,
                }
            )
    return explanations


def diversify_terms(
    terms: List[Dict[str, str]],
    history: Dict[str, Dict[str, Any]],
    max_per_genre: int = 2,
) -> List[Dict[str, str]]:
    buckets: Dict[str, List[Dict[str, str]]] = {}
    for term in terms:
        genre = clean(term.get("genre")) or "未分類"
        buckets.setdefault(genre, []).append(term)

    for genre, items in buckets.items():
        buckets[genre] = sorted(
            items,
            key=lambda term: sort_tuple(term, history.get(clean(term.get("search_name")).casefold())),
        )

    diversified: List[Dict[str, str]] = []
    used_keys = set()
    round_index = 0
    while len(used_keys) < len(terms):
        progressed = False
        genres = sorted(
            buckets,
            key=lambda genre: (
                sum(1 for term in diversified if clean(term.get("genre")) == genre),
                sort_tuple(buckets[genre][0], history.get(clean(buckets[genre][0].get("search_name")).casefold())) if buckets[genre] else (9,),
            ),
        )
        for genre in genres:
            picked_in_genre = sum(1 for term in diversified if clean(term.get("genre")) == genre)
            if round_index == 0 and picked_in_genre >= 1:
                continue
            if round_index == 1 and picked_in_genre >= max_per_genre:
                continue
            while buckets[genre]:
                term = buckets[genre].pop(0)
                key = clean(term.get("search_name")).casefold()
                if key in used_keys:
                    continue
                diversified.append(term)
                used_keys.add(key)
                progressed = True
                break
        if not progressed:
            round_index += 1
            if round_index > 2:
                for items in buckets.values():
                    for term in items:
                        key = clean(term.get("search_name")).casefold()
                        if key not in used_keys:
                            diversified.append(term)
                            used_keys.add(key)
                break
    return diversified


def optimize_terms(
    terms: List[Dict[str, str]],
    history: Dict[str, Dict[str, Any]],
    cooldown_hours: int = 24,
) -> List[Dict[str, str]]:
    filtered = []
    for term in terms:
        record = history.get(clean(term.get("search_name")).casefold())
        if exclusion_reason(term, record):
            continue
        filtered.append(term)
    ranked = sorted(
        filtered,
        key=lambda term: sort_tuple(term, history.get(clean(term.get("search_name")).casefold())),
    )
    return diversify_terms(ranked, history)


def is_focus_genre(genre: Any) -> bool:
    return clean(genre) in FOCUS_GENRES


def select_daily_research_terms(
    terms: List[Dict[str, str]],
    history: Dict[str, Dict[str, Any]],
    search_limit: int = 2,
    cooldown_hours: int = 24,
) -> List[Dict[str, str]]:
    """Pick daily terms with one focus-genre slot and one other-genre slot first."""
    optimized = optimize_terms(terms, history, cooldown_hours)
    if search_limit <= 0:
        return []
    selected: List[Dict[str, str]] = []
    used: set[str] = set()

    def add_first(predicate: Any) -> None:
        if len(selected) >= search_limit:
            return
        for term in optimized:
            key = clean(term.get("search_name")).casefold()
            if key in used:
                continue
            if predicate(term):
                selected.append(term)
                used.add(key)
                return

    if search_limit >= 2:
        add_first(lambda term: is_focus_genre(term.get("genre")))
        add_first(lambda term: not is_focus_genre(term.get("genre")))

    for term in optimized:
        if len(selected) >= search_limit:
            break
        key = clean(term.get("search_name")).casefold()
        if key in used:
            continue
        selected.append(term)
        used.add(key)
    return selected


def update_search_history(
    service: Any,
    spreadsheet_id: str,
    search_name: str,
    genre: str = "",
    sub_genre: str = "",
    found_count: int = 0,
    hit_count_90_plus: int = 0,
    added_count: int = 0,
    duplicate_skip_count: int = 0,
    result: str = "成功",
    note: str = "",
    sheet_name: str = HISTORY_SHEET,
) -> None:
    history = load_history(service, spreadsheet_id, sheet_name)
    headers = ensure_history_sheet(service, spreadsheet_id, sheet_name)
    key = clean(search_name).casefold()
    existing = history.get(key, {})
    failed = result == "エラー"
    status, priority = infer_next_status_priority(existing, hit_count_90_plus, added_count, duplicate_skip_count, failed)
    use_count = to_int(existing.get("使用回数")) + (0 if failed else 1)
    failure_count = to_int(existing.get("失敗回数")) + (1 if failed else 0)
    total_found = to_int(existing.get("発見広告数")) + int(found_count or hit_count_90_plus)
    total_90_plus = to_int(existing.get("90日以上候補数")) + int(hit_count_90_plus)
    winning_rate = total_90_plus / total_found if total_found else 0
    last_added = today_text() if added_count > 0 else clean(existing.get("最終追加日"))
    last_hit = today_text() if added_count > 0 else clean(existing.get("最終ヒット日"))
    row_map = dict(existing)
    row_map.update(
        {
            "検索名": clean(search_name),
            "ジャンル": clean(genre) or clean(existing.get("ジャンル")),
            "サブジャンル": clean(sub_genre) or clean(existing.get("サブジャンル")) or clean(search_name),
            "最終検索日": today_text(),
            "使用回数": use_count,
            "発見広告数": total_found,
            "90日以上候補数": total_90_plus,
            "勝ち広告率": round(winning_rate, 4),
            "新規追加数": to_int(existing.get("新規追加数")) + int(added_count),
            "重複スキップ数": to_int(existing.get("重複スキップ数")) + int(duplicate_skip_count),
            "失敗回数": failure_count,
            "状態": status,
            "優先度": priority,
            "メモ": note,
            "最終追加日": last_added,
            "最終ヒット日": last_hit,
            "次回検索予定日": next_search_date(result, hit_count_90_plus, added_count),
        }
    )
    row = [row_map.get(header, "") for header in headers]
    if existing.get("_row_number"):
        row_number = int(existing["_row_number"])
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A{row_number}:{column_letter(len(headers) - 1)}{row_number}", [row])
    else:
        append_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A:{column_letter(len(headers) - 1)}", [row])
