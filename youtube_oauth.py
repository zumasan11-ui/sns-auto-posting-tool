import argparse
import os
import sys
import urllib.parse
import webbrowser
from datetime import timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv


ENV_FILE = Path(".env")
DEFAULT_REDIRECT_URI = "http://localhost:8080/callback"
SERVER_HOST = "localhost"
YOUTUBE_SCOPES = ("https://www.googleapis.com/auth/youtube.upload",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube Data API v3用の初回OAuth認証を行い、refresh_tokenを.envへ保存します。"
    )
    return parser.parse_args()


def load_youtube_app() -> Dict[str, str]:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("YOUTUBE_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI
    missing = []

    if not client_id:
        missing.append("YOUTUBE_CLIENT_ID")
    if not client_secret:
        missing.append("YOUTUBE_CLIENT_SECRET")

    if missing:
        raise RuntimeError(".env に必要な値がありません: " + ", ".join(missing))

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


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


def build_client_config(client_id: str, client_secret: str) -> Dict[str, Dict[str, str]]:
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def build_flow(client_id: str, client_secret: str, redirect_uri: str) -> Any:
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        build_client_config(client_id, client_secret),
        scopes=YOUTUBE_SCOPES,
        redirect_uri=redirect_uri,
    )
    return flow


class CallbackHandler(BaseHTTPRequestHandler):
    code = ""
    state = ""
    error = ""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        CallbackHandler.code = params.get("code", [""])[0]
        CallbackHandler.state = params.get("state", [""])[0]
        CallbackHandler.error = params.get("error", [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if CallbackHandler.code:
            body = "YouTube OAuth code received. You can close this tab."
        else:
            body = f"YouTube OAuth failed: {CallbackHandler.error}"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


def receive_authorization_code(auth_url: str, redirect_uri: str, expected_state: str) -> str:
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port
    if parsed.hostname not in ("localhost", "127.0.0.1") or port is None:
        raise RuntimeError("YOUTUBE_REDIRECT_URI は http://localhost:PORT/callback 形式にしてください。")

    print("Open this URL if it does not open automatically:\n")
    print(auth_url)
    print(f"\nWaiting on {redirect_uri}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    server = HTTPServer((SERVER_HOST, port), CallbackHandler)
    server.handle_request()
    server.server_close()

    if CallbackHandler.error:
        raise RuntimeError(f"YouTube OAuth error: {CallbackHandler.error}")
    if CallbackHandler.state != expected_state:
        raise RuntimeError("YouTube OAuth state が一致しません。もう一度実行してください。")
    if not CallbackHandler.code:
        raise RuntimeError("YouTubeの認可コードを受け取れませんでした。")

    return CallbackHandler.code


def main() -> int:
    try:
        parse_args()
        app = load_youtube_app()
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        flow = build_flow(app["client_id"], app["client_secret"], app["redirect_uri"])
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        save_env_values({"YOUTUBE_REDIRECT_URI": app["redirect_uri"]})
        code = receive_authorization_code(auth_url, app["redirect_uri"], state)
        flow.fetch_token(code=code)

        refresh_token = flow.credentials.refresh_token
        if not refresh_token:
            raise RuntimeError(
                "refresh_token を取得できませんでした。Googleの認可画面で同意済みの場合は、"
                "Googleアカウントのサードパーティ連携を解除してから再実行してください。"
            )

        updates = {"YOUTUBE_REFRESH_TOKEN": refresh_token}
        if flow.credentials.token:
            updates["YOUTUBE_ACCESS_TOKEN"] = flow.credentials.token
        if flow.credentials.expiry:
            updates["YOUTUBE_ACCESS_TOKEN_EXPIRES_AT"] = flow.credentials.expiry.replace(tzinfo=timezone.utc).isoformat()
        save_env_values(updates)
        print("\nYouTube Refresh Tokenを .env に保存しました。")
        return 0
    except KeyboardInterrupt:
        print("\nキャンセルしました。")
        return 130
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
