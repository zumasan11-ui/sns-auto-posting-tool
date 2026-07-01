import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


FACEBOOK_MANUAL_PHOTOS_ALBUM = os.getenv("FACEBOOK_MANUAL_PHOTOS_ALBUM", "SNS Auto Post")


def truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def default_facebook_manual_dir() -> Path:
    configured = os.getenv("FACEBOOK_MANUAL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if platform.system() == "Darwin" and not truthy_env("CI"):
        return Path.home() / "Desktop" / "Facebook個人投稿用"
    return Path("deliverables/facebook_manual")


FACEBOOK_MANUAL_DIR = default_facebook_manual_dir()


def photos_import_enabled() -> bool:
    return truthy_env("IMPORT_FACEBOOK_MANUAL_TO_PHOTOS", False)


def import_video_to_photos(video_path: Path, album_name: str = FACEBOOK_MANUAL_PHOTOS_ALBUM) -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("写真アプリへの自動保存はmacOSでのみ使えます。")
    if not video_path.exists():
        raise RuntimeError(f"写真アプリへ保存する動画が見つかりません: {video_path}")

    script = """
on run argv
  set videoPath to item 1 of argv
  set albumName to item 2 of argv
  tell application "Photos"
    if not (exists album albumName) then
      make new album named albumName
    end if
    import {POSIX file videoPath} into album albumName skip check duplicates yes
  end tell
end run
"""
    subprocess.run(
        ["osascript", "-e", script, str(video_path.resolve()), album_name],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def export_facebook_manual_video(
    video_path: Path,
    *,
    run_id: str,
    chunk_index: int,
    caption: str,
    import_to_photos: Optional[bool] = None,
) -> dict[str, Path]:
    if not video_path.exists():
        raise RuntimeError(f"Facebook個人投稿用の元動画が見つかりません: {video_path}")

    FACEBOOK_MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    named_path = FACEBOOK_MANUAL_DIR / f"{run_id}_facebook_personal_reel_{chunk_index:02d}.mp4"
    named_caption_path = FACEBOOK_MANUAL_DIR / f"{run_id}_facebook_personal_caption_{chunk_index:02d}.txt"
    shutil.copy2(video_path, named_path)
    caption_text = caption.strip() + "\n"
    named_caption_path.write_text(caption_text, encoding="utf-8")

    should_import = photos_import_enabled() if import_to_photos is None else import_to_photos
    if should_import:
        import_video_to_photos(named_path)

    return {
        "named_video": named_path,
        "named_caption": named_caption_path,
        "video": named_path,
        "caption": named_caption_path,
    }
