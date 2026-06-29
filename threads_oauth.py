import os
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import requests
from dotenv import load_dotenv


ENV_FILE = Path(".env")
REDIRECT_URI = "http://localhost:8765/callback"
SERVER_HOST = "localhost"
SERVER_PORT = 8765
AUTH_URL = "https://www.threads.com/oauth/authorize"
TOKEN_URL = "https://graph.threads.net/oauth/access_token"
LONG_LIVED_TOKEN_URL = "https://graph.threads.net/access_token"
SCOPES = ("threads_basic", "threads_content_publish")


def load_threads_app() -> Dict[str, str]:
    load_dotenv()
    app_id = os.getenv("THREADS_APP_ID", "").strip()
    app_secret = os.getenv("THREADS_APP_SECRET", "").strip()
    redirect_uri = os.getenv("THREADS_REDIRECT_URI", "").strip() or REDIRECT_URI
    missing = []

    if not app_id:
        missing.append("THREADS_APP_ID")
    if not app_secret:
        missing.append("THREADS_APP_SECRET")

    if missing:
        raise RuntimeError(".env に必要な値がありません: " + ", ".join(missing))

    return {"app_id": app_id, "app_secret": app_secret, "redirect_uri": redirect_uri}


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


def build_authorization_url(app_id: str, redirect_uri: str) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(SCOPES),
        "response_type": "code",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class CallbackHandler(BaseHTTPRequestHandler):
    code = ""
    error = ""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        CallbackHandler.code = params.get("code", [""])[0]
        CallbackHandler.error = params.get("error", [""])[0] or params.get(
            "error_message", [""]
        )[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if CallbackHandler.code:
            body = "Threads OAuth code received. You can close this tab."
        else:
            body = f"Threads OAuth failed: {CallbackHandler.error}"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


def receive_authorization_code(auth_url: str, redirect_uri: str) -> str:
    print("Open this URL in Firefox if it does not open automatically:\n")
    print(auth_url)
    print(f"\nOAuth Redirect URI: {redirect_uri}")
    print(f"Local callback listener: http://{SERVER_HOST}:{SERVER_PORT}/callback\n")
    webbrowser.open(auth_url)

    server = HTTPServer((SERVER_HOST, SERVER_PORT), CallbackHandler)
    server.handle_request()
    server.server_close()

    if CallbackHandler.error:
        raise RuntimeError(f"Threads OAuth error: {CallbackHandler.error}")
    if not CallbackHandler.code:
        raise RuntimeError("認可コードを受け取れませんでした。")

    return CallbackHandler.code


def exchange_short_lived_token(
    app_id: str, app_secret: str, redirect_uri: str, code: str
) -> Dict[str, str]:
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"短期トークン交換に失敗しました: {response.text}")

    data = response.json()
    access_token = data.get("access_token")
    user_id = data.get("user_id")
    if not access_token or not user_id:
        raise RuntimeError(f"短期トークン交換レスポンスが不正です: {data}")

    return {"access_token": access_token, "user_id": str(user_id)}


def iso_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0))).isoformat()


def exchange_long_lived_token(app_secret: str, short_lived_token: str) -> Dict[str, Any]:
    response = requests.get(
        LONG_LIVED_TOKEN_URL,
        params={
            "grant_type": "th_exchange_token",
            "client_secret": app_secret,
            "access_token": short_lived_token,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"長期トークン交換に失敗しました: {response.text}")

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"長期トークン交換レスポンスが不正です: {data}")

    return data


def main() -> int:
    try:
        app = load_threads_app()

        auth_url = build_authorization_url(app["app_id"], app["redirect_uri"])
        code = receive_authorization_code(auth_url, app["redirect_uri"])
        short_lived = exchange_short_lived_token(
            app["app_id"], app["app_secret"], app["redirect_uri"], code
        )
        long_lived = exchange_long_lived_token(
            app["app_secret"], short_lived["access_token"]
        )
        updates = {
            "THREADS_REDIRECT_URI": app["redirect_uri"],
            "THREADS_USER_ID": short_lived["user_id"],
            "THREADS_ACCESS_TOKEN": str(long_lived["access_token"]),
            "THREADS_LAST_REFRESHED_AT": datetime.now(timezone.utc).isoformat(),
        }
        if long_lived.get("expires_in"):
            updates["THREADS_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(int(long_lived["expires_in"]))
        save_env_values(updates)

        print("\nThreads OAuth complete.")
        print(f"THREADS_USER_ID={short_lived['user_id']}")
        return 0
    except KeyboardInterrupt:
        print("\nキャンセルしました。")
        return 130
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
