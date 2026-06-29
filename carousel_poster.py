import argparse
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from main import (
    create_instagram_image_post,
    get_linkedin_person_urn,
    load_instagram_credentials,
    load_linkedin_credentials,
    request_meta_graph_api,
)


META_GRAPH_BASE_URL = "https://graph.facebook.com/v23.0"
LINKEDIN_VERSION = "202506"


def wait_for_instagram_container(creation_id: str, access_token: str) -> None:
    for _ in range(20):
        data = request_meta_graph_api(
            "GET",
            f"/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
        )
        status_code = data.get("status_code")
        if status_code in ("FINISHED", "PUBLISHED"):
            return
        if status_code == "ERROR":
            raise RuntimeError(f"Instagramコンテナ作成に失敗しました: {data}")
        time.sleep(3)
    raise RuntimeError(f"Instagramコンテナが時間内に完了しませんでした: {creation_id}")


def post_instagram_carousel(image_urls: List[str], caption: str) -> str:
    credentials = load_instagram_credentials()
    user_id = credentials["INSTAGRAM_USER_ID"]
    access_token = credentials["INSTAGRAM_ACCESS_TOKEN"]
    child_ids = []

    if not 2 <= len(image_urls) <= 10:
        raise RuntimeError("Instagramカルーセルは2〜10枚の画像URLが必要です。")

    for image_url in image_urls:
        data = request_meta_graph_api(
            "POST",
            f"/{user_id}/media",
            data={
                "media_type": "IMAGE",
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": access_token,
            },
        )
        child_id = data.get("id")
        if not child_id:
            raise RuntimeError(f"Instagram子コンテナIDを取得できませんでした: {data}")
        wait_for_instagram_container(child_id, access_token)
        child_ids.append(child_id)

    parent = request_meta_graph_api(
        "POST",
        f"/{user_id}/media",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": access_token,
        },
    )
    parent_id = parent.get("id")
    if not parent_id:
        raise RuntimeError(f"Instagram親コンテナIDを取得できませんでした: {parent}")

    wait_for_instagram_container(parent_id, access_token)
    published = request_meta_graph_api(
        "POST",
        f"/{user_id}/media_publish",
        data={"creation_id": parent_id, "access_token": access_token},
    )
    media_id = published.get("id")
    if not media_id:
        raise RuntimeError(f"Instagramカルーセル投稿IDを取得できませんでした: {published}")

    media = request_meta_graph_api(
        "GET",
        f"/{media_id}",
        params={
            "fields": "id,media_type,permalink,children{id,media_type,permalink}",
            "access_token": access_token,
        },
    )
    Path("sns_posts").mkdir(exist_ok=True)
    Path("sns_posts/instagram_carousel_last.json").write_text(
        json.dumps(
            {
                "child_ids": child_ids,
                "parent_container_id": parent_id,
                "published": published,
                "media": media,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    media_type = media.get("media_type")
    child_count = len((media.get("children") or {}).get("data", []))
    if media_type != "CAROUSEL_ALBUM" or child_count < 2:
        raise RuntimeError(f"Instagram投稿がカルーセルとして確認できませんでした: {media}")

    return media.get("permalink") or f"Instagram投稿ID: {media_id}"


def linkedin_headers(access_token: str, content_type: Optional[str] = "application/json") -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def post_linkedin_pdf(pdf_path: Path, commentary: str, title: str) -> str:
    credentials = load_linkedin_credentials()
    access_token = credentials["LINKEDIN_ACCESS_TOKEN"]
    owner = get_linkedin_person_urn(access_token)

    initialize = requests.post(
        "https://api.linkedin.com/rest/documents?action=initializeUpload",
        headers=linkedin_headers(access_token),
        json={"initializeUploadRequest": {"owner": owner}},
        timeout=30,
    )
    if initialize.status_code >= 400:
        raise RuntimeError(f"LinkedIn PDF upload初期化に失敗しました: {initialize.status_code} {initialize.text}")

    value = initialize.json().get("value", {})
    upload_url = value.get("uploadUrl")
    document = value.get("document")
    if not upload_url or not document:
        raise RuntimeError(f"LinkedIn PDF upload初期化レスポンスが不正です: {initialize.text}")

    content_type = mimetypes.guess_type(str(pdf_path))[0] or "application/pdf"
    upload = requests.put(
        upload_url,
        headers={"Content-Type": content_type},
        data=pdf_path.read_bytes(),
        timeout=60,
    )
    if upload.status_code >= 400:
        raise RuntimeError(f"LinkedIn PDF uploadに失敗しました: {upload.status_code} {upload.text}")

    post = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=linkedin_headers(access_token),
        json={
            "author": owner,
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": {"media": {"title": title, "id": document}},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        },
        timeout=30,
    )
    if post.status_code >= 400:
        raise RuntimeError(f"LinkedIn PDF投稿に失敗しました: {post.status_code} {post.text}")

    return post.headers.get("x-restli-id") or post.headers.get("X-RestLi-Id") or "LinkedIn PDF投稿に成功しました。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成済みカルーセルをSNSへ投稿します。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    instagram = subparsers.add_parser("instagram")
    instagram.add_argument("--base-url", required=True, help="slide_01.png などを公開しているURL")
    instagram.add_argument("--caption", required=True)
    instagram.add_argument("--count", type=int, default=10)

    linkedin = subparsers.add_parser("linkedin")
    linkedin.add_argument("--pdf", required=True)
    linkedin.add_argument("--text", required=True)
    linkedin.add_argument("--title", default="広告クリエイティブ改善メモ")

    youtube = subparsers.add_parser("youtube")
    youtube.add_argument("--video", required=True, help="アップロードするMP4ファイル")
    youtube.add_argument("--title", required=True, help="YouTubeタイトル")
    youtube.add_argument("--description", default="", help="概要欄")
    youtube.add_argument(
        "--tags",
        nargs="*",
        default=[],
        help="タグ。空白区切り、またはカンマ区切りで指定できます。",
    )
    youtube.add_argument(
        "--privacy-status",
        choices=("public", "unlisted", "private"),
        default="public",
        help="公開設定。初期値は public です。",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "instagram":
        base_url = args.base_url.rstrip("/")
        urls = [f"{base_url}/slide_{index:02d}.png" for index in range(1, args.count + 1)]
        print(post_instagram_carousel(urls, args.caption))
        return 0
    if args.command == "linkedin":
        print(post_linkedin_pdf(Path(args.pdf), args.text, args.title))
        return 0
    if args.command == "youtube":
        from youtube_poster import upload_youtube_short

        print(
            upload_youtube_short(
                Path(args.video),
                args.title,
                args.description,
                args.tags,
                args.privacy_status,
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    load_dotenv(dotenv_path=".env", override=True)
    raise SystemExit(main())
