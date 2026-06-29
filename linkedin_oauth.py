import os
import secrets
import sys
import urllib.parse
import webbrowser
import base64
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict

import requests
from dotenv import load_dotenv


ENV_FILE = Path(".env")
DEFAULT_REDIRECT_URI = "http://localhost:3000/callback"
SERVER_HOST = "localhost"
SERVER_PORT = 3000
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
DEFAULT_SCOPES = "openid profile w_member_social offline_access"


def load_linkedin_app() -> Dict[str, str]:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    client_id = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("LINKEDIN_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI
    scopes = os.getenv("LINKEDIN_SCOPES", "").strip() or DEFAULT_SCOPES
    missing = []

    if not client_id:
        missing.append("LINKEDIN_CLIENT_ID")
    if not client_secret:
        missing.append("LINKEDIN_CLIENT_SECRET")

    if missing:
        raise RuntimeError(".env に必要な値がありません: " + ", ".join(missing))

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scopes": scopes,
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


def iso_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0))).isoformat()


def build_authorization_url(client_id: str, redirect_uri: str, scopes: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scopes,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class CallbackHandler(BaseHTTPRequestHandler):
    code = ""
    state = ""
    error = ""
    error_description = ""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        CallbackHandler.code = params.get("code", [""])[0]
        CallbackHandler.state = params.get("state", [""])[0]
        CallbackHandler.error = params.get("error", [""])[0]
        CallbackHandler.error_description = params.get("error_description", [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if CallbackHandler.code:
            body = "LinkedIn OAuth code received. You can close this tab."
        else:
            body = f"LinkedIn OAuth failed: {CallbackHandler.error} {CallbackHandler.error_description}"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


def receive_authorization_code(auth_url: str, expected_state: str) -> str:
    print("Open this URL if it does not open automatically:\n")
    print(auth_url)
    print(f"\nWaiting on {DEFAULT_REDIRECT_URI}\n")
    webbrowser.open(auth_url)

    server = HTTPServer((SERVER_HOST, SERVER_PORT), CallbackHandler)
    server.handle_request()
    server.server_close()

    if CallbackHandler.error:
        raise RuntimeError(
            f"LinkedIn OAuth error: {CallbackHandler.error} {CallbackHandler.error_description}"
        )
    if CallbackHandler.state != expected_state:
        print("警告: LinkedIn OAuth state が一致しません。ローカル取得のため認可コードを優先します。")
    if not CallbackHandler.code:
        raise RuntimeError("LinkedInの認可コードを受け取れませんでした。")

    return CallbackHandler.code


def exchange_access_token(
    client_id: str, client_secret: str, redirect_uri: str, code: str
) -> Dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"LinkedIn access token取得に失敗しました: {response.text}")

    return response.json()


def decode_id_token_subject(id_token: str) -> str:
    parts = id_token.split(".")
    if len(parts) < 2:
        return ""

    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
    except (ValueError, json.JSONDecodeError):
        return ""

    return str(data.get("sub", "")).strip()


def main() -> int:
    try:
        app = load_linkedin_app()
        state = secrets.token_urlsafe(24)
        save_env_values(
            {
                "LINKEDIN_REDIRECT_URI": app["redirect_uri"],
                "LINKEDIN_SCOPES": app["scopes"],
            }
        )
        auth_url = build_authorization_url(
            app["client_id"], app["redirect_uri"], app["scopes"], state
        )
        code = receive_authorization_code(auth_url, state)
        token_data = exchange_access_token(
            app["client_id"], app["client_secret"], app["redirect_uri"], code
        )
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise RuntimeError(f"LinkedIn access tokenレスポンスが不正です: {token_data}")

        updates = {
            "LINKEDIN_ACCESS_TOKEN": access_token,
            "LINKEDIN_LAST_REFRESHED_AT": datetime.now(timezone.utc).isoformat(),
        }
        if token_data.get("refresh_token"):
            updates["LINKEDIN_REFRESH_TOKEN"] = str(token_data["refresh_token"])
        if token_data.get("expires_in"):
            updates["LINKEDIN_ACCESS_TOKEN_EXPIRES_AT"] = iso_at(int(token_data["expires_in"]))
        if token_data.get("refresh_token_expires_in"):
            updates["LINKEDIN_REFRESH_TOKEN_EXPIRES_AT"] = iso_at(int(token_data["refresh_token_expires_in"]))
        subject = decode_id_token_subject(str(token_data.get("id_token", "")))
        if subject:
            updates["LINKEDIN_PERSON_URN"] = f"urn:li:person:{subject}"

        save_env_values(updates)
        print("\nLinkedIn Access Tokenを .env に保存しました。")
        if subject:
            print(f"LINKEDIN_PERSON_URN=urn:li:person:{subject}")
        print(f"expires_in={token_data.get('expires_in')}")
        return 0
    except KeyboardInterrupt:
        print("\nキャンセルしました。")
        return 130
    except Exception as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
