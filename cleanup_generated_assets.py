import argparse
import os
import shutil
import time
from pathlib import Path
from typing import Iterable


DEFAULT_RETENTION_DAYS = int(os.getenv("GENERATED_ASSET_RETENTION_DAYS", "7"))
AUTO_POST_DIR = Path(os.getenv("AUTO_POST_RUNTIME_DIR", "deliverables/auto_post"))
FACEBOOK_MANUAL_DIR = Path(os.getenv("FACEBOOK_MANUAL_DIR", "deliverables/facebook_manual"))
PUBLIC_RUNS_DIR = Path(os.getenv("PUBLIC_RUNS_DIR", "public_state/public/runs"))
PUBLIC_MANUAL_TESTS_DIR = Path(os.getenv("PUBLIC_MANUAL_TESTS_DIR", "public_state/public/manual_tests"))
LATEST_FACEBOOK_FILES = {
    "latest_facebook_personal_reel.mp4",
    "latest_facebook_personal_caption.txt",
}


def cutoff_timestamp(retention_days: int) -> float:
    return time.time() - retention_days * 24 * 60 * 60


def is_older(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def remove_path(path: Path, *, dry_run: bool) -> None:
    if dry_run:
        print(f"would remove {path}")
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    print(f"removed {path}")


def cleanup_child_dirs(parent: Path, cutoff: float, *, dry_run: bool) -> None:
    if not parent.exists():
        return
    for child in sorted(parent.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir() and is_older(child, cutoff):
            remove_path(child, dry_run=dry_run)


def cleanup_files(parent: Path, cutoff: float, *, dry_run: bool, keep_names: Iterable[str] = ()) -> None:
    if not parent.exists():
        return
    keep = set(keep_names)
    for child in sorted(parent.iterdir()):
        if child.name.startswith(".") or child.name in keep:
            continue
        if child.is_file() and is_older(child, cutoff):
            remove_path(child, dry_run=dry_run)


def cleanup_generated_assets(retention_days: int = DEFAULT_RETENTION_DAYS, *, dry_run: bool = False) -> None:
    cutoff = cutoff_timestamp(retention_days)
    cleanup_child_dirs(AUTO_POST_DIR, cutoff, dry_run=dry_run)
    cleanup_child_dirs(PUBLIC_RUNS_DIR, cutoff, dry_run=dry_run)
    cleanup_child_dirs(PUBLIC_MANUAL_TESTS_DIR, cutoff, dry_run=dry_run)
    cleanup_files(FACEBOOK_MANUAL_DIR, cutoff, dry_run=dry_run, keep_names=LATEST_FACEBOOK_FILES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="古い生成物を削除します。")
    parser.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS, help="保持日数。初期値は7日。")
    parser.add_argument("--dry-run", action="store_true", help="削除せず対象だけ表示します。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cleanup_generated_assets(args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
