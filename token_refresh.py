import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


ENV_FILE = Path(".env")
META_GRAPH_BASE_URL = "https://graph.facebook.com/v23.0"
THREADS_GRAPH_BASE_URL = "https://graph.threads.net"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
YOUTUBE_SCOPES = ("https://www.googleapis.com/auth/youtube.upload",)
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REFRESH_MARGIN = timedelta(days=7)


class ReauthRequired(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_at(seconds: int) -> str:
    return (utc_now() + timedelta(seconds=max(seconds, 0))).isoformat()


def now_iso() -> str:
    return utc_now().isoformat()


def load_env() -> None:
    load_dotenv(dotenv_path=ENV_FILE, override=True)


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


def env_value(key: str) -> str:
    return os.getenv(key, "").strip()


def first_env(*keys: str) -> Tuple[str, str]:
    for key in keys:
        value = env_value(key)
        if value:
            return key, value
    return "", ""


def parse_expires_at(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def should_refresh(expires_at_key: str, force: bool) -> bool:
    if force:
        return True
    expires_at = parse_expires_at(env_value(expires_at_key))
    if expires_at is None:
        return True
    return expires_at - utc_now() <= REFRESH_MARGIN


def require_values(*keys: str) -> Dict[str, str]:
    values = {key: env_value(key) for key in keys}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ReauthRequired(".env に必要な値がありません: " + ", ".join(missing))
    return values


def request_json(method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
    response = requests.request(method, url, timeout=30, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    if not response.text:
        return {}
    return response.json()


def refresh_threads(force: bool = False) -> Dict[str, Any]:
    load_env()
    if not should_refresh("THREADS_ACCESS_TOKEN_EXPIRES_AT", force):
        return {"service": "threads", "status": "skipped"}

    values = require_values("THREADS_ACCESS_TOKEN")
    data = request_json(
        "GET",
        f"{THREADS_GRAPH_BASE_URL}/refresh_access_token",
        params={
            "grant_type": "th_refresh_token",
            "access_token": values["THREADS_ACCESS_TOKEN"],
        },
    )
    access_token = str(data.get("access_token", "")).strip()
    expires_in = int(data.get("expires_in") or 0)
    if not access_token:
        raise ReauthRequired(f"Threads token refreshレスポンスが不正です: {data}")

    updates = {
        "THREADS_ACCESS_TOKEN": access_token,
        "THREADS_LAST_REFRESHED_AT": now_iso(),
    }
    if expires_in:
        updates["THREADS_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(expires_in)
    save_env_values(updates)
    return {"service": "threads", "status": "refreshed", "expires_in": expires_in}


def meta_app_credentials(service: str = "combined") -> Tuple[str, str]:
    if service == "instagram":
        _id_key, app_id = first_env("INSTAGRAM_APP_ID", "META_APP_ID")
        _secret_key, app_secret = first_env("INSTAGRAM_APP_SECRET", "META_APP_SECRET")
    elif service == "facebook":
        _id_key, app_id = first_env("FACEBOOK_APP_ID", "META_APP_ID")
        _secret_key, app_secret = first_env("FACEBOOK_APP_SECRET", "META_APP_SECRET")
    else:
        _id_key, app_id = first_env("META_APP_ID", "FACEBOOK_APP_ID", "INSTAGRAM_APP_ID")
        _secret_key, app_secret = first_env("META_APP_SECRET", "FACEBOOK_APP_SECRET", "INSTAGRAM_APP_SECRET")
    if not app_id or not app_secret:
        raise ReauthRequired(
            "Meta系トークン更新には対象サービスのAPP_IDとAPP_SECRETが必要です。"
        )
    return app_id, app_secret


def exchange_meta_long_lived_token(token: str, service: str = "combined") -> Dict[str, Any]:
    app_id, app_secret = meta_app_credentials(service)
    return request_json(
        "GET",
        f"{META_GRAPH_BASE_URL}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": token,
        },
    )


def refresh_instagram(force: bool = False) -> Dict[str, Any]:
    load_env()
    if not should_refresh("INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT", force):
        return {"service": "instagram", "status": "skipped"}

    values = require_values("INSTAGRAM_ACCESS_TOKEN")
    token = values["INSTAGRAM_ACCESS_TOKEN"]
    try:
        data = exchange_meta_long_lived_token(token, "instagram")
    except ReauthRequired:
        data = request_json(
            "GET",
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
        )
    except RuntimeError as error:
        raise ReauthRequired(f"Instagram token refreshに失敗しました。再認証してください: {error}") from error

    access_token = str(data.get("access_token", "")).strip()
    expires_in = int(data.get("expires_in") or 0)
    if not access_token:
        raise ReauthRequired(f"Instagram token refreshレスポンスが不正です: {data}")

    updates = {
        "INSTAGRAM_ACCESS_TOKEN": access_token,
        "INSTAGRAM_LAST_REFRESHED_AT": now_iso(),
    }
    if expires_in:
        updates["INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(expires_in)
    save_env_values(updates)
    return {"service": "instagram", "status": "refreshed", "expires_in": expires_in}


def refresh_facebook(force: bool = False) -> Dict[str, Any]:
    load_env()
    if not should_refresh("FACEBOOK_PAGE_ACCESS_TOKEN_EXPIRES_AT", force):
        return {"service": "facebook", "status": "skipped"}

    page_id = env_value("FACEBOOK_PAGE_ID")
    if not page_id:
        raise ReauthRequired(".env に必要な値がありません: FACEBOOK_PAGE_ID")

    user_token = env_value("FACEBOOK_USER_ACCESS_TOKEN") or env_value("INSTAGRAM_ACCESS_TOKEN")
    if not user_token:
        raise ReauthRequired(
            "Facebookページトークン更新には FACEBOOK_USER_ACCESS_TOKEN または INSTAGRAM_ACCESS_TOKEN が必要です。"
        )

    try:
        data = exchange_meta_long_lived_token(user_token, "facebook")
        user_token = str(data.get("access_token") or user_token)
    except ReauthRequired:
        pass
    except RuntimeError as error:
        raise ReauthRequired(f"Facebook user token refreshに失敗しました。再認証してください: {error}") from error

    page = request_json(
        "GET",
        f"{META_GRAPH_BASE_URL}/{page_id}",
        params={"fields": "access_token", "access_token": user_token},
    )
    page_token = str(page.get("access_token", "")).strip()
    if not page_token:
        raise ReauthRequired(f"Facebookページアクセストークンを取得できませんでした: {page}")

    updates = {
        "FACEBOOK_PAGE_ACCESS_TOKEN": page_token,
        "FACEBOOK_USER_ACCESS_TOKEN": user_token,
        "FACEBOOK_LAST_REFRESHED_AT": now_iso(),
    }
    if data_expires := locals().get("data", {}).get("expires_in"):
        updates["FACEBOOK_USER_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(int(data_expires))
    save_env_values(updates)
    return {"service": "facebook", "status": "refreshed"}


def refresh_linkedin(force: bool = False) -> Dict[str, Any]:
    load_env()
    if not should_refresh("LINKEDIN_ACCESS_TOKEN_EXPIRES_AT", force):
        return {"service": "linkedin", "status": "skipped"}

    values = require_values("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET", "LINKEDIN_REFRESH_TOKEN")
    data = request_json(
        "POST",
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": values["LINKEDIN_REFRESH_TOKEN"],
            "client_id": values["LINKEDIN_CLIENT_ID"],
            "client_secret": values["LINKEDIN_CLIENT_SECRET"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    access_token = str(data.get("access_token", "")).strip()
    if not access_token:
        raise ReauthRequired(f"LinkedIn token refreshレスポンスが不正です: {data}")

    updates = {
        "LINKEDIN_ACCESS_TOKEN": access_token,
        "LINKEDIN_LAST_REFRESHED_AT": now_iso(),
    }
    if data.get("refresh_token"):
        updates["LINKEDIN_REFRESH_TOKEN"] = str(data["refresh_token"])
    if data.get("expires_in"):
        updates["LINKEDIN_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(int(data["expires_in"]))
    if data.get("refresh_token_expires_in"):
        updates["LINKEDIN_REFRESH_TOKEN_EXPIRES_AT"] = iso_at(int(data["refresh_token_expires_in"]))
    save_env_values(updates)
    return {"service": "linkedin", "status": "refreshed", "expires_in": data.get("expires_in")}


def refresh_youtube(force: bool = False) -> Dict[str, Any]:
    load_env()
    if not should_refresh("YOUTUBE_ACCESS_TOKEN_EXPIRES_AT", force):
        return {"service": "youtube", "status": "skipped"}

    values = require_values(
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    )
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = Credentials(
        token=None,
        refresh_token=values["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=values["YOUTUBE_CLIENT_ID"],
        client_secret=values["YOUTUBE_CLIENT_SECRET"],
        scopes=YOUTUBE_SCOPES,
    )
    try:
        credentials.refresh(Request())
    except Exception as error:
        raise ReauthRequired(f"YouTube access token refreshに失敗しました。再認証してください: {error}") from error

    updates = {
        "YOUTUBE_ACCESS_TOKEN": credentials.token or "",
        "YOUTUBE_LAST_REFRESHED_AT": now_iso(),
    }
    if credentials.expiry:
        updates["YOUTUBE_ACCESS_TOKEN_EXPIRES_AT"] = credentials.expiry.replace(tzinfo=timezone.utc).isoformat()
    save_env_values({key: value for key, value in updates.items() if value})
    return {"service": "youtube", "status": "refreshed"}


def refresh_tiktok(force: bool = False) -> Dict[str, Any]:
    load_env()
    if not should_refresh("TIKTOK_ACCESS_TOKEN_EXPIRES_AT", force):
        return {"service": "tiktok", "status": "skipped"}

    values = require_values("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN")
    data = request_json(
        "POST",
        TIKTOK_TOKEN_URL,
        data={
            "client_key": values["TIKTOK_CLIENT_KEY"],
            "client_secret": values["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": values["TIKTOK_REFRESH_TOKEN"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
    )
    access_token = str(data.get("access_token", "")).strip()
    refresh_token = str(data.get("refresh_token", "")).strip()
    if not access_token:
        raise ReauthRequired(f"TikTok token refreshレスポンスが不正です: {data}")
    updates = {
        "TIKTOK_ACCESS_TOKEN": access_token,
        "TIKTOK_LAST_REFRESHED_AT": now_iso(),
    }
    if refresh_token:
        updates["TIKTOK_REFRESH_TOKEN"] = refresh_token
    if data.get("open_id"):
        updates["TIKTOK_OPEN_ID"] = str(data["open_id"])
    if data.get("scope"):
        updates["TIKTOK_SCOPE"] = str(data["scope"])
    if data.get("expires_in"):
        updates["TIKTOK_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(int(data["expires_in"]))
    if data.get("refresh_expires_in"):
        updates["TIKTOK_REFRESH_TOKEN_EXPIRES_AT"] = iso_at(int(data["refresh_expires_in"]))
    save_env_values({key: value for key, value in updates.items() if value})
    return {"service": "tiktok", "status": "refreshed", "expires_in": data.get("expires_in")}


REFRESHERS = {
    "threads": refresh_threads,
    "instagram": refresh_instagram,
    "facebook": refresh_facebook,
    "linkedin": refresh_linkedin,
    "youtube": refresh_youtube,
    "tiktok": refresh_tiktok,
}


def refresh_service(service: str, force: bool = False) -> Dict[str, Any]:
    if service not in REFRESHERS:
        raise RuntimeError(f"未対応のサービスです: {service}")
    return REFRESHERS[service](force)


def ensure_token_fresh(service: str, strict: bool = False) -> None:
    try:
        result = refresh_service(service, force=False)
        if result.get("status") == "refreshed":
            print(f"{service} token refreshed.", file=sys.stderr)
    except ReauthRequired:
        if strict:
            raise
    except Exception as error:
        if strict:
            raise
        print(f"{service} token refresh skipped: {error}", file=sys.stderr)


def token_status(service: str) -> Dict[str, Any]:
    load_env()
    keys = {
        "threads": ("THREADS_ACCESS_TOKEN", "THREADS_ACCESS_TOKEN_EXPIRES_AT"),
        "instagram": ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT"),
        "facebook": ("FACEBOOK_PAGE_ACCESS_TOKEN", "FACEBOOK_PAGE_ACCESS_TOKEN_EXPIRES_AT"),
        "linkedin": ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_ACCESS_TOKEN_EXPIRES_AT"),
        "youtube": ("YOUTUBE_REFRESH_TOKEN", "YOUTUBE_ACCESS_TOKEN_EXPIRES_AT"),
        "tiktok": ("TIKTOK_REFRESH_TOKEN", "TIKTOK_ACCESS_TOKEN_EXPIRES_AT"),
    }[service]
    expires_at = parse_expires_at(env_value(keys[1]))
    return {
        "service": service,
        "token_present": bool(env_value(keys[0])),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "refresh_due": should_refresh(keys[1], False),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SNSアクセストークンを更新し、.envへ保存します。")
    parser.add_argument(
        "service",
        choices=("all", "status", "threads", "instagram", "facebook", "linkedin", "youtube", "tiktok"),
        help="更新対象。status は期限情報だけ表示します。",
    )
    parser.add_argument("--force", action="store_true", help="期限に関係なく更新します。")
    parser.add_argument("--strict", action="store_true", help="更新できないサービスがあれば失敗終了します。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    services = list(REFRESHERS)

    if args.service == "status":
        print(json.dumps([token_status(service) for service in services], ensure_ascii=False, indent=2))
        return 0

    targets = services if args.service == "all" else [args.service]
    results = []
    had_error = False
    for service in targets:
        try:
            results.append(refresh_service(service, args.force))
        except Exception as error:
            had_error = True
            results.append({"service": service, "status": "reauth_required", "error": str(error)})
            if args.strict:
                break

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if had_error and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
