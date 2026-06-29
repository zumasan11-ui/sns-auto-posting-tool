import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Literal, Optional

from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

try:
    import tweepy
except ModuleNotFoundError:
    tweepy = None


REQUIRED_ENV_KEYS = (
    "API_KEY",
    "API_SECRET",
    "ACCESS_TOKEN",
    "ACCESS_TOKEN_SECRET",
)
THREADS_REQUIRED_ENV_KEYS = (
    "THREADS_USER_ID",
    "THREADS_ACCESS_TOKEN",
)
INSTAGRAM_REQUIRED_ENV_KEYS = (
    "INSTAGRAM_USER_ID",
    "INSTAGRAM_ACCESS_TOKEN",
)
FACEBOOK_REQUIRED_ENV_KEYS = (
    "FACEBOOK_PAGE_ID",
    "FACEBOOK_PAGE_ACCESS_TOKEN",
)
LINKEDIN_REQUIRED_ENV_KEYS = (
    "LINKEDIN_ACCESS_TOKEN",
)
Platform = Literal["x", "threads", "instagram", "facebook", "linkedin", "youtube"]
JST = ZoneInfo("Asia/Tokyo")
QUEUE_DIR = Path("sns_posts")
QUEUE_FILE = QUEUE_DIR / "x_queue.json"
LOG_FILE = QUEUE_DIR / "x_post_log.jsonl"
THREADS_GRAPH_BASE_URL = "https://graph.threads.net/v1.0"
META_GRAPH_BASE_URL = "https://graph.facebook.com/v23.0"


def require_dotenv() -> Any:
    if load_dotenv is None:
        raise RuntimeError(
            "python-dotenv がインストールされていません。先に pip install -r requirements.txt を実行してください。"
        )

    return load_dotenv


def load_env_values(keys: tuple[str, ...], service_name: str) -> Dict[str, str]:
    dotenv_loader = require_dotenv()
    dotenv_loader(override=True)
    credentials = {key: os.getenv(key, "").strip() for key in keys}
    missing = [key for key, value in credentials.items() if not value]

    if missing:
        raise RuntimeError(
            ".env に必要な値がありません: "
            + ", ".join(missing)
            + f"\n.env.example を見ながら .env に {service_name} の値を入れてください。"
        )

    return credentials


def load_credentials() -> Dict[str, str]:
    return load_env_values(REQUIRED_ENV_KEYS, "X")


def load_threads_credentials() -> Dict[str, str]:
    refresh_platform_token("threads")
    return load_env_values(THREADS_REQUIRED_ENV_KEYS, "Threads")


def load_instagram_credentials() -> Dict[str, str]:
    refresh_platform_token("instagram")
    return load_env_values(INSTAGRAM_REQUIRED_ENV_KEYS, "Instagram")


def load_facebook_credentials() -> Dict[str, str]:
    refresh_platform_token("facebook")
    return load_env_values(FACEBOOK_REQUIRED_ENV_KEYS, "Facebook")


def load_linkedin_credentials() -> Dict[str, str]:
    refresh_platform_token("linkedin")
    return load_env_values(LINKEDIN_REQUIRED_ENV_KEYS, "LinkedIn")


def refresh_platform_token(platform: str) -> None:
    try:
        from token_refresh import ensure_token_fresh

        ensure_token_fresh(platform)
    except Exception as error:
        print(f"{platform} token refresh skipped: {error}", file=sys.stderr)


def read_post_text(arg_text: Optional[str]) -> str:
    if arg_text:
        return arg_text.strip()

    print("投稿文を入力してください。複数行にしたい場合は、最後に空行を入力してください。")
    print("キャンセルする場合は Ctrl+C を押してください。\n")

    lines: List[str] = []
    while True:
        line = input("> ")
        if line == "":
            break
        lines.append(line)

    return "\n".join(lines).strip()


def confirm_action(text: str, prompt: str) -> bool:
    print("\n--- 投稿前確認 ---")
    print(text)
    print("----------------")
    answer = input(f"{prompt} yes と入力した時だけ実行します: ").strip()
    return answer == "yes"


def ensure_queue_dir() -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


def load_queue() -> List[Dict[str, Any]]:
    if not QUEUE_FILE.exists():
        return []

    with QUEUE_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise RuntimeError(f"{QUEUE_FILE} の形式が不正です。配列のJSONにしてください。")

    return data


