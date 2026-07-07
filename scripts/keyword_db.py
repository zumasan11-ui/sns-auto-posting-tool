from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sheets_api import append_values, read_values, update_values


KEYWORD_SHEET = "キーワードDB"
VERTICAL_KEYWORD_HEADERS = [
    "検索名",
    "ジャンル",
    "サブジャンル",
    "種別",
    "優先度",
    "状態",
    "追加日",
    "最終更新日",
    "メモ",
]
ACTIVE_STATUS = "有効"
DEFAULT_PRIORITY = "中"
BROAD_TERMS = {
    "AI",
    "美容",
    "副業",
    "DX",
    "転職",
    "通販",
    "SaaS",
    "マーケ",
    "広告",
    "金融",
    "医療",
    "介護",
    "旅行",
    "飲食",
}
LOW_VALUE_TERMS = {
    "安心",
    "人気",
    "おすすめ",
    "限定",
    "今だけ",
    "無料",
    "公式",
    "キャンペーン",
    "ランキング",
    "プレゼント",
    "詳しくはこちら",
    "お申し込み",
    "お問い合わせ",
    "今すぐ",
    "簡単",
}
SEARCH_VALUE_COMPOUNDS = (
    "無料相談",
    "無料カウンセリング",
    "資料請求",
    "一括査定",
    "無料診断",
    "無料体験",
    "無料見積",
    "無料見積もり",
    "オンライン相談",
    "オンライン診療",
    "無料セミナー",
    "個別相談",
)
GENRE_SEARCH_TERMS = {
    "人材・転職": (
        "転職エージェント",
        "転職サイト",
        "求人サイト",
        "看護師転職",
        "薬剤師転職",
        "医師転職",
        "介護転職",
        "IT転職",
        "未経験転職",
        "ハイクラス転職",
        "年収アップ",
        "キャリア相談",
        "スカウト転職",
    ),
    "情報商材・スクール": (
        "AI副業",
        "生成AI講座",
        "Webマーケティング講座",
        "動画編集スクール",
        "プログラミングスクール",
        "Webデザインスクール",
        "SNS運用代行",
        "起業塾",
        "副業スクール",
        "オンラインサロン",
        "フリーランス養成講座",
    ),
    "美容・美容医療": (
        "医療脱毛",
        "メンズ脱毛",
        "ポテンツァ",
        "ダーマペン",
        "クマ取り",
        "二重整形",
        "毛穴ケア",
        "ニキビ跡治療",
        "シミ取り",
        "小顔施術",
        "AGA治療",
        "FAGA治療",
        "脂肪吸引",
        "ホワイトニング",
    ),
    "D2C・通販": (
        "プロテイン",
        "ダイエットサプリ",
        "睡眠サプリ",
        "美容サプリ",
        "酵素ドリンク",
        "青汁",
        "白髪ケア",
        "育毛剤",
        "シャンプー",
        "スキンケア",
        "健康食品",
        "サブスク",
    ),
    "SaaS・BtoB": (
        "勤怠管理",
        "電子契約",
        "経費精算",
        "インボイス対応",
        "確定申告ソフト",
        "MAツール",
        "SFA",
        "CRM",
        "AI議事録",
        "AIチャットボット",
        "請求書管理",
        "プロジェクト管理",
        "人事DX",
        "営業支援ツール",
    ),
    "金融": (
        "クレジットカード",
        "カードローン",
        "消費者金融",
        "住宅ローン",
        "不動産投資",
        "投資スクール",
        "NISA",
        "iDeCo",
        "証券口座",
        "FX",
        "資産運用",
        "保険相談",
    ),
    "不動産": (
        "不動産投資",
        "ワンルーム投資",
        "マンション経営",
        "不動産売却",
        "売却査定",
        "投資用マンション",
        "賃貸",
    ),
    "住宅": (
        "注文住宅",
        "建売住宅",
        "リフォーム",
        "リノベーション",
        "外壁塗装",
        "屋根修理",
        "蓄電池",
        "太陽光発電",
        "V2H",
        "オール電化",
    ),
    "自動車": (
        "中古車",
        "新車",
        "カーリース",
        "車買取",
        "車査定",
        "タイヤ",
        "バイク買取",
        "電動自転車",
    ),
    "教育": (
        "学習塾",
        "個別指導",
        "オンライン学習",
        "通信講座",
        "資格取得",
        "英会話",
        "大学受験",
        "高校受験",
        "中学受験",
        "プログラミング教育",
    ),
    "ペット": (
        "ドッグフード",
        "キャットフード",
        "ペット保険",
        "ペットサプリ",
        "ペット用品",
        "トリミング",
    ),
    "通信": (
        "格安SIM",
        "光回線",
        "ホームルーター",
        "ポケットWiFi",
        "スマホ乗り換え",
    ),
    "インフラ": (
        "電力",
        "電気代",
        "ガス",
        "ウォーターサーバー",
    ),
    "飲食": (
        "宅配",
        "デリバリー",
        "ミールキット",
        "冷凍食品",
        "宅食",
        "おせち",
        "ふるさと納税",
    ),
    "ブライダル": (
        "結婚相談所",
        "婚活アプリ",
        "マッチングアプリ",
        "フォトウェディング",
        "結婚式場",
    ),
    "旅行": (
        "ホテル",
        "旅館",
        "航空券",
        "海外旅行",
        "国内旅行",
        "ツアー",
        "温泉",
        "レンタカー",
    ),
    "士業": (
        "税理士",
        "会計事務所",
        "弁護士",
        "司法書士",
        "行政書士",
        "社労士",
    ),
    "フランチャイズ": (
        "加盟店募集",
        "独立開業",
        "起業支援",
        "FC募集",
    ),
    "医療": (
        "歯科",
        "矯正歯科",
        "インプラント",
        "眼科",
        "人間ドック",
        "健康診断",
        "自由診療",
    ),
    "介護": (
        "介護施設",
        "老人ホーム",
        "デイサービス",
        "介護タクシー",
        "訪問介護",
        "終活",
        "葬儀",
    ),
}
SEARCH_SUFFIXES = (
    "転職",
    "求人",
    "脱毛",
    "治療",
    "施術",
    "整形",
    "投資",
    "管理",
    "ツール",
    "システム",
    "講座",
    "スクール",
    "相談",
    "査定",
    "見積",
    "診断",
    "サプリ",
    "保険",
    "ローン",
    "リフォーム",
    "塗装",
    "修理",
    "買取",
    "予約",
    "代行",
)
CORPORATE_SUFFIXES = (
    "株式会社",
    "有限会社",
    "合同会社",
    "医療法人社団",
    "医療法人",
    "一般社団法人",
    "学校法人",
)


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def is_vertical_keyword_sheet(headers: List[str]) -> bool:
    return "検索名" in headers and "ジャンル" in headers


