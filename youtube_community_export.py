import os
import platform
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


def truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def youtube_community_export_enabled() -> bool:
    if not truthy_env("YOUTUBE_COMMUNITY_EXPORT_ENABLED", True):
        return False
    if truthy_env("CI") or truthy_env("GITHUB_ACTIONS"):
        return False
    return platform.system() == "Darwin"


def default_youtube_community_dir() -> Path:
    configured = os.getenv("YOUTUBE_COMMUNITY_EXPORT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Desktop" / "YouTube投稿"


def safe_path_part(value: str, fallback: str = "投稿") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[/:*?\"<>|\\]", "_", text)
    text = text.strip(" .")
    return text[:80] or fallback


def export_youtube_community_images(
    slide_paths: Sequence[Path],
    *,
    chunk_index: int,
    post_title: str = "",
    caption: str = "",
    enabled: Optional[bool] = None,
) -> Optional[Path]:
    should_export = youtube_community_export_enabled() if enabled is None else enabled
    if not should_export:
        return None

    if len(slide_paths) < 10:
        raise RuntimeError(f"YouTubeコミュニティ投稿用画像は10枚必要です。現在: {len(slide_paths)}枚")

    output_dir = default_youtube_community_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"post_{chunk_index:02d}"
    for old_file in output_dir.glob(f"{prefix}_*"):
        if old_file.is_file():
            old_file.unlink()

    for index, source in enumerate(slide_paths[:10], start=1):
        source = Path(source)
        if not source.exists():
            raise RuntimeError(f"YouTubeコミュニティ投稿用画像の元ファイルが見つかりません: {source}")
        target = output_dir / f"{prefix}_{index:02d}.png"
        shutil.copy2(source, target)

    if caption.strip():
        (output_dir / f"{prefix}_caption.txt").write_text(caption.strip() + "\n", encoding="utf-8")

    return output_dir