def save_queue(items: List[Dict[str, Any]]) -> None:
    ensure_queue_dir()
    with QUEUE_FILE.open("w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)
        file.write("\n")


def append_log(entry: Dict[str, Any]) -> None:
    ensure_queue_dir()
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_local_datetime(value: str) -> datetime:
    normalized = value.strip().replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, pattern).replace(tzinfo=JST)
        except ValueError:
            pass

    raise ValueError("日時は '2026-06-28 09:00' の形式で指定してください。")


def default_next_day_datetime() -> datetime:
    tomorrow = datetime.now(JST) + timedelta(days=1)
    return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)


def validate_post_text(text: str, platform: Platform = "x") -> Optional[str]:
    if not text:
        return "投稿文が空です。処理を終了します。"

    max_lengths = {
        "x": 280,
        "threads": 500,
        "instagram": 2200,
        "facebook": 63206,
        "linkedin": 3000,
        "youtube": 5000,
    }
    platform_names = {
        "x": "X",
        "threads": "Threads",
        "instagram": "Instagram",
        "facebook": "Facebook",
        "linkedin": "LinkedIn",
        "youtube": "YouTube",
    }
    max_length = max_lengths[platform]
    platform_name = platform_names[platform]

    if len(text) > max_length:
        return dedent(
            f"""
            投稿文が{max_length}文字を超えています: {len(text)}文字
            通常の{platform_name}投稿では{max_length}文字以内にしてください。
            """
        ).strip()

    return None


def schedule_post(text: str, scheduled_at: datetime) -> Dict[str, Any]:
    if scheduled_at <= datetime.now(JST):
        raise RuntimeError("予約日時は現在より未来にしてください。")

    item = {
        "id": f"x_{uuid.uuid4().hex[:12]}",
        "platform": "x",
        "status": "scheduled",
        "text": text,
        "scheduled_at": scheduled_at.isoformat(),
        "created_at": datetime.now(JST).isoformat(),
        "posted_at": None,
        "post_url": None,
        "error": None,
    }

    items = load_queue()
    items.append(item)
    save_queue(items)
    return item


def list_scheduled_posts() -> None:
    items = [item for item in load_queue() if item.get("status") == "scheduled"]

    if not items:
        print("予約中のX投稿はありません。")
        return

    print("予約中のX投稿:")
    for item in sorted(items, key=lambda post: post.get("scheduled_at", "")):
        preview = item.get("text", "").replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        print(f"- {item.get('id')} / {item.get('scheduled_at')} / {preview}")


def run_due_posts() -> int:
    items = load_queue()
    now = datetime.now(JST)
    due_items = [
        item
        for item in items
        if item.get("status") == "scheduled"
        and datetime.fromisoformat(item["scheduled_at"]) <= now
    ]

    if not due_items:
        print("実行対象の予約投稿はありません。")
        return 0

    credentials = load_credentials()
    client = build_client(credentials)
    posted_count = 0

    for item in due_items:
        post_id = item.get("id")
        try:
            validation_error = validate_post_text(item.get("text", ""))
            if validation_error:
                raise RuntimeError(validation_error)

            url = create_post(client, item["text"])
            item["status"] = "posted"
            item["posted_at"] = datetime.now(JST).isoformat()
            item["post_url"] = url
            item["error"] = None
            posted_count += 1
            append_log(
                {
                    "id": post_id,
                    "platform": "x",
                    "status": "posted",
                    "scheduled_at": item.get("scheduled_at"),
                    "posted_at": item.get("posted_at"),
                    "post_url": url,
                }
            )
            print(f"投稿しました: {post_id} {url}")
        except Exception as error:
            item["status"] = "failed"
            item["error"] = str(error)
            append_log(
                {
                    "id": post_id,
                    "platform": "x",
                    "status": "failed",
                    "scheduled_at": item.get("scheduled_at"),
                    "failed_at": datetime.now(JST).isoformat(),
                    "error": str(error),
                }
            )
            print(f"投稿に失敗しました: {post_id} {error}", file=sys.stderr)

    save_queue(items)

    return posted_count


def require_tweepy() -> Any:
    if tweepy is None:
        raise RuntimeError(
            "tweepy がインストールされていません。先に pip install -r requirements.txt を実行してください。"
        )

    return tweepy


def build_client(credentials: Dict[str, str]) -> Any:
    tweepy_module = require_tweepy()
    return tweepy_module.Client(
        consumer_key=credentials["API_KEY"],
        consumer_secret=credentials["API_SECRET"],
        access_token=credentials["ACCESS_TOKEN"],
        access_token_secret=credentials["ACCESS_TOKEN_SECRET"],
        wait_on_rate_limit=False,
    )


