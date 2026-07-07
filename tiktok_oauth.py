import argparse
import secrets
import urllib.parse
from typing import Dict

import requests
from dotenv import load_dotenv

from token_refresh import env_value, iso_at, now_iso, require_values, save_env_values
from tiktok_poster import DEFAULT_EXPECTED_USERNAME, query_creator_info, validate_expected_username


AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DEFAULT_SCOPES = ("user.info.basic", "video.publish")


def build_authorization_url(client_key: str, redirect_uri: str, scopes: tuple[str, ...], state: str) -> str:
    params = {
        "client_key": client_key,
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str, client_key: str, client_secret: str, redirect_uri: str) -> Dict[str, str]:
    response = requests.post(
        TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"TikTok OAuth HTTP {response.status_code}: {response.text}")
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"TikTok OAuth error: {data}")
    access_token = str(data.get("access_token", "")).strip()
    refresh_token = str(data.get("refresh_token", "")).strip()
    if not access_token or not refresh_token:
        raise RuntimeError(f"TikTok OAuthレスポンスが不正です: {data}")
    return data


def save_token_response(data: Dict[str, str], authorized_username: str = "") -> None:
    updates = {
        "TIKTOK_ACCESS_TOKEN": str(data.get("access_token", "")),
        "TIKTOK_REFRESH_TOKEN": str(data.get("refresh_token", "")),
        "TIKTOK_OPEN_ID": str(data.get("open_id", "")),
        "TIKTOK_SCOPE": str(data.get("scope", "")),
        "TIKTOK_LAST_REFRESHED_AT": now_iso(),
    }
    if authorized_username:
        updates["TIKTOK_AUTHORIZED_USERNAME"] = authorized_username
    if data.get("expires_in"):
        updates["TIKTOK_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(int(data["expires_in"]))
    if data.get("refresh_expires_in"):
        updates["TIKTOK_REFRESH_TOKEN_EXPIRES_AT"] = iso_at(int(data["refresh_expires_in"]))
    save_env_values({key: value for key, value in updates.items() if value})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TikTok Login Kitの初回OAuth認証URL生成・code交換を行います。")
    parser.add_argument("--print-url", action="store_true", help="認証URLを表示します。")
    parser.add_argument("--code", help="TikTokからリダイレクトされたURL内の code。")
    parser.add_argument("--scopes", default=",".join(DEFAULT_SCOPES), help="カンマ区切りのTikTokスコープ。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(dotenv_path=".env", override=True)
    values = require_values("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI")
    scopes = tuple(item.strip() for item in env_value("TIKTOK_SCOPES").split(",") if item.strip()) or tuple(
        item.strip() for item in DEFAULT_SCOPES
    )
    if args.scopes:
        scopes = tuple(item.strip() for item in args.scopes.split(",") if item.strip())
    if args.print_url:
        state = secrets.token_urlsafe(24)
        print(build_authorization_url(values["TIKTOK_CLIENT_KEY"], values["TIKTOK_REDIRECT_URI"], scopes, state))
        print(f"state={state}")
        return 0
    if args.code:
        data = exchange_code(args.code, values["TIKTOK_CLIENT_KEY"], values["TIKTOK_CLIENT_SECRET"], values["TIKTOK_REDIRECT_URI"])
        expected = env_value("TIKTOK_EXPECTED_USERNAME") or DEFAULT_EXPECTED_USERNAME
        creator_info = query_creator_info(str(data["access_token"]))
        username = validate_expected_username(creator_info, expected)
        save_token_response(data, username)
        print(f"TikTok OAuthトークンを.envへ保存しました。投稿先: @{username}")
        return 0
    raise RuntimeError("--print-url または --code を指定してください。")


if __name__ == "__main__":
    raise SystemExit(main())
