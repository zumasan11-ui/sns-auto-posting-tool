#!/usr/bin/env python3
"""Fail when staged or tracked files contain local secrets.

The scanner intentionally prints only key names and file locations, never the
secret values themselves.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


SECRET_KEY_RE = re.compile(
    r"(SECRET|TOKEN|PRIVATE_KEY|CREDENTIALS_JSON|API_KEY|API_SECRET|"
    r"ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|APP_SECRET)$"
)
TOKEN_PATTERNS = {
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{25,}"),
    "google_oauth_token": re.compile(r"ya29\.[0-9A-Za-z_-]+"),
    "facebook_token": re.compile(r"EAA[A-Za-z0-9]{80,}"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
}
IGNORED_ENV_KEYS = {
    "GOOGLE_SHEETS_CREDENTIALS_FILE",
    "GOOGLE_SHEETS_DEFAULT_SHEET",
    "LINKEDIN_SCOPES",
    "META_REDIRECT_URI",
    "NOTION_VERSION",
    "PUBLIC_ASSET_BASE_URL",
    "THREADS_REDIRECT_URI",
}
TEXT_SUFFIXES = {
    ".cfg",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {".gitignore", "README", "README.md"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "credentials", "public_state", "sns_posts"}


def run_git(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def is_text_path(path: str) -> bool:
    p = Path(path)
    return p.suffix in TEXT_SUFFIXES or p.name in TEXT_NAMES


def load_local_secret_values(env_path: Path) -> list[tuple[str, str]]:
    if not env_path.exists():
        return []

    values: list[tuple[str, str]] = []
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in IGNORED_ENV_KEYS:
            continue
        if SECRET_KEY_RE.search(key) and len(value) >= 12:
            values.append((key, value))
    return values


def staged_files() -> list[str]:
    result = run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return [line for line in result.stdout.splitlines() if line]


def tracked_files() -> list[str]:
    result = run_git(["ls-files"])
    return [line for line in result.stdout.splitlines() if line]


def all_workspace_files() -> list[str]:
    files: list[str] = []
    for root, dirnames, filenames in os.walk("."):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in filenames:
            path = Path(root, filename)
            files.append(path.as_posix().removeprefix("./"))
    return files


def staged_content(path: str) -> str | None:
    result = run_git(["show", f":{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def file_content(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def scan_content(
    *,
    path: str,
    content: str,
    local_secret_values: Iterable[tuple[str, str]],
) -> list[str]:
    findings: list[str] = []

    for key, value in local_secret_values:
        if value and value in content:
            findings.append(f"{path}: contains local .env value for {key}")

    for line_number, line in enumerate(content.splitlines(), start=1):
        lowered = line.lower()
        if "secrets." in lowered or "${{ secrets" in lowered:
            continue
        if "your_" in lowered or "placeholder" in lowered or "example" in lowered:
            continue
        for pattern_name, pattern in TOKEN_PATTERNS.items():
            if pattern.search(line):
                findings.append(f"{path}:{line_number}: matches {pattern_name}")

    return findings


def paths_for_mode(mode: str) -> list[str]:
    if mode == "staged":
        return staged_files()
    if mode == "tracked":
        return tracked_files()
    return all_workspace_files()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check files for accidental secrets.")
    parser.add_argument(
        "--mode",
        choices=("staged", "tracked", "all"),
        default="staged",
        help="staged: pre-commit default, tracked: repository files, all: workspace files except ignored dirs",
    )
    args = parser.parse_args()

    local_secret_values = load_local_secret_values(Path(".env"))
    findings: list[str] = []

    for path in paths_for_mode(args.mode):
        if not is_text_path(path):
            continue
        content = staged_content(path) if args.mode == "staged" else file_content(path)
        if content is None:
            continue
        findings.extend(
            scan_content(path=path, content=content, local_secret_values=local_secret_values)
        )

    if findings:
        print("Secret scan failed. Values are hidden; rotate any exposed credential.", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1

    print(f"Secret scan passed ({args.mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
