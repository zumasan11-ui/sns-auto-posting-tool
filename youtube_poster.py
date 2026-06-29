import argparse
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv


YOUTUBE_REQUIRED_ENV_KEYS = (
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REDIRECT_URI",
    "YOUTUBE_REFRESH_TOKEN",
)
YOUTUBE_SCOPES = ("https://www.googleapis.com/auth/youtube.upload",)
DEFAULT_CATEGORY_ID = "22"
DEFAULT_PRIVACY_STATUS = "public"


def load_youtube_credentials() -> Dict[str, str]:
    try:
        from token_refresh import ensure_token_fresh

        ensure_token_fresh("youtube")
    except Exception:
        pass

    load_dotenv(dotenv_path=".env", override=True)
    credentials = {key: os.getenv(key, "").strip() for key in YOUTUBE_REQUIRED_ENV_KEYS}
    missing = [key for key, value in credentials.items() if not value]

    if missing:
        raise RuntimeError(
            ".env に必要な値がありません: "
            + ", ".join(missing)
            + "\nyoutube_oauth.py で初回認証を行い、YOUTUBE_REFRESH_TOKEN を保存してください。"
        )

    return credentials


def normalize_tags(tags: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    for raw_tag in tags or []:
        for tag in raw_tag.split(","):
            value = tag.strip()
            if value and value not in normalized:
                normalized.append(value)

    if "Shorts" not in normalized:
        normalized.append("Shorts")
    return normalized


def ensure_shorts_metadata(title: str, description: str, tags: List[str]) -> Dict[str, Any]:
    clean_title = title.strip()
    clean_description = description.strip()

    if not clean_title:
        raise RuntimeError("YouTube投稿には --title が必要です。")
    if "#Shorts" not in clean_title and "#Shorts" not in clean_description:
        clean_description = (clean_description + "\n\n#Shorts").strip()

    return {
        "title": clean_title,
        "description": clean_description,
        "tags": normalize_tags(tags),
        "categoryId": DEFAULT_CATEGORY_ID,
    }


def build_youtube_client(credentials: Dict[str, str]) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    google_credentials = Credentials(
        token=None,
        refresh_token=credentials["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=credentials["YOUTUBE_CLIENT_ID"],
        client_secret=credentials["YOUTUBE_CLIENT_SECRET"],
        scopes=YOUTUBE_SCOPES,
    )
    google_credentials.refresh(Request())
    return build("youtube", "v3", credentials=google_credentials)


def upload_youtube_short(
    video_path: Path,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy_status: str = DEFAULT_PRIVACY_STATUS,
) -> str:
    if not video_path.exists():
        raise RuntimeError(f"MP4ファイルが見つかりません: {video_path}")
    if video_path.suffix.lower() != ".mp4":
        raise RuntimeError("YouTube Shorts投稿には .mp4 ファイルを指定してください。")

    credentials = load_youtube_credentials()
    youtube = build_youtube_client(credentials)
    snippet = ensure_shorts_metadata(title, description, tags or [])
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": snippet,
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=-1,
        resumable=True,
    )

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = None
        while response is None:
            _status, response = request.next_chunk()
    except HttpError as error:
        raise RuntimeError(f"YouTubeアップロードに失敗しました: {error}") from error

    video_id = response.get("id") if isinstance(response, dict) else None
    if not video_id:
        raise RuntimeError(f"YouTube動画IDを取得できませんでした: {response}")

    return f"https://www.youtube.com/shorts/{video_id}"


def set_youtube_thumbnail(video_id: str, thumbnail_path: Path) -> str:
    if not video_id.strip():
        raise RuntimeError("サムネ設定には --video-id が必要です。")
    if not thumbnail_path.exists():
        raise RuntimeError(f"サムネ画像が見つかりません: {thumbnail_path}")
    if thumbnail_path.stat().st_size > 2 * 1024 * 1024:
        raise RuntimeError("YouTubeサムネ画像は2MB以内にしてください。")

    content_type = mimetypes.guess_type(str(thumbnail_path))[0] or "image/png"
    if content_type not in ("image/jpeg", "image/png", "application/octet-stream"):
        raise RuntimeError("YouTubeサムネ画像はJPEGまたはPNGにしてください。")

    credentials = load_youtube_credentials()
    youtube = build_youtube_client(credentials)
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(thumbnail_path), mimetype=content_type, resumable=False)

    try:
        youtube.thumbnails().set(videoId=video_id.strip(), media_body=media).execute()
    except HttpError as error:
        raise RuntimeError(f"YouTubeサムネ設定に失敗しました: {error}") from error

    return f"https://www.youtube.com/shorts/{video_id.strip()}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ローカルMP4をYouTube Shortsとして投稿します。")
    parser.add_argument("--video", help="アップロードするMP4ファイル")
    parser.add_argument("--title", help="YouTubeタイトル")
    parser.add_argument("--description", default="", help="概要欄")
    parser.add_argument(
        "--tags",
        nargs="*",
        default=[],
        help="タグ。空白区切り、またはカンマ区切りで指定できます。",
    )
    parser.add_argument(
        "--privacy-status",
        choices=("public", "unlisted", "private"),
        default=DEFAULT_PRIVACY_STATUS,
        help="公開設定。初期値は public です。",
    )
    parser.add_argument("--thumbnail", help="投稿後に設定するサムネ画像。JPEG/PNG、2MB以内。")
    parser.add_argument("--video-id", help="既存動画へサムネだけ設定する時のYouTube動画ID。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.video_id:
        if not args.thumbnail:
            raise RuntimeError("--video-id を使う時は --thumbnail も指定してください。")
        print(set_youtube_thumbnail(args.video_id, Path(args.thumbnail)))
        return 0

    if not args.video or not args.title:
        raise RuntimeError("アップロードには --video と --title が必要です。")

    url = upload_youtube_short(
        Path(args.video),
        args.title,
        args.description,
        args.tags,
        args.privacy_status,
    )
    if args.thumbnail:
        video_id = url.rstrip("/").split("/")[-1]
        set_youtube_thumbnail(video_id, Path(args.thumbnail))
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
