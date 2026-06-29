import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


NOTION_API_BASE_URL = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2022-06-28"
ENV_FILE = Path(".env")


def normalize_notion_id(value: str) -> str:
    return value.strip().replace("-", "")


def load_notion_config() -> Dict[str, str]:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    token = os.getenv("NOTION_TOKEN", "").strip()
    database_id = normalize_notion_id(os.getenv("NOTION_DATABASE_ID", ""))
    version = os.getenv("NOTION_VERSION", "").strip() or DEFAULT_NOTION_VERSION
    missing = []

    if not token:
        missing.append("NOTION_TOKEN")
    if not database_id:
        missing.append("NOTION_DATABASE_ID")

    if missing:
        raise RuntimeError(".env に必要な値がありません: " + ", ".join(missing))

    return {"token": token, "database_id": database_id, "version": version}


def notion_headers(config: Dict[str, str]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config['token']}",
        "Notion-Version": config["version"],
        "Content-Type": "application/json",
    }


def request_notion(
    method: str,
    path: str,
    config: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    response = requests.request(
        method,
        f"{NOTION_API_BASE_URL}{path}",
        headers=notion_headers(config),
        json=payload,
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Notion APIエラー: HTTP {response.status_code} {response.text}")

    return response.json() if response.text else {}


def retrieve_database(config: Dict[str, str]) -> Dict[str, Any]:
    return request_notion("GET", f"/databases/{config['database_id']}", config)


def retrieve_page(config: Dict[str, str], page_id: Optional[str] = None) -> Dict[str, Any]:
    return request_notion("GET", f"/pages/{normalize_notion_id(page_id or config['database_id'])}", config)


def retrieve_block_children(config: Dict[str, str], block_id: Optional[str] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    cursor = None
    target_id = normalize_notion_id(block_id or config["database_id"])

    while True:
        path = f"/blocks/{target_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        data = request_notion("GET", path, config)
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            return results
        cursor = data.get("next_cursor")


def search_notion(config: Dict[str, str], query: str, page_size: int = 10) -> List[Dict[str, Any]]:
    payload = {"query": query, "page_size": min(max(page_size, 1), 100)}
    data = request_notion("POST", "/search", config, payload)
    return data.get("results", [])


def query_database(
    config: Dict[str, str],
    page_size: int = 10,
    filter_data: Optional[Dict[str, Any]] = None,
    sorts: Optional[List[Dict[str, Any]]] = None,
    fetch_all: bool = False,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    cursor = None

    while True:
        payload: Dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
        if cursor:
            payload["start_cursor"] = cursor
        if filter_data:
            payload["filter"] = filter_data
        if sorts:
            payload["sorts"] = sorts

        data = request_notion(
            "POST",
            f"/databases/{config['database_id']}/query",
            config,
            payload,
        )
        results.extend(data.get("results", []))
        if not fetch_all or not data.get("has_more"):
            return results
        cursor = data.get("next_cursor")


def create_database_page(
    config: Dict[str, str],
    properties: Dict[str, Any],
    children: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "parent": {"database_id": config["database_id"]},
        "properties": properties,
    }
    if children:
        payload["children"] = children

    return request_notion("POST", "/pages", config, payload)


def update_page(
    config: Dict[str, str],
    page_id: str,
    properties: Optional[Dict[str, Any]] = None,
    archived: Optional[bool] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if properties:
        payload["properties"] = properties
    if archived is not None:
        payload["archived"] = archived
    if not payload:
        raise RuntimeError("更新する内容がありません。--properties-json または --archive/--restore を指定してください。")

    return request_notion("PATCH", f"/pages/{normalize_notion_id(page_id)}", config, payload)


def paragraph_children_from_text(text: str) -> List[Dict[str, Any]]:
    children = []
    for paragraph in [part.strip() for part in text.split("\n\n") if part.strip()]:
        children.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": paragraph[:2000]},
                        }
                    ]
                },
            }
        )
    return children


def load_json_arg(value: Optional[str], label: str) -> Optional[Any]:
    if not value:
        return None

    source = value.strip()
    if source.startswith("@"):
        source = Path(source[1:]).read_text(encoding="utf-8")

    try:
        return json.loads(source)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{label} のJSON形式が不正です: {error}") from error


def rich_text_plain_text(items: List[Dict[str, Any]]) -> str:
    return "".join(str(item.get("plain_text", "")) for item in items)


