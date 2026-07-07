import argparse
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


TIKTOK_API_BASE_URL = "https://open.tiktokapis.com"
DEFAULT_EXPECTED_USERNAME = "dyb36jfv1f6y"
DEFAULT_PRIVACY_LEVEL = "SELF_ONLY"
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024
MIN_CHUNK_SIZE = 5 * 1024 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024


def clean(value: Any) -> str:
    return str(value or "").strip()


def load_tiktok_credentials() -> Dict[str, str]:
    try:
        from token_refresh import ensure_token_fresh

        ensure_token_fresh("tiktok")
    except Exception:
        pass

    load_dotenv(dotenv_path=".env", override=True)
    keys = (
        "TIKTOK_CLIENT_KEY",
        "TIKTOK_CLIENT_SECRET",
        "TIKTOK_ACCESS_TOKEN",
    )
    credentials = {key: clean(os.getenv(key)) for key in keys}
    missing = [key for key, value in credentials.items() if not value]
    if missing:
        raise RuntimeError(".env に必要な値がありません: " + ", ".join(missing))
    return credentials


def request_tiktok_json(method: str, path: str, access_token: str, **kwargs: Any) -> Dict[str, Any]:
    response = requests.request(
        method,
        f"{TIKTOK_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            **kwargs.pop("headers", {}),
        },
        timeout=60,
        **kwargs,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"TikTok API HTTP {response.status_code}: {response.text}")
    data = response.json() if response.text else {}
    error = data.get("error") or {}
    if clean(error.get("code")) not in ("", "ok"):
        raise RuntimeError(f"TikTok API error: {data}")
    return data


def query_creator_info(access_token: str) -> Dict[str, Any]:
    data = request_tiktok_json("POST", "/v2/post/publish/creator_info/query/", access_token)
    return data.get("data") or {}


def normalize_username(value: Any) -> str:
    return clean(value).lstrip("@").lower()


def expected_username() -> str:
    return normalize_username(os.getenv("TIKTOK_EXPECTED_USERNAME") or DEFAULT_EXPECTED_USERNAME)


def validate_expected_username(creator_info: Dict[str, Any], expected: Optional[str] = None) -> str:
    expected = normalize_username(expected) or expected_username()
    actual = normalize_username(creator_info.get("creator_username"))
    if expected and not actual:
        raise RuntimeError("TikTok投稿先アカウントを確認できないため停止しました。")
    if expected and actual != expected:
        raise RuntimeError(f"TikTok投稿先が @{actual} のため停止しました。想定は @{expected} です。")
    return actual


def pick_privacy_level(creator_info: Dict[str, Any], requested: str) -> str:
    options = creator_info.get("privacy_level_options") or []
    if requested in options:
        return requested
    if DEFAULT_PRIVACY_LEVEL in options:
        return DEFAULT_PRIVACY_LEVEL
    if options:
        return str(options[0])
    return requested


def compute_chunk_size(video_size: int, preferred: int = DEFAULT_CHUNK_SIZE) -> int:
    if video_size < MIN_CHUNK_SIZE:
        return video_size
    return min(max(preferred, MIN_CHUNK_SIZE), MAX_CHUNK_SIZE, video_size)


def init_direct_post(
    access_token: str,
    video_path: Path,
    caption: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
    brand_content_toggle: bool,
    brand_organic_toggle: bool,
    is_aigc: bool,
) -> Dict[str, Any]:
    video_size = video_path.stat().st_size
    chunk_size = compute_chunk_size(video_size)
    total_chunk_count = max(1, math.ceil(video_size / chunk_size))
    payload = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": privacy_level,
            "disable_duet": disable_duet,
            "disable_comment": disable_comment,
            "disable_stitch": disable_stitch,
            "video_cover_timestamp_ms": 1000,
            "brand_content_toggle": brand_content_toggle,
            "brand_organic_toggle": brand_organic_toggle,
            "is_aigc": is_aigc,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count,
        },
    }
    data = request_tiktok_json("POST", "/v2/post/publish/video/init/", access_token, json=payload)
    result = data.get("data") or {}
    if not clean(result.get("upload_url")) or not clean(result.get("publish_id")):
        raise RuntimeError(f"TikTok投稿初期化レスポンスが不正です: {data}")
    result["chunk_size"] = chunk_size
    result["total_chunk_count"] = total_chunk_count
    result["video_size"] = video_size
    return result


