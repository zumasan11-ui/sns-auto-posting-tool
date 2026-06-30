import os
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


ENV_FILE = Path(".env")
GRAPH_BASE_URL = "https://graph.facebook.com/v23.0"
AUTH_URL = "https://www.facebook.com/v23.0/dialog/oauth"
TOKEN_URL = f"{GRAPH_BASE_URL}/oauth/access_token"
SERVER_HOST = "localhost"
SERVER_PORT = 8766
DEFAULT_REDIRECT_URI = "http://localhost:8766/callback"
SCOPES = (
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "business_management",
    "instagram_basic",
    "instagram_content_publish",
)


def env_value(key: str) -> str:
    return os.getenv(key, "").strip()


def first_env(*keys: str) -> str:
    for key in keys:
        value = env_value(key)
        if value:
            return value
    return ""


def load_meta_app() -> Dict[str, str]:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    app_id = first_env("META_APP_ID", "FACEBOOK_APP_ID", "INSTAGRAM_APP_ID")
    app_secret = first_env("META_APP_SECRET", "FACEBOOK_APP_SECRET", "INSTAGRAM_APP_SECRET")
    redirect_uri = first_env("META_REDIRECT_URI", "FACEBOOK_REDIRECT_URI", "INSTAGRAM_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    missing = []
    if not app_id:
        missing.append("META_APP_ID")
    if not app_secret:
        missing.append("META_APP_SECRET")
    if missing:
        raise RuntimeError(".env に必要な値がありません: " + ", ".join(missing))
    return {"app_id": app_id, "app_secret": app_secret, "redirect_uri": redirect_uri}


def save_env_values(updates: Dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    output: List[str] = []
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


def iso_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0))).isoformat()


def build_authorization_url(app_id: str, redirect_uri: str) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(SCOPES),
        "response_type": "code",
        "auth_type": "rerequest",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class CallbackHandler(BaseHTTPRequestHandler):
    code = ""
    error = ""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        CallbackHandler.code = params.get("code", [""])[0]
        CallbackHandler.error = params.get("error", [""])[0] or params.get("error_message", [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = "Meta OAuth code received. You can close this tab." if CallbackHandler.code else f"Meta OAuth failed: {CallbackHandler.error}"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


def receive_authorization_code(auth_url: str, redirect_uri: str) -> str:
    print("Open this URL if it does not open automatically:\n", flush=True)
    print(auth_url, flush=True)
    print(f"\nOAuth Redirect URI: {redirect_uri}", flush=True)
    webbrowser.open(auth_url)

    server = HTTPServer((SERVER_HOST, SERVER_PORT), CallbackHandler)
    server.handle_request()
    server.server_close()

    if CallbackHandler.error:
        raise RuntimeError(f"Meta OAuth error: {CallbackHandler.error}")
    if not CallbackHandler.code:
        raise RuntimeError("認可コードを受け取れませんでした。")
    return CallbackHandler.code


def request_json(method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
    response = requests.request(method, url, timeout=30, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def exchange_short_lived_token(app_id: str, app_secret: str, redirect_uri: str, code: str) -> Dict[str, Any]:
    return request_json(
        "GET",
        TOKEN_URL,
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )


def exchange_long_lived_token(app_id: str, app_secret: str, token: str) -> Dict[str, Any]:
    return request_json(
        "GET",
        TOKEN_URL,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": token,
        },
    )


def graph_get(path: str, token: str, fields: str) -> Dict[str, Any]:
    return request_json(
        "GET",
        f"{GRAPH_BASE_URL}{path}",
        params={"fields": fields, "access_token": token},
    )


def choose_page(pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not pages:
        raise RuntimeError("管理可能なFacebookページが見つかりませんでした。")
    preferred = env_value("FACEBOOK_PAGE_NAME")
    if preferred:
        for page in pages:
            if page.get("name") == preferred:
                return page
        raise RuntimeError(f"FACEBOOK_PAGE_NAME={preferred} に一致するページがありません。")
    return pages[0]


def get_page_access_token(page_id: str, user_token: str) -> str:
    data = graph_get(f"/{page_id}", user_token, "access_token")
    token = str(data.get("access_token", "")).strip()
    if not token:
        raise RuntimeError(f"Facebookページアクセストークンを取得できませんでした: {data}")
    return token


def main() -> int:
    try:
        app = load_meta_app()
        auth_url = build_authorization_url(app["app_id"], app["redirect_uri"])
        code = receive_authorization_code(auth_url, app["redirect_uri"])
        short_lived = exchange_short_lived_token(
            app["app_id"],
            app["app_secret"],
            app["redirect_uri"],
            code,
        )
        short_token = str(short_lived.get("access_token", "")).strip()
        if not short_token:
            raise RuntimeError(f"短期トークン交換レスポンスが不正です: {short_lived}")

        long_lived = exchange_long_lived_token(app["app_id"], app["app_secret"], short_token)
        user_token = str(long_lived.get("access_token", "")).strip()
        if not user_token:
            raise RuntimeError(f"長期トークン交換レスポンスが不正です: {long_lived}")

        pages_data = graph_get(
            "/me/accounts",
            user_token,
            "id,name,instagram_business_account{id,username}",
        )
        page = choose_page(pages_data.get("data", []))
        page_id = str(page.get("id", "")).strip()
        if not page_id:
            raise RuntimeError(f"FacebookページIDを取得できませんでした: {page}")
        page_token = get_page_access_token(page_id, user_token)

        instagram_account = page.get("instagram_business_account") or {}
        instagram_id = str(instagram_account.get("id", "")).strip()
        if not instagram_id:
            raise RuntimeError(
                f"Facebookページ「{page.get('name')}」にInstagram Business Accountが接続されていません。"
            )

        updates = {
            "META_APP_ID": app["app_id"],
            "META_APP_SECRET": app["app_secret"],
            "META_REDIRECT_URI": app["redirect_uri"],
            "INSTAGRAM_USER_ID": instagram_id,
            "INSTAGRAM_ACCESS_TOKEN": user_token,
            "INSTAGRAM_LAST_REFRESHED_AT": datetime.now(timezone.utc).isoformat(),
            "FACEBOOK_PAGE_ID": page_id,
            "FACEBOOK_PAGE_ACCESS_TOKEN": page_token,
            "FACEBOOK_USER_ACCESS_TOKEN": user_token,
            "FACEBOOK_LAST_REFRESHED_AT": datetime.now(timezone.utc).isoformat(),
        }
        if long_lived.get("expires_in"):
            expires_at = iso_at(int(long_lived["expires_in"]))
            updates["INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT"] = expires_at
            updates["FACEBOOK_USER_ACCESS_TOKEN_EXPIRES_AT"] = expires_at
        save_env_values(updates)

        print("\nMeta OAuth complete.")
        print(f"FACEBOOK_PAGE_ID={page_id}")
        print(f"FACEBOOK_PAGE_NAME={page.get('name')}")
        print(f"INSTAGRAM_USER_ID={instagram_id}")
        print(f"INSTAGRAM_USERNAME={instagram_account.get('username', '')}")
        return 0
    except KeyboardInterrupt:
        print("\nキャンセルしました。")
        return 130
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
