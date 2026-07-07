import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sheets_api import build_sheets_service, load_sheets_config, read_values
from scripts.append_meta_visible_rows_to_sheet import DEFAULT_SPREADSHEET, ensure_sheet_headers, parse_spreadsheet_id, quote_sheet_name
from scripts.keyword_db import (
    KEYWORD_SHEET,
    append_keyword,
    candidate_keyword_records_from_ad,
    infer_genre_for_keyword,
    keyword_exists_semantically,
    load_keyword_set,
    load_keywords,
)
from scripts.search_history import HISTORY_SHEET, explain_term_selection, is_focus_genre, load_history, select_daily_research_terms, update_search_history


TODAY_SHEET = "今日の広告DB"
MASTER_SHEET = "広告分析マスターDB"
DEFAULT_DAILY_TARGET = 2
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_PER_SEARCH_MAX = 5


def log(message: str) -> None:
    print(f"[collect-meta-ads] {message}", flush=True)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def read_rows(service: Any, spreadsheet_id: str, sheet_name: str) -> tuple[List[str], List[Dict[str, Any]]]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:Z")
    if not values:
        return [], []
    headers = [clean(value) for value in values[0]]
    rows = []
    for row_number, row in enumerate(values[1:], start=2):
        item = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        item["_row_number"] = row_number
        rows.append(item)
    return headers, rows


def ad_url_set(rows: List[Dict[str, Any]]) -> Set[str]:
    return {clean(row.get("広告ライブラリURL")) for row in rows if clean(row.get("広告ライブラリURL"))}


def count_open_today_ads(rows: List[Dict[str, Any]]) -> int:
    active_statuses = {"", "未分析", "Notion投入済み"}
    count = 0
    for row in rows:
        status = clean(row.get("ステータス") or row.get("分析状況"))
        if clean(row.get("広告ライブラリURL")) and status in active_statuses and not clean(row.get("広告分析")):
            count += 1
    return count


def run_browser_collect(args: argparse.Namespace, term: Dict[str, str], output_path: Path, per_search_max: int) -> None:
    cmd = [
        sys.executable,
        "scripts/meta_ad_library_playwright_collect.py",
        "--search-name",
        term["search_name"],
        "--output-json",
        str(output_path),
        "--extractor",
        "scripts/meta_ad_library_visible_cards_extractor.js",
        "--scrolls",
        str(args.scrolls),
        "--min-duration-days",
        str(args.min_duration_days),
        "--max-rows",
        str(max(per_search_max * 3, per_search_max)),
    ]
    if not args.headless:
        cmd.append("--no-headless")
    if args.chrome_executable:
        cmd.extend(["--chrome-executable", args.chrome_executable])
    subprocess.run(cmd, cwd=ROOT_DIR, check=True)


def browser_runtime_available(args: argparse.Namespace) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import playwright; print('ok')"],
            cwd=ROOT_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        return False, str(error)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, ""


def append_results(args: argparse.Namespace, term: Dict[str, str], output_path: Path, max_rows: int) -> None:
    cmd = [
        sys.executable,
        "scripts/append_meta_visible_rows_to_sheet.py",
        "--input-json",
        str(output_path),
        "--spreadsheet",
        args.spreadsheet,
        "--sheet-name",
        args.today_sheet,
        "--search-name",
        term["search_name"],
        "--genre",
        term["genre"],
        "--sub-genre",
        term.get("sub_genre") or term["search_name"],
        "--max-rows",
        str(max_rows),
        "--min-duration-days",
        str(args.min_duration_days),
        "--history-sheet",
        args.history_sheet,
        "--daily-genre-cap",
        str(args.daily_genre_cap),
        "--daily-company-cap",
        str(args.daily_company_cap),
        "--daily-lp-cap",
        str(args.daily_lp_cap),
        "--daily-service-cap",
        str(args.daily_service_cap),
    ]
    subprocess.run(cmd, cwd=ROOT_DIR, check=True)