def upload_video_file(upload_url: str, video_path: Path, video_size: int, chunk_size: int) -> None:
    with video_path.open("rb") as file:
        start = 0
        while start < video_size:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            end = start + len(chunk) - 1
            response = requests.put(
                upload_url,
                data=chunk,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{video_size}",
                },
                timeout=300,
            )
            if response.status_code not in (200, 201, 206):
                raise RuntimeError(f"TikTok動画アップロード失敗 HTTP {response.status_code}: {response.text}")
            start = end + 1


def fetch_publish_status(access_token: str, publish_id: str) -> Dict[str, Any]:
    data = request_tiktok_json(
        "POST",
        "/v2/post/publish/status/fetch/",
        access_token,
        json={"publish_id": publish_id},
    )
    return data.get("data") or {}


def upload_tiktok_video(
    video_path: Path,
    caption: str,
    privacy_level: str = DEFAULT_PRIVACY_LEVEL,
    expected_account: Optional[str] = None,
    disable_comment: bool = False,
    disable_duet: bool = False,
    disable_stitch: bool = False,
    brand_content_toggle: bool = False,
    brand_organic_toggle: bool = True,
    is_aigc: bool = False,
    wait_status: bool = False,
) -> str:
    if not video_path.exists():
        raise RuntimeError(f"MP4ファイルが見つかりません: {video_path}")
    if video_path.suffix.lower() != ".mp4":
        raise RuntimeError("TikTok投稿には .mp4 ファイルを指定してください。")
    credentials = load_tiktok_credentials()
    access_token = credentials["TIKTOK_ACCESS_TOKEN"]
    creator_info = query_creator_info(access_token)
    username = validate_expected_username(creator_info, expected_account)
    privacy = pick_privacy_level(creator_info, privacy_level)
    init = init_direct_post(
        access_token,
        video_path,
        caption,
        privacy,
        disable_comment,
        disable_duet,
        disable_stitch,
        brand_content_toggle,
        brand_organic_toggle,
        is_aigc,
    )
    upload_video_file(init["upload_url"], video_path, int(init["video_size"]), int(init["chunk_size"]))
    publish_id = clean(init["publish_id"])
    if wait_status:
        for _ in range(10):
            status = fetch_publish_status(access_token, publish_id)
            if status:
                state = clean(status.get("status") or status.get("publish_status"))
                if state and state.lower() not in {"processing", "send_to_tiktok", "pending"}:
                    break
            time.sleep(10)
    account_label = f" @{username}" if username else ""
    return f"TikTok publish_id: {publish_id}{account_label}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="既存MP4をTikTok Content Posting API Direct Postで投稿します。")
    parser.add_argument("--video", required=True)
    parser.add_argument("--caption", required=True)
    parser.add_argument("--privacy-level", default=os.getenv("TIKTOK_PRIVACY_LEVEL", DEFAULT_PRIVACY_LEVEL))
    parser.add_argument("--expected-account", default=os.getenv("TIKTOK_EXPECTED_USERNAME", DEFAULT_EXPECTED_USERNAME))
    parser.add_argument("--disable-comment", action="store_true")
    parser.add_argument("--disable-duet", action="store_true")
    parser.add_argument("--disable-stitch", action="store_true")
    parser.add_argument("--brand-content-toggle", action="store_true")
    parser.add_argument("--no-brand-organic-toggle", dest="brand_organic_toggle", action="store_false")
    parser.add_argument("--is-aigc", action="store_true")
    parser.add_argument("--wait-status", action="store_true")
    parser.set_defaults(brand_organic_toggle=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(
        upload_tiktok_video(
            Path(args.video),
            args.caption,
            privacy_level=args.privacy_level,
            expected_account=args.expected_account,
            disable_comment=args.disable_comment,
            disable_duet=args.disable_duet,
            disable_stitch=args.disable_stitch,
            brand_content_toggle=args.brand_content_toggle,
            brand_organic_toggle=args.brand_organic_toggle,
            is_aigc=args.is_aigc,
            wait_status=args.wait_status,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
