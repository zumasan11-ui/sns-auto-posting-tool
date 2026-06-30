import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable


ENV_FILE = Path(".env")

SECRET_KEYS = (
    "THREADS_ACCESS_TOKEN",
    "THREADS_ACCESS_TOKEN_EXPIRES_AT",
    "THREADS_LAST_REFRESHED_AT",
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_ACCESS_TOKEN_EXPIRES_AT",
    "INSTAGRAM_LAST_REFRESHED_AT",
    "FACEBOOK_PAGE_ACCESS_TOKEN",
    "FACEBOOK_USER_ACCESS_TOKEN",
    "FACEBOOK_USER_ACCESS_TOKEN_EXPIRES_AT",
    "FACEBOOK_PAGE_ACCESS_TOKEN_EXPIRES_AT",
    "FACEBOOK_LAST_REFRESHED_AT",
    "LINKEDIN_ACCESS_TOKEN",
    "LINKEDIN_ACCESS_TOKEN_EXPIRES_AT",
    "LINKEDIN_REFRESH_TOKEN",
    "LINKEDIN_REFRESH_TOKEN_EXPIRES_AT",
    "LINKEDIN_LAST_REFRESHED_AT",
    "YOUTUBE_ACCESS_TOKEN",
    "YOUTUBE_ACCESS_TOKEN_EXPIRES_AT",
    "YOUTUBE_LAST_REFRESHED_AT",
)


def load_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def mask(value: str) -> None:
    if value:
        print(f"::add-mask::{value}")


def set_secret(key: str, value: str) -> None:
    mask(value)
    subprocess.run(
        ["gh", "secret", "set", key],
        input=value,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def sync(keys: Iterable[str]) -> int:
    if not os.getenv("GH_TOKEN") and not os.getenv("GITHUB_TOKEN"):
        print("GitHub token is not available; skipped secret sync.", file=sys.stderr)
        return 1

    env = load_env(ENV_FILE)
    updated = []
    skipped = []
    for key in keys:
        value = env.get(key, "").strip()
        if not value:
            skipped.append(key)
            continue
        set_secret(key, value)
        updated.append(key)

    print(f"Synced refreshed secrets: {len(updated)}")
    if skipped:
        print("Skipped empty secrets: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(sync(SECRET_KEYS))
