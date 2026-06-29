import os
import sys
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv


ENV_FILE = Path(".env")
GRAPH_BASE_URL = "https://graph.facebook.com/v23.0"
DEFAULT_PAGE_NAME = "Kazuma Marketing"
REQUIRED_PERMISSIONS = {
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
}


def load_access_token() -> str:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(".env に INSTAGRAM_ACCESS_TOKEN を入れてから再実行してください。")
    return token


def save_env_values(updates: Dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    output = []
    seen = set()

    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue

        key, _value = line.split("=", 1)
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)

    if output and output[-1] != "":
        output.append("")

    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


def graph_get(path: str, token: str, fields: str) -> Dict:
    response = requests.get(
        f"{GRAPH_BASE_URL}{path}",
        params={"fields": fields, "access_token": token},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Graph APIエラー: HTTP {response.status_code}: {response.text}")
    return response.json()


def get_pages(token: str) -> List[Dict]:
    data = graph_get(
        "/me/accounts",
        token,
        "id,name,instagram_business_account{id,username}",
    )
    pages = data.get("data", [])
    if not isinstance(pages, list):
        raise RuntimeError(f"/me/accounts のレスポンス形式が不正です: {data}")
    return pages


def get_granted_permissions(token: str) -> List[str]:
    response = requests.get(
        f"{GRAPH_BASE_URL}/me/permissions",
        params={"access_token": token},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"権限確認エラー: HTTP {response.status_code}: {response.text}")

    data = response.json().get("data", [])
    return [
        item.get("permission", "")
        for item in data
        if item.get("status") == "granted" and item.get("permission")
    ]


def find_instagram_account(pages: List[Dict], page_name: str) -> Dict[str, str]:
    page_names = []
    for page in pages:
        name = page.get("name", "")
        page_names.append(name)
        if name != page_name:
            continue

        instagram_account = page.get("instagram_business_account") or {}
        instagram_id = str(instagram_account.get("id", "")).strip()
        username = str(instagram_account.get("username", "")).strip()
        if not instagram_id:
            raise RuntimeError(
                f"Facebookページ「{page_name}」は見つかりましたが、instagram_business_account がありません。"
            )
        return {"id": instagram_id, "username": username, "page_name": name}

    raise RuntimeError(
        f"Facebookページ「{page_name}」が見つかりませんでした。取得できたページ: {', '.join(page_names) or 'なし'}"
    )


def main() -> int:
    try:
        token = load_access_token()
        granted_permissions = set(get_granted_permissions(token))
        missing_permissions = sorted(REQUIRED_PERMISSIONS - granted_permissions)
        if missing_permissions:
            raise RuntimeError(
                "Instagram Graph APIに必要な権限が不足しています: "
                + ", ".join(missing_permissions)
                + "\nGraph API ExplorerでUser Access Tokenを作り直してください。"
            )

        pages = get_pages(token)
        instagram_account = find_instagram_account(pages, DEFAULT_PAGE_NAME)
        save_env_values({"INSTAGRAM_USER_ID": instagram_account["id"]})
        print("Instagram Business Account IDを .env に保存しました。")
        print(f"PAGE_NAME={instagram_account['page_name']}")
        print(f"INSTAGRAM_USERNAME={instagram_account['username']}")
        print(f"INSTAGRAM_USER_ID={instagram_account['id']}")
        return 0
    except Exception as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