def format_tweepy_error(error: Exception) -> str:
    parts = [f"{error.__class__.__name__}: {error}"]

    response = getattr(error, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        reason = getattr(response, "reason", None)
        if status_code:
            parts.append(f"HTTPステータス: {status_code} {reason or ''}".strip())

        text = getattr(response, "text", None)
        if text:
            parts.append(f"APIレスポンス: {text}")

    api_errors = getattr(error, "api_errors", None)
    if api_errors:
        parts.append(f"APIエラー詳細: {api_errors}")

    api_codes = getattr(error, "api_codes", None)
    if api_codes:
        parts.append(f"APIエラーコード: {api_codes}")

    return "\n".join(parts)


def create_post(client: Any, text: str) -> str:
    response = client.create_tweet(text=text)
    tweet_id = response.data.get("id") if response.data else None

    if not tweet_id:
        raise RuntimeError(f"投稿IDを取得できませんでした。APIレスポンス: {response}")

    return f"https://x.com/i/web/status/{tweet_id}"


def require_requests() -> Any:
    try:
        import requests
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "requests がインストールされていません。先に pip install -r requirements.txt を実行してください。"
        ) from error

    return requests


def format_meta_error(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        body = response.text

    return f"HTTP {response.status_code}: {body}"


def request_threads_api(method: str, path: str, data: Dict[str, str]) -> Dict[str, Any]:
    requests = require_requests()
    response = requests.request(
        method,
        f"{THREADS_GRAPH_BASE_URL}{path}",
        data=data,
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(format_meta_error(response))

    return response.json()


def request_meta_graph_api(
    method: str,
    path: str,
    data: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    requests = require_requests()
    response = requests.request(
        method,
        f"{META_GRAPH_BASE_URL}{path}",
        data=data,
        params=params,
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(format_meta_error(response))

    return response.json()


def get_threads_post_permalink(post_id: str, access_token: str) -> Optional[str]:
    requests = require_requests()
    response = requests.get(
        f"{THREADS_GRAPH_BASE_URL}/{post_id}",
        params={"fields": "permalink", "access_token": access_token},
        timeout=30,
    )

    if response.status_code >= 400:
        return None

    data = response.json()
    permalink = data.get("permalink")
    return permalink if isinstance(permalink, str) else None


def create_threads_post(credentials: Dict[str, str], text: str) -> str:
    user_id = credentials["THREADS_USER_ID"]
    access_token = credentials["THREADS_ACCESS_TOKEN"]
    container = request_threads_api(
        "POST",
        f"/{user_id}/threads",
        {
            "media_type": "TEXT",
            "text": text,
            "access_token": access_token,
        },
    )
    creation_id = container.get("id")

    if not creation_id:
        raise RuntimeError(f"Threadsの投稿コンテナIDを取得できませんでした。APIレスポンス: {container}")

    published = request_threads_api(
        "POST",
        f"/{user_id}/threads_publish",
        {
            "creation_id": creation_id,
            "access_token": access_token,
        },
    )
    post_id = published.get("id")

    if not post_id:
        raise RuntimeError(f"Threadsの投稿IDを取得できませんでした。APIレスポンス: {published}")

    return get_threads_post_permalink(post_id, access_token) or f"Threads投稿ID: {post_id}"


def validate_image_url(image_url: Optional[str]) -> str:
    if not image_url:
        raise RuntimeError("Instagram投稿には --image-url が必要です。公開HTTPS画像URLを指定してください。")

    if not image_url.startswith("https://"):
        raise RuntimeError("Instagram投稿の --image-url は https:// で始まる公開画像URLにしてください。")

    return image_url


def get_instagram_media_permalink(media_id: str, access_token: str) -> Optional[str]:
    data = request_meta_graph_api(
        "GET",
        f"/{media_id}",
        params={"fields": "permalink", "access_token": access_token},
    )
    permalink = data.get("permalink")
    return permalink if isinstance(permalink, str) else None


def wait_for_instagram_container(creation_id: str, access_token: str) -> None:
    for _ in range(10):
        data = request_meta_graph_api(
            "GET",
            f"/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
        )
        status_code = data.get("status_code")
        if status_code in ("FINISHED", "PUBLISHED"):
            return
        if status_code == "ERROR":
            raise RuntimeError(f"Instagramの画像コンテナ作成に失敗しました。APIレスポンス: {data}")
        time.sleep(2)

    raise RuntimeError("Instagramの画像コンテナ作成が時間内に完了しませんでした。")


def create_instagram_image_post(
    credentials: Dict[str, str], image_url: str, caption: str
) -> str:
    user_id = credentials["INSTAGRAM_USER_ID"]
    access_token = credentials["INSTAGRAM_ACCESS_TOKEN"]
    container = request_meta_graph_api(
        "POST",
        f"/{user_id}/media",
        data={
            "image_url": image_url,
            "caption": caption,
            "access_token": access_token,
        },
    )
    creation_id = container.get("id")

    if not creation_id:
        raise RuntimeError(f"Instagramの投稿コンテナIDを取得できませんでした。APIレスポンス: {container}")

    wait_for_instagram_container(creation_id, access_token)

    published = request_meta_graph_api(
        "POST",
        f"/{user_id}/media_publish",
        data={
            "creation_id": creation_id,
            "access_token": access_token,
        },
    )
    media_id = published.get("id")

    if not media_id:
        raise RuntimeError(f"Instagramの投稿IDを取得できませんでした。APIレスポンス: {published}")

    return get_instagram_media_permalink(media_id, access_token) or f"Instagram投稿ID: {media_id}"


def create_facebook_text_post(credentials: Dict[str, str], message: str) -> str:
    page_id = credentials["FACEBOOK_PAGE_ID"]
    access_token = credentials["FACEBOOK_PAGE_ACCESS_TOKEN"]
    data = request_meta_graph_api(
        "POST",
        f"/{page_id}/feed",
        data={"message": message, "access_token": access_token},
    )
    post_id = data.get("id")

    if not post_id:
        raise RuntimeError(f"Facebookページ投稿IDを取得できませんでした。APIレスポンス: {data}")

    return f"https://www.facebook.com/{post_id}"


def create_facebook_photo_post(
    credentials: Dict[str, str], image_url: str, caption: str
) -> str:
    page_id = credentials["FACEBOOK_PAGE_ID"]
    access_token = credentials["FACEBOOK_PAGE_ACCESS_TOKEN"]
    data = request_meta_graph_api(
        "POST",
        f"/{page_id}/photos",
        data={"url": image_url, "caption": caption, "access_token": access_token},
    )
    post_id = data.get("post_id") or data.get("id")

    if not post_id:
        raise RuntimeError(f"Facebookページ画像投稿IDを取得できませんでした。APIレスポンス: {data}")

    return f"https://www.facebook.com/{post_id}"


def request_linkedin_api(
    method: str,
    url: str,
    access_token: str,
    json_data: Optional[Dict[str, Any]] = None,
) -> Any:
    requests = require_requests()
    response = requests.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
        json=json_data,
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

    if response.text:
        try:
            return response.json()
        except ValueError:
            return response

    return response


def get_linkedin_person_urn(access_token: str) -> str:
    manual_urn = os.getenv("LINKEDIN_PERSON_URN", "").strip()
    if manual_urn:
        return manual_urn

    requests = require_requests()
    userinfo_response = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if userinfo_response.status_code < 400:
        sub = str(userinfo_response.json().get("sub", "")).strip()
        if sub:
            return f"urn:li:person:{sub}"

    me_response = requests.get(
        "https://api.linkedin.com/v2/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if me_response.status_code < 400:
        member_id = str(me_response.json().get("id", "")).strip()
        if member_id:
            return f"urn:li:person:{member_id}"

    raise RuntimeError(
        "LinkedInの個人IDを取得できませんでした。"
        "OAuthスコープに w_member_social に加えて openid profile が必要な場合があります。"
    )


def create_linkedin_text_post(credentials: Dict[str, str], text: str) -> str:
    access_token = credentials["LINKEDIN_ACCESS_TOKEN"]
    author = get_linkedin_person_urn(access_token)
    response = request_linkedin_api(
        "POST",
        "https://api.linkedin.com/v2/ugcPosts",
        access_token,
        {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
            },
        },
    )
    post_id = getattr(response, "headers", {}).get("X-RestLi-Id", "")
    return post_id or "LinkedIn投稿に成功しました。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="X APIを使って、自分のXアカウントへテキスト投稿・予約投稿します。"
    )
    parser.add_argument(
        "-t",
        "--text",
        help="投稿文をコマンドで直接渡します。指定しない場合は対話形式で入力します。",
    )
    parser.add_argument(
        "--platform",
        choices=("x", "threads", "instagram", "facebook", "linkedin", "youtube"),
        default="x",
        help="投稿先を指定します。初期値は x です。",
    )
    parser.add_argument(
        "--image-url",
        help="Instagramへ画像投稿する時の公開HTTPS画像URLです。",
    )
    parser.add_argument(
        "--video",
        help="YouTubeへアップロードするMP4ファイルです。",
    )
    parser.add_argument(
        "--title",
        help="YouTubeタイトルです。",
    )
    parser.add_argument(
        "--tags",
        nargs="*",
        default=[],
        help="YouTubeタグです。空白区切り、またはカンマ区切りで指定できます。",
    )
    parser.add_argument(
        "--privacy-status",
        choices=("public", "unlisted", "private"),
        default="public",
        help="YouTubeの公開設定です。初期値は public です。",
    )
    parser.add_argument(
        "--schedule-next-day",
        action="store_true",
        help="投稿文を翌日9:00に予約します。",
    )
    parser.add_argument(
        "--schedule-at",
        help="投稿文を指定日時に予約します。例: '2026-06-28 09:00'",
    )
    parser.add_argument(
        "--list-scheduled",
        action="store_true",
        help="予約中のX投稿を一覧表示します。",
    )
    parser.add_argument(
        "--run-due",
        action="store_true",
        help="予約時刻を過ぎたX投稿を実行します。cronやlaunchdから定期実行してください。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.list_scheduled:
            list_scheduled_posts()
            return 0

        if args.run_due:
            run_due_posts()
            return 0

        text = read_post_text(args.text)
        validation_error = validate_post_text(text, args.platform)

        if validation_error:
            print(validation_error)
            return 1

        if args.schedule_next_day or args.schedule_at:
            if args.platform != "x":
                print("予約投稿はまだXのみ対応です。Threads/Instagramは即時投稿からテストしてください。")
                return 1

            scheduled_at = (
                parse_local_datetime(args.schedule_at)
                if args.schedule_at
                else default_next_day_datetime()
            )
            print(f"\n予約日時: {scheduled_at.isoformat()}")
            if not confirm_action(text, "この内容で予約しますか？"):
                print("予約をキャンセルしました。")
                return 0

            item = schedule_post(text, scheduled_at)
            print("\n予約しました。")
            print(f"予約ID: {item['id']}")
            print(f"予約日時: {item['scheduled_at']}")
            print("実行するには、予約日時以降に python main.py --run-due を実行してください。")
            return 0

        if args.platform == "threads":
            credentials = load_threads_credentials()
        elif args.platform == "instagram":
            credentials = load_instagram_credentials()
            image_url = validate_image_url(args.image_url)
        elif args.platform == "facebook":
            credentials = load_facebook_credentials()
            image_url = args.image_url
            if image_url:
                image_url = validate_image_url(image_url)
        elif args.platform == "linkedin":
            credentials = load_linkedin_credentials()
        elif args.platform == "youtube":
            if not args.video:
                raise RuntimeError("YouTube投稿には --video でMP4ファイルを指定してください。")
            if not args.title:
                raise RuntimeError("YouTube投稿には --title を指定してください。")
            credentials = {}
        else:
            credentials = load_credentials()

        if not confirm_action(text, "この内容で投稿しますか？"):
            print("投稿をキャンセルしました。")
            return 0

        if args.platform == "threads":
            url = create_threads_post(credentials, text)
        elif args.platform == "instagram":
            url = create_instagram_image_post(credentials, image_url, text)
        elif args.platform == "facebook":
            if image_url:
                url = create_facebook_photo_post(credentials, image_url, text)
            else:
                url = create_facebook_text_post(credentials, text)
        elif args.platform == "linkedin":
            url = create_linkedin_text_post(credentials, text)
        elif args.platform == "youtube":
            from youtube_poster import upload_youtube_short

            url = upload_youtube_short(
                Path(args.video),
                args.title,
                text,
                args.tags,
                args.privacy_status,
            )
        else:
            client = build_client(credentials)
            url = create_post(client, text)
        print("\n投稿に成功しました。")
        print(f"投稿URL: {url}")
        return 0

    except KeyboardInterrupt:
        print("\nキャンセルしました。")
        return 130
    except Exception as error:
        if tweepy is not None and isinstance(error, tweepy.TweepyException):
            print("\nX APIエラーが発生しました。", file=sys.stderr)
            print(format_tweepy_error(error), file=sys.stderr)
            print(
                "\n確認ポイント: APIキー、アクセストークン、アプリ権限(Read and write)、"
                "Developer Portalのプラン/利用上限を確認してください。",
                file=sys.stderr,
            )
            return 1

        print(f"\nエラーが発生しました: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