def property_to_plain_value(value: Dict[str, Any]) -> Any:
    property_type = value.get("type")
    data = value.get(property_type, None)

    if property_type == "title":
        return rich_text_plain_text(data or [])
    if property_type == "rich_text":
        return rich_text_plain_text(data or [])
    if property_type in ("select", "status"):
        return (data or {}).get("name")
    if property_type == "multi_select":
        return [item.get("name") for item in data or []]
    if property_type == "date":
        return data
    if property_type in ("checkbox", "url", "email", "phone_number", "number"):
        return data
    if property_type in ("created_time", "last_edited_time"):
        return data
    if property_type in ("created_by", "last_edited_by"):
        return (data or {}).get("id")
    if property_type == "people":
        return [item.get("id") for item in data or []]
    if property_type == "relation":
        return [item.get("id") for item in data or []]
    if property_type == "files":
        return [item.get("name") for item in data or []]
    if property_type in ("formula", "rollup"):
        return data

    return data


def summarize_page(page: Dict[str, Any]) -> Dict[str, Any]:
    properties = page.get("properties", {})
    return {
        "id": page.get("id"),
        "url": page.get("url"),
        "created_time": page.get("created_time"),
        "last_edited_time": page.get("last_edited_time"),
        "properties": {
            name: property_to_plain_value(value)
            for name, value in properties.items()
            if isinstance(value, dict)
        },
    }


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notion APIでデータベースを読み書きします。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("schema", help="データベースのプロパティ定義を表示します。")

    page_parser = subparsers.add_parser("page", help="ページ情報を表示します。")
    page_parser.add_argument("--page-id", help="省略時は NOTION_DATABASE_ID をページIDとして使います。")

    children_parser = subparsers.add_parser("children", help="ページ/ブロック配下の子ブロックを表示します。")
    children_parser.add_argument("--block-id", help="省略時は NOTION_DATABASE_ID をブロックIDとして使います。")

    search_parser = subparsers.add_parser("search", help="インテグレーションがアクセスできるNotionオブジェクトを検索します。")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=10)

    list_parser = subparsers.add_parser("list", help="データベースのページを取得します。")
    list_parser.add_argument("--limit", type=int, default=10)
    list_parser.add_argument("--all", action="store_true", help="全ページをページング取得します。")
    list_parser.add_argument("--filter-json", help="Notion filter JSON。@file.json も指定できます。")
    list_parser.add_argument("--sorts-json", help="Notion sorts JSON配列。@file.json も指定できます。")
    list_parser.add_argument("--raw", action="store_true", help="Notion APIレスポンスを要約せず表示します。")

    create_parser = subparsers.add_parser("create", help="データベースにページを作成します。")
    create_parser.add_argument("--properties-json", required=True, help="Notion properties JSON。@file.json も指定できます。")
    create_parser.add_argument("--body", help="ページ本文。空行区切りでparagraph blockにします。")
    create_parser.add_argument("--body-file", help="ページ本文ファイル。")

    update_parser = subparsers.add_parser("update", help="既存ページを更新します。")
    update_parser.add_argument("--page-id", required=True)
    update_parser.add_argument("--properties-json", help="Notion properties JSON。@file.json も指定できます。")
    archive_group = update_parser.add_mutually_exclusive_group()
    archive_group.add_argument("--archive", action="store_true")
    archive_group.add_argument("--restore", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_notion_config()

    if args.command == "schema":
        database = retrieve_database(config)
        print_json(
            {
                "id": database.get("id"),
                "title": database.get("title"),
                "url": database.get("url"),
                "properties": database.get("properties", {}),
            }
        )
        return 0

    if args.command == "page":
        print_json(retrieve_page(config, args.page_id))
        return 0

    if args.command == "children":
        print_json(retrieve_block_children(config, args.block_id))
        return 0

    if args.command == "search":
        print_json([summarize_page(item) if item.get("object") == "page" else item for item in search_notion(config, args.query, args.limit)])
        return 0

    if args.command == "list":
        filter_data = load_json_arg(args.filter_json, "--filter-json")
        sorts = load_json_arg(args.sorts_json, "--sorts-json")
        pages = query_database(config, args.limit, filter_data, sorts, args.all)
        print_json(pages if args.raw else [summarize_page(page) for page in pages])
        return 0

    if args.command == "create":
        properties = load_json_arg(args.properties_json, "--properties-json")
        if not isinstance(properties, dict):
            raise RuntimeError("--properties-json はJSONオブジェクトにしてください。")
        body_text = ""
        if args.body_file:
            body_text = Path(args.body_file).read_text(encoding="utf-8")
        elif args.body:
            body_text = args.body
        page = create_database_page(
            config,
            properties,
            paragraph_children_from_text(body_text) if body_text else None,
        )
        print_json(summarize_page(page))
        return 0

    if args.command == "update":
        properties = load_json_arg(args.properties_json, "--properties-json")
        if properties is not None and not isinstance(properties, dict):
            raise RuntimeError("--properties-json はJSONオブジェクトにしてください。")
        archived = True if args.archive else False if args.restore else None
        page = update_page(config, args.page_id, properties, archived)
        print_json(summarize_page(page))
        return 0

    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nキャンセルしました。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        raise SystemExit(1)