def normalize_keyword(value: str) -> str:
    text = clean(value).casefold()
    text = re.sub(r"[（(].*?[）)]", "", text)
    for suffix in CORPORATE_SUFFIXES:
        text = text.replace(suffix.casefold(), "")
    text = re.sub(r"[\s　・･／/|｜\\ー_.,，。、:：!！?？【】「」『』\"'“”‘’\-]", "", text)
    return text


def is_same_meaning_keyword(candidate: str, existing: str) -> bool:
    cand = normalize_keyword(candidate)
    exist = normalize_keyword(existing)
    if not cand or not exist:
        return False
    if cand == exist:
        return True
    shorter, longer = sorted((cand, exist), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return True
    return False


def load_keywords(service: Any, spreadsheet_id: str, sheet_name: str = KEYWORD_SHEET) -> List[Dict[str, str]]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:ZZ")
    if not values:
        return []
    headers = [clean(value) for value in values[0]]
    if is_vertical_keyword_sheet(headers):
        terms: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for row in values[1:]:
            record = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
            term = clean(record.get("検索名"))
            key = term.casefold()
            if not term or key in seen:
                continue
            status = clean(record.get("状態")) or ACTIVE_STATUS
            if status == "除外":
                continue
            seen.add(key)
            terms.append(
                {
                    "search_name": term,
                    "genre": clean(record.get("ジャンル")),
                    "sub_genre": clean(record.get("サブジャンル")) or term,
                    "type": clean(record.get("種別")),
                    "priority": clean(record.get("優先度")),
                    "status": status,
                    "memo": clean(record.get("メモ")),
                }
            )
        return terms

    genres = headers
    terms: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for col_index, genre in enumerate(genres):
        if not genre:
            continue
        for row in values[1:]:
            term = clean(row[col_index]) if col_index < len(row) else ""
            key = term.casefold()
            if not term or key in seen:
                continue
            seen.add(key)
            terms.append({"search_name": term, "genre": genre, "sub_genre": term})
    return terms


def load_keyword_set(service: Any, spreadsheet_id: str, sheet_name: str = KEYWORD_SHEET) -> Set[str]:
    return {item["search_name"].casefold() for item in load_keywords(service, spreadsheet_id, sheet_name)}


def keyword_exists_semantically(keyword: str, keywords: List[Dict[str, str]], history_keys: Optional[Set[str]] = None) -> bool:
    if history_keys and normalize_keyword(keyword) in {normalize_keyword(item) for item in history_keys}:
        return True
    return any(is_same_meaning_keyword(keyword, item["search_name"]) for item in keywords)


def keyword_columns(service: Any, spreadsheet_id: str, sheet_name: str = KEYWORD_SHEET) -> Dict[str, int]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:ZZ1")
    headers = [clean(value) for value in values[0]] if values else []
    return {header: index for index, header in enumerate(headers) if header}


def ensure_vertical_keyword_headers(service: Any, spreadsheet_id: str, sheet_name: str = KEYWORD_SHEET) -> List[str]:
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:ZZ1")
    headers = [clean(value) for value in values[0]] if values else []
    if not headers:
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:I1", [VERTICAL_KEYWORD_HEADERS])
        return VERTICAL_KEYWORD_HEADERS
    if not is_vertical_keyword_sheet(headers):
        return headers
    updated = list(headers)
    for header in VERTICAL_KEYWORD_HEADERS:
        if header not in updated:
            updated.append(header)
    if updated != headers:
        end_col = column_letter(len(updated) - 1)
        update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A1:{end_col}1", [updated])
    return updated


def column_letter(index: int) -> str:
    index += 1
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def infer_genre_for_keyword(keyword: str, fallback_genre: str = "") -> str:
    text = clean(keyword)
    rules = [
        ("人材・転職", ("転職", "求人", "採用", "看護師", "エージェント", "doda", "ビズリーチ")),
        ("情報商材・スクール", ("講座", "スクール", "副業", "教材", "セミナー", "資格")),
        ("美容・美容医療", ("脱毛", "美容", "クリニック", "痩身", "シミ", "湘南", "TCB")),
        ("D2C・通販", ("通販", "プロテイン", "サプリ", "EC", "コスメ", "健康食品")),
        ("SaaS・BtoB", ("管理", "クラウド", "SaaS", "勤怠", "CRM", "請求", "会計")),
        ("金融", ("ローン", "保険", "NISA", "証券", "投資", "カード", "資産", "FX")),
        ("不動産", ("不動産", "マンション", "戸建", "土地", "賃貸", "物件", "売却", "査定")),
        ("住宅", ("注文住宅", "新築", "リフォーム", "蓄電池", "太陽光", "外壁", "屋根", "引越し")),
        ("自動車", ("中古車", "新車", "カー", "車査定", "車買取", "タイヤ", "バイク", "自転車")),
        ("教育", ("学習塾", "個別指導", "受験", "英会話", "オンライン学習", "知育", "習い事")),
        ("ペット", ("ペット", "ドッグ", "キャット", "犬", "猫")),
        ("通信", ("SIM", "スマホ", "光回線", "Wi-Fi", "WiFi", "ルーター")),
        ("インフラ", ("電気", "ガス", "電力", "ウォーターサーバー")),
        ("飲食", ("宅配", "デリバリー", "冷凍食品", "宅食", "ミールキット", "飲食")),
        ("ブライダル", ("ブライダル", "結婚式", "ウェディング", "式場", "フォトウェディング")),
        ("旅行", ("旅行", "ホテル", "旅館", "航空券", "ツアー", "温泉", "レンタカー")),
        ("士業", ("税理士", "弁護士", "司法書士", "行政書士", "社労士", "会計事務所")),
        ("フランチャイズ", ("フランチャイズ", "加盟店", "独立開業", "起業支援")),
        ("医療", ("歯科", "眼科", "人間ドック", "健康診断", "矯正", "インプラント")),
        ("介護", ("介護", "老人ホーム", "デイサービス", "葬儀", "終活", "霊園")),
        ("婚活", ("婚活", "結婚相談所", "マッチングアプリ", "恋活")),
        ("ハウスクリーニング", ("ハウスクリーニング", "家事代行", "不用品回収", "害虫駆除", "エアコン")),
        ("防犯", ("防犯", "ホームセキュリティ", "警備", "監視カメラ")),
        ("法人向けサービス", ("展示会", "イベント", "セミナー", "法人", "BtoB")),
    ]
    for genre, keywords in rules:
        if any(item in text for item in keywords):
            return genre
    return clean(fallback_genre)


def is_good_keyword_candidate(value: str) -> bool:
    text = clean(value)
    if not text or text in BROAD_TERMS or text in LOW_VALUE_TERMS:
        return False
    if len(text) < 3 or len(text) > 32:
        return False
    if any(marker in text for marker in ("【", "】", "無料ダウンロード", "参加費無料", "必見", "キャンペーン")):
        return False
    if re.fullmatch(r"https?://\S+|[a-z0-9.-]+\.[a-z]{2,}.*", text, flags=re.I):
        return False
    return True


def keyword_type_for(candidate: str, row: Dict[str, Any]) -> str:
    service_name = clean(row.get("サービス名") or row.get("service_name") or row.get("広告表示名"))
    company_name = clean(row.get("会社名") or row.get("page_name"))
    if candidate == service_name:
        return "サービス名"
    if candidate == company_name:
        return "ブランド名"
    if candidate in SEARCH_VALUE_COMPOUNDS:
        return "ベネフィット"
    if any(word in candidate for word in ("悩み", "不安", "改善", "解消", "年収アップ", "業務効率化")):
        return "悩み"
    if any(candidate.endswith(suffix) for suffix in ("脱毛", "治療", "施術", "整形", "矯正", "インプラント")):
        return "施術名"
    if any(candidate.endswith(suffix) for suffix in ("管理", "ツール", "システム", "ソフト", "DX", "CRM", "SFA")):
        return "機能名"
    if any(candidate.endswith(suffix) for suffix in ("講座", "スクール", "資格")):
        return "商品名"
    return "検索ワード"


def text_for_keyword_extraction(row: Dict[str, Any]) -> str:
    keys = (
        "広告本文",
        "広告タイトル",
        "コピー",
        "LP URL",
        "lp_url",
        "link_url",
        "destination_url",
        "raw_text",
    )
    return "\n".join(clean(row.get(key)) for key in keys if clean(row.get(key)))


def extract_known_search_terms(text: str, genre: str) -> List[str]:
    candidates: List[str] = []
    for term in SEARCH_VALUE_COMPOUNDS:
        if term in text:
            candidates.append(term)
    genre_terms = list(GENRE_SEARCH_TERMS.get(genre, ()))
    if genre:
        genre_terms.extend(term for terms in GENRE_SEARCH_TERMS.values() for term in terms if infer_genre_for_keyword(term) == genre)
    for term in dict.fromkeys(genre_terms):
        if term and term in text:
            candidates.append(term)
    return candidates


def extract_suffix_phrases(text: str) -> List[str]:
    candidates: List[str] = []
    for suffix in SEARCH_SUFFIXES:
        pattern = rf"[A-Za-z0-9一-龥ァ-ンー]{{2,18}}{re.escape(suffix)}"
        for match in re.finditer(pattern, text):
            candidate = clean(match.group(0))
            candidate = re.sub(r"^(なら|ならば|まずは|今すぐ|無料で|最新|おすすめ)", "", candidate)
            candidates.append(candidate)
    return candidates


def candidate_keyword_records_from_ad(row: Dict[str, Any]) -> List[Dict[str, str]]:
    genre = clean(row.get("ジャンル") or row.get("genre"))
    base_candidates = [
        clean(row.get("サービス名") or row.get("service_name") or row.get("広告表示名")),
        clean(row.get("会社名") or row.get("page_name")),
    ]
    text = text_for_keyword_extraction(row)
    candidates = base_candidates + extract_known_search_terms(text, genre) + extract_suffix_phrases(text)
    records: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for candidate in candidates:
        candidate = clean(candidate)
        key = normalize_keyword(candidate)
        if not key or key in seen or not is_good_keyword_candidate(candidate):
            continue
        seen.add(key)
        records.append(
            {
                "keyword": candidate,
                "type": keyword_type_for(candidate, row),
                "genre": genre if candidate in SEARCH_VALUE_COMPOUNDS and genre else infer_genre_for_keyword(candidate, genre),
            }
        )
    return records


def append_keyword(
    service: Any,
    spreadsheet_id: str,
    keyword: str,
    genre: str,
    sheet_name: str = KEYWORD_SHEET,
    sub_genre: str = "",
    keyword_type: str = "サービス名",
    priority: str = DEFAULT_PRIORITY,
    memo: str = "",
) -> bool:
    keyword = clean(keyword)
    genre = clean(genre)
    if not is_good_keyword_candidate(keyword) or not genre:
        return False
    existing_keywords = load_keywords(service, spreadsheet_id, sheet_name)
    if keyword_exists_semantically(keyword, existing_keywords):
        return False
    headers = ensure_vertical_keyword_headers(service, spreadsheet_id, sheet_name)
    if is_vertical_keyword_sheet(headers):
        row_map = {
            "検索名": keyword,
            "ジャンル": genre,
            "サブジャンル": clean(sub_genre) or keyword,
            "種別": clean(keyword_type) or "その他",
            "優先度": clean(priority) or DEFAULT_PRIORITY,
            "状態": ACTIVE_STATUS,
            "追加日": today_text(),
            "最終更新日": today_text(),
            "メモ": clean(memo),
        }
        row = [row_map.get(header, "") for header in headers]
        append_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!A:{column_letter(len(headers) - 1)}", [row])
        return True

    columns = keyword_columns(service, spreadsheet_id, sheet_name)
    if genre not in columns:
        return False
    col_index = columns[genre]
    column = column_letter(col_index)
    values = read_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!{column}:{column}")
    next_row = len(values) + 1
    update_values(service, spreadsheet_id, f"{quote_sheet_name(sheet_name)}!{column}{next_row}:{column}{next_row}", [[keyword]])
    return True


def candidate_keywords_from_ad(row: Dict[str, Any]) -> List[str]:
    return [record["keyword"] for record in candidate_keyword_records_from_ad(row)]