def per_term_add_limit(args: argparse.Namespace, missing_count: int, total_added: int) -> int:
    remaining = max(missing_count - total_added, 0)
    if args.daily_target <= 2:
        return min(1, args.per_search_max, remaining)
    return min(args.per_search_max, remaining)


def add_keyword_candidates(
    service: Any,
    spreadsheet_id: str,
    rows: List[Dict[str, Any]],
    history_keys: Set[str],
    keyword_sheet: str,
) -> List[str]:
    added: List[str] = []
    keyword_keys = load_keyword_set(service, spreadsheet_id, keyword_sheet)
    existing_keywords = load_keywords(service, spreadsheet_id, keyword_sheet)
    for row in rows:
        genre = clean(row.get("ジャンル"))
        for record in candidate_keyword_records_from_ad(row):
            candidate = clean(record.get("keyword"))
            key = candidate.casefold()
            if key in keyword_keys or key in history_keys:
                continue
            if keyword_exists_semantically(candidate, existing_keywords, history_keys):
                continue
            inferred_genre = clean(record.get("genre")) or infer_genre_for_keyword(candidate, genre)
            keyword_type = clean(record.get("type")) or "検索ワード"
            if append_keyword(
                service,
                spreadsheet_id,
                candidate,
                inferred_genre,
                keyword_sheet,
                keyword_type=keyword_type,
                memo="広告本文/コピー/LPから自動抽出",
            ):
                keyword_keys.add(key)
                existing_keywords.append({"search_name": candidate, "genre": inferred_genre, "sub_genre": candidate})
                added.append(f"{candidate}（{inferred_genre or '要確認'} / {keyword_type}）")
    return added


