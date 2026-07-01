import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


FACEBOOK_MANUAL_DIR = Path(os.getenv("FACEBOOK_MANUAL_DIR", "deliverables/facebook_manual"))
FACEBOOK_MANUAL_LATEST_FILENAME = "latest_facebook_personal_reel.mp4"
FACEBOOK_MANUAL_PHOTOS_ALBUM = os.getenv("FACEBOOK_MANUAL_PHOTOS_ALBUM", "SNS Auto Post")


def truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def photos_import_enabled() -> bool:
    default = platform.system() == "Darwin" and not truthy_env("CI")
    return truthy_env("IMPORT_FACEBOOK_MANUAL_TO_PHOTOS", default)


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
    import_to_photos: Optional[bool] = None,
) -> Path:
    if not video_path.exists():
        raise RuntimeError(f"Facebook個人投稿用の元動画が見つかりません: {video_path}")

    FACEBOOK_MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    named_path = FACEBOOK_MANUAL_DIR / f"{run_id}_facebook_personal_reel_{chunk_index:02d}.mp4"
    latest_path = FACEBOOK_MANUAL_DIR / FACEBOOK_MANUAL_LATEST_FILENAME
    shutil.copy2(video_path, named_path)
    shutil.copy2(video_path, latest_path)

    should_import = photos_import_enabled() if import_to_photos is None else import_to_photos
    if should_import:
        import_video_to_photos(latest_path)

    return latest_path