def genre_counts_for_rows(rows: List[Dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        genre = clean(row.get("ジャンル"))
        if genre:
            counts[genre] += 1
    return counts


def duplicate_companies_for_rows(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        company = clean(row.get("会社名"))
        if company:
            counts[company] += 1
    return {company: count for company, count in counts.items() if count > 1}


def log_final_summary(
    collected_count: int,
    added_count: int,
    history_updates: int,
    keyword_added_count: int,
) -> None:
    log(f"広告収集件数: {collected_count}件")
    log(f"今日の広告DBへ追加した件数: {added_count}件")
    log(f"検索履歴更新件数: {history_updates}件")
    log(f"キーワードDBへ追加した件数: {keyword_added_count}件")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="キーワードDB/検索履歴DBを使って今日の広告DBへMeta広告候補を補充します。")
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET)
    parser.add_argument("--keyword-sheet", default=os.getenv("KEYWORD_DB_SHEET", KEYWORD_SHEET))
    parser.add_argument("--history-sheet", default=os.getenv("SEARCH_HISTORY_SHEET", HISTORY_SHEET))
    parser.add_argument("--today-sheet", default=os.getenv("TODAY_AD_DB_SHEET", TODAY_SHEET))
    parser.add_argument("--master-sheet", default=os.getenv("AD_ANALYSIS_MASTER_SHEET", MASTER_SHEET))
    parser.add_argument("--daily-target", type=int, default=int(os.getenv("DAILY_AD_TARGET", DEFAULT_DAILY_TARGET)))
    parser.add_argument("--search-limit", type=int, default=int(os.getenv("DAILY_SEARCH_LIMIT", DEFAULT_SEARCH_LIMIT)))
    parser.add_argument("--per-search-max", type=int, default=int(os.getenv("PER_SEARCH_AD_MAX", DEFAULT_PER_SEARCH_MAX)))
    parser.add_argument("--daily-genre-cap", type=int, default=int(os.getenv("DAILY_AD_GENRE_CAP", "2")))
    parser.add_argument("--daily-company-cap", type=int, default=int(os.getenv("DAILY_AD_COMPANY_CAP", "1")))
    parser.add_argument("--daily-lp-cap", type=int, default=int(os.getenv("DAILY_AD_LP_CAP", "1")))
    parser.add_argument("--daily-service-cap", type=int, default=int(os.getenv("DAILY_AD_SERVICE_CAP", "1")))
    parser.add_argument("--min-duration-days", type=int, default=90)
    parser.add_argument("--cooldown-hours", type=int, default=24)
    parser.add_argument("--scrolls", type=int, default=10)
    parser.add_argument("--chrome-executable", default="")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--work-dir", default="/private/tmp/meta_ad_collect")
    return parser.parse_args()


def main() -> int:
    load_dotenv(dotenv_path=".env", override=True)
    args = parse_args()
    spreadsheet_id = parse_spreadsheet_id(args.spreadsheet)
    config = load_sheets_config()
    service = build_sheets_service(config)
    ensure_sheet_headers(service, spreadsheet_id, args.today_sheet)

    _today_headers, today_rows = read_rows(service, spreadsheet_id, args.today_sheet)
    _master_headers, master_rows = read_rows(service, spreadsheet_id, args.master_sheet)
    open_count = count_open_today_ads(today_rows)
    missing_count = max(args.daily_target - open_count, 0)
    log(f"今日の広告DB 未分析残: {open_count}件 / 目標: {args.daily_target}件 / 新規必要: {missing_count}件")
    if missing_count <= 0:
        log("今日の広告DBが目標件数に達しているため、新規収集はスキップします。")
        log_final_summary(0, 0, 0, 0)
        return 0

    terms = load_keywords(service, spreadsheet_id, args.keyword_sheet)
    history = load_history(service, spreadsheet_id, args.history_sheet)
    selected_terms = select_daily_research_terms(terms, history, args.search_limit, args.cooldown_hours)
    log("今回選んだ検索名: " + ", ".join(f"{term['search_name']}（{term.get('genre') or '未分類'}）" for term in selected_terms))
    if args.daily_target <= 2:
        focus_selected = [term for term in selected_terms if is_focus_genre(term.get("genre"))]
        other_selected = [term for term in selected_terms if not is_focus_genre(term.get("genre"))]
        log(
            "日次2広告の配分: "
            f"重点6ジャンル={focus_selected[0]['search_name'] if focus_selected else '候補なし'} / "
            f"その他ジャンル={other_selected[0]['search_name'] if other_selected else '候補なし'}"
        )
    skipped_explanations = explain_term_selection(terms, history, selected_terms)
    if skipped_explanations:
        log("検索しなかった理由:")
        for item in skipped_explanations[:30]:
            log(f"- {item['search_name']}（{item.get('genre') or '未分類'}）: {item['reason']}")
        if len(skipped_explanations) > 30:
            log(f"- 他 {len(skipped_explanations) - 30}件")
    if args.dry_run or args.skip_browser:
        log("dry-run/skip-browser のためMeta広告ライブラリ検索とDB追記は行いません。")
        log_final_summary(0, 0, 0, 0)
        return 0
    available, runtime_error = browser_runtime_available(args)
    if not available:
        log(f"ブラウザ自動検索ランタイムが使えないため停止します: {runtime_error}")
        log("検索履歴DBは更新していません。Playwrightを導入するか、Codexブラウザ上の表示済みカード抽出で実行してください。")
        log_final_summary(0, 0, 0, 0)
        return 2

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    existing_before = ad_url_set(today_rows) | ad_url_set(master_rows)
    new_keyword_candidates: List[str] = []
    total_added = 0
    failures = 0
    summary = []

    for term in selected_terms:
        if total_added >= missing_count:
            break
        output_path = work_dir / f"{re.sub(r'[^0-9A-Za-zぁ-んァ-ン一-龥_-]+', '_', term['search_name'])}.json"
        before_headers, before_rows = read_rows(service, spreadsheet_id, args.today_sheet)
        before_urls = ad_url_set(before_rows)
        try:
            add_limit = per_term_add_limit(args, missing_count, total_added)
            run_browser_collect(args, term, output_path, add_limit)
            rows = json.loads(output_path.read_text(encoding="utf-8"))
            hit_count = len(rows)
            append_results(args, term, output_path, add_limit)
            _after_headers, after_rows = read_rows(service, spreadsheet_id, args.today_sheet)
            after_urls = ad_url_set(after_rows)
            added_urls = after_urls - before_urls
            added_count = len(added_urls)
            duplicate_count = max(hit_count - added_count, 0)
            total_added += added_count
            added_rows = [row for row in after_rows if clean(row.get("広告ライブラリURL")) in added_urls]
            new_keyword_candidates.extend(
                add_keyword_candidates(service, spreadsheet_id, added_rows, set(history.keys()), args.keyword_sheet)
            )
            updated_history = load_history(service, spreadsheet_id, args.history_sheet)
            updated_record = updated_history.get(clean(term["search_name"]).casefold(), {})
            summary.append(
                {
                    "search_name": term["search_name"],
                    "hit_count": hit_count,
                    "added_count": added_count,
                    "duplicate_count": duplicate_count,
                    "failure_count": 0,
                    "priority": clean(updated_record.get("優先度")),
                    "next_search_date": clean(updated_record.get("次回検索予定日")),
                }
            )
        except Exception as error:
            failures += 1
            update_search_history(
                service,
                spreadsheet_id,
                term["search_name"],
                term["genre"],
                term.get("sub_genre") or term["search_name"],
                result="エラー",
                note=str(error)[:500],
                sheet_name=args.history_sheet,
            )
            updated_history = load_history(service, spreadsheet_id, args.history_sheet)
            updated_record = updated_history.get(clean(term["search_name"]).casefold(), {})
            summary.append(
                {
                    "search_name": term["search_name"],
                    "hit_count": 0,
                    "added_count": 0,
                    "duplicate_count": 0,
                    "failure_count": 1,
                    "priority": clean(updated_record.get("優先度")),
                    "next_search_date": clean(updated_record.get("次回検索予定日")),
                }
            )
            log(f"エラー: {term['search_name']} / {error}")

    _final_headers, final_today_rows = read_rows(service, spreadsheet_id, args.today_sheet)
    log("検索別結果:")
    for item in summary:
        log(
            f"- {item['search_name']}: 90日以上候補={item['hit_count']} / 新規追加={item['added_count']} / "
            f"重複スキップ={item['duplicate_count']} / 失敗={item['failure_count']} / "
            f"優先度={item['priority'] or '-'} / 次回検索予定日={item['next_search_date'] or '-'}"
        )
    log(f"今日の広告DB 未分析残: {count_open_today_ads(final_today_rows)}件")
    added_final_rows = [row for row in final_today_rows if clean(row.get("広告ライブラリURL")) not in existing_before]
    genre_counts = genre_counts_for_rows(added_final_rows)
    duplicate_companies = duplicate_companies_for_rows(added_final_rows)
    log("ジャンル別の追加件数: " + (", ".join(f"{genre}={count}" for genre, count in genre_counts.items()) if genre_counts else "なし"))
    log("同一会社の重複: " + (", ".join(f"{company}={count}" for company, count in duplicate_companies.items()) if duplicate_companies else "なし"))
    log(f"キーワードDBへ新規追加: {', '.join(new_keyword_candidates) if new_keyword_candidates else 'なし'}")
    log("ジャンル候補として検出: なし")
    log("掲載停止チェック: 毎日の広告収集では実行しません。月1回「停止リサーチして」で実行します。")
    log_final_summary(sum(int(item["hit_count"]) for item in summary), total_added, len(summary), len(new_keyword_candidates))
    log(f"合計新規追加: {total_added}件 / 失敗検索数: {failures}件")
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
