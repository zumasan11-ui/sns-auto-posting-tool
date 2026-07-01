import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw

from carousel_generator import render_slide, wrap_text, load_font
from main import (
    get_instagram_media_permalink,
    load_instagram_credentials,
    request_meta_graph_api,
)


CANVAS_SIZE = (1080, 1920)
DEFAULT_SLIDE_GLOB = "slide_*.png"
DEFAULT_ENDING_LINES = ("続きはプロフィールへ", "毎日広告分析を発信中")
REEL_PREVIEW_PATH = Path("deliverables/reels/template_preview.png")
AD_NUMBER_LABELS = ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩")
REEL_COVER_TITLE_TEMPLATE = "なぜこの広告は{period}回っているのか？"
REEL_COVER_FONT_STYLE = "noto"
REEL_BODY_FONT_STYLE = "mincho"
REEL_COVER_DURATION = 1.5
REEL_STRUCTURED_PAGE_DURATION = 3.0
REEL_STRUCTURED_TRANSITION = "none"
REEL_BGM_ENABLED = True
REEL_BGM_PATH = Path("assets/audio/reel_bgm_reference.m4a")
REEL_THUMBNAIL_FILENAME = "thumbnail.png"
SOFT_BODY_FONT_PATHS = (
    "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
    "/System/Library/Fonts/Supplemental/ChalkboardSE.ttc",
    "assets/fonts/NotoSansJP-Regular.ttf",
)
BODY_FONT_STYLES = {
    "maru": (
        "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
        "assets/fonts/NotoSansJP-Regular.ttf",
    ),
    "gothic": (
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "assets/fonts/NotoSansJP-Regular.ttf",
    ),
    "mincho": (
        "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
        "assets/fonts/NotoSansJP-Regular.ttf",
    ),
    "noto": (
        "assets/fonts/NotoSansJP-Regular.ttf",
    ),
}
COVER_FONT_STYLES = {
    "maru": (
        "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
        "assets/fonts/NotoSansJP-Bold.ttf",
    ),
    "gothic": (
        "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
        "assets/fonts/NotoSansJP-Bold.ttf",
    ),
    "mincho": (
        "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
        "assets/fonts/NotoSansJP-Bold.ttf",
    ),
    "noto": (
        "assets/fonts/NotoSansJP-Bold.ttf",
    ),
}


@dataclass(frozen=True)
class ReelSpec:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    slide_duration: float = 2.0
    fade_duration: float = 0.35
    transition: str = "fade"
    ending_duration: float = 2.0
    zoom_amount: float = 0.035
    background: str = "#ffffff"


@dataclass(frozen=True)
class ReelPage:
    path: Path
    duration: float


def sorted_slide_paths(input_dir: Path, pattern: str = DEFAULT_SLIDE_GLOB) -> List[Path]:
    paths = sorted(input_dir.glob(pattern))
    if not paths:
        raise RuntimeError(f"{input_dir} に {pattern} が見つかりません。")
    return paths


def fit_on_canvas(image: Image.Image, spec: ReelSpec) -> Image.Image:
    src = image.convert("RGB")
    canvas = Image.new("RGB", (spec.width, spec.height), spec.background)
    scale = min(spec.width / src.width, spec.height / src.height)
    resized = src.resize((round(src.width * scale), round(src.height * scale)), Image.LANCZOS)
    left = (spec.width - resized.width) // 2
    top = (spec.height - resized.height) // 2
    canvas.paste(resized, (left, top))
    return canvas


def zoom_frame(base: Image.Image, progress: float, spec: ReelSpec, zoom_in: bool) -> Image.Image:
    direction = progress if zoom_in else 1.0 - progress
    zoom = 1.0 + spec.zoom_amount * direction
    crop_w = round(spec.width / zoom)
    crop_h = round(spec.height / zoom)
    left = (spec.width - crop_w) // 2
    top = (spec.height - crop_h) // 2
    cropped = base.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((spec.width, spec.height), Image.LANCZOS)


def blend_frames(current: Image.Image, previous: Optional[Image.Image], alpha: float) -> Image.Image:
    if previous is None or alpha <= 0:
        return current
    if alpha >= 1:
        return current
    return Image.blend(previous, current, alpha)


def render_ending_frame(spec: ReelSpec, lines: Sequence[str] = DEFAULT_ENDING_LINES) -> Image.Image:
    frame = Image.new("RGB", (spec.width, spec.height), spec.background)
    draw = ImageDraw.Draw(frame)
    title_font = load_font(74, bold=True)
    sub_font = load_font(48, bold=True)
    fonts = [title_font, sub_font]
    line_height = 104
    total_h = line_height * len(lines)
    y = (spec.height - total_h) // 2

    for line, font in zip(lines, fonts):
        wrapped = wrap_text(line, font, spec.width - 180, 1)
        text = wrapped[0]
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (spec.width - (bbox[2] - bbox[0])) / 2 - bbox[0]
        draw.text((x, y - bbox[1]), text, font=font, fill="#111111")
        y += line_height

    return frame


def draw_centered_badge(
    draw: ImageDraw.ImageDraw,
    label: str,
    badge: tuple[int, int, int, int],
) -> None:
    label_font = load_font(40, bold=True)
    label_bbox = draw.textbbox((0, 0), label, font=label_font)
    label_w = label_bbox[2] - label_bbox[0]
    label_h = label_bbox[3] - label_bbox[1]
    label_x = badge[0] + (badge[2] - badge[0] - label_w) / 2 - label_bbox[0]
    label_y = badge[1] + (badge[3] - badge[1] - label_h) / 2 - label_bbox[1] + 4
    draw.rounded_rectangle(badge, radius=32, fill="#111111")
    draw.text((label_x, label_y), label, font=label_font, fill="#ffffff")


def load_soft_body_font(size: int, style: str = "maru") -> object:
    paths = BODY_FONT_STYLES.get(style, SOFT_BODY_FONT_PATHS)
    for path in paths:
        if Path(path).exists():
            from PIL import ImageFont

            return ImageFont.truetype(path, size=size)
    return load_font(size)


def load_cover_font(size: int, style: str = "gothic") -> object:
    paths = COVER_FONT_STYLES.get(style, COVER_FONT_STYLES["gothic"])
    for path in paths:
        if Path(path).exists():
            from PIL import ImageFont

            return ImageFont.truetype(path, size=size)
    return load_font(size, bold=True)


def fit_reel_text_lines(
    text: str,
    font_style: str,
    max_width: int,
    max_height: int,
    preferred_size: int,
    preferred_line_height: int,
    min_size: int,
    max_lines: int,
) -> tuple[object, List[str], int, bool]:
    preferred_font = load_soft_body_font(preferred_size, font_style)
    full_lines = wrap_text(text, preferred_font, max_width, 10000)
    if len(full_lines) <= max_lines and len(full_lines) * preferred_line_height <= max_height:
        return preferred_font, full_lines, preferred_line_height, False

    for size in range(preferred_size, min_size - 1, -2):
        font = load_soft_body_font(size, font_style)
        line_height = round(size * 1.48)
        lines = wrap_text(text, font, max_width, 10000)
        if len(lines) <= max_lines and len(lines) * line_height <= max_height:
            return font, lines, line_height, True

    font = load_soft_body_font(min_size, font_style)
    line_height = round(min_size * 1.48)
    return font, wrap_text(text, font, max_width, max(1, max_height // line_height)), line_height, True


def draw_reel_text_block(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    x: int,
    y: int,
    font: object,
    fill: str,
    line_height: int,
) -> None:
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height


def draw_reel_centered_text_block(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    box: tuple[int, int, int, int],
    font: object,
    fill: str,
    line_height: int,
) -> None:
    x1, y1, _x2, y2 = box
    total_height = len(lines) * line_height
    y = y1 + max(0, (y2 - y1 - total_height) // 2)
    draw_reel_text_block(draw, lines, x1, y, font, fill, line_height)


def render_reel_analysis_slide(
    index: int,
    total: int,
    text: str,
    screenshot: Image.Image,
    spec: ReelSpec = ReelSpec(),
    font_style: str = "mincho",
) -> Image.Image:
    frame = Image.new("RGB", (spec.width, spec.height), spec.background)
    draw = ImageDraw.Draw(frame)
    black = "#111111"

    margin_x = 72
    ad_number = AD_NUMBER_LABELS[index - 1] if 1 <= index <= len(AD_NUMBER_LABELS) else str(index)
    draw_centered_badge(draw, f"広告分析{ad_number}", (margin_x, 128, margin_x + 330, 192))

    text_top = 328
    body_font, text_lines, line_height, resized = fit_reel_text_lines(
        text,
        font_style,
        spec.width - margin_x * 2,
        490,
        preferred_size=42,
        preferred_line_height=62,
        min_size=31,
        max_lines=9,
    )
    if resized:
        draw_reel_centered_text_block(
            draw,
            text_lines,
            (margin_x, 300, spec.width - margin_x, 815),
            body_font,
            black,
            line_height,
        )
    else:
        draw_reel_text_block(draw, text_lines, margin_x, text_top, body_font, black, line_height)

    image_top = 855
    image_box = (90, image_top, spec.width - 90, 1635)
    image_area = fit_on_canvas(
        screenshot,
        ReelSpec(
            width=image_box[2] - image_box[0],
            height=image_box[3] - image_box[1],
            background=spec.background,
        ),
    )
    frame.paste(image_area, (image_box[0], image_box[1]))
    return frame


def render_reel_cover_slide(
    title: str,
    screenshot: Image.Image,
    spec: ReelSpec = ReelSpec(),
    font_style: str = "mincho",
) -> Image.Image:
    frame = Image.new("RGB", (spec.width, spec.height), spec.background)
    draw = ImageDraw.Draw(frame)
    title_font = load_cover_font(104, font_style)
    highlight_font = load_cover_font(142, font_style)
    black = "#111111"
    margin_x = 72

    def draw_centered_text(text: str, y: int, font: object, fill: str = black) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (spec.width - (bbox[2] - bbox[0])) / 2 - bbox[0]
        draw.text((x, y - bbox[1]), text, font=font, fill=fill)

    def draw_left_text(text: str, y: int, font: object, fill: str = black) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((margin_x, y - bbox[1]), text, font=font, fill=fill)

    title_match = re.fullmatch(r"なぜこの広告は(.+)回っているのか？", title)
    if title_match:
        title_top = 290
        line_1 = "なぜこの広告は"
        line_2 = title_match.group(1)
        line_3 = "回っているのか？"

        draw_left_text(line_1, title_top, title_font)

        badge_top = title_top + 122
        badge_w = 520
        badge = (margin_x, badge_top, margin_x + badge_w, badge_top + 166)
        draw.rounded_rectangle(badge, radius=24, fill=black)
        bbox = draw.textbbox((0, 0), line_2, font=highlight_font)
        text_x = badge[0] + (badge[2] - badge[0] - (bbox[2] - bbox[0])) / 2 - bbox[0]
        text_y = badge[1] + (badge[3] - badge[1] - (bbox[3] - bbox[1])) / 2 - bbox[1] + 4
        draw.text((text_x, text_y), line_2, font=highlight_font, fill="#ffffff")

        draw_left_text(line_3, badge_top + 188, title_font)
    else:
        title_top = 235
        title_lines = wrap_text(title, title_font, spec.width - margin_x * 2, 3)
        for line in title_lines:
            draw_centered_text(line, title_top, title_font)
            title_top += 92

    image_box = (90, 820, spec.width - 90, 1600)
    image_area = fit_on_canvas(
        screenshot,
        ReelSpec(
            width=image_box[2] - image_box[0],
            height=image_box[3] - image_box[1],
            background=spec.background,
        ),
    )
    frame.paste(image_area, (image_box[0], image_box[1]))
    return frame


def render_business_model_slide(
    text: str,
    spec: ReelSpec = ReelSpec(),
    font_style: str = "mincho",
) -> Image.Image:
    frame = Image.new("RGB", (spec.width, spec.height), spec.background)
    draw = ImageDraw.Draw(frame)
    black = "#111111"
    margin_x = 72

    draw_centered_badge(draw, "ビジネスモデル", (margin_x, 128, margin_x + 410, 192))

    text_top = 360
    body_font, text_lines, line_height, resized = fit_reel_text_lines(
        text,
        font_style,
        spec.width - margin_x * 2,
        1040,
        preferred_size=48,
        preferred_line_height=72,
        min_size=32,
        max_lines=12,
    )
    if resized:
        draw_reel_centered_text_block(
            draw,
            text_lines,
            (margin_x, 320, spec.width - margin_x, 1450),
            body_font,
            black,
            line_height,
        )
    else:
        draw_reel_text_block(draw, text_lines, margin_x, text_top, body_font, black, line_height)

    return frame


def iter_reel_frames(slide_paths: Sequence[Path], spec: ReelSpec) -> Iterable[np.ndarray]:
    base_frames = [fit_on_canvas(Image.open(path), spec) for path in slide_paths]
    previous_last: Optional[Image.Image] = None
    frames_per_slide = round(spec.slide_duration * spec.fps)
    fade_frames = max(0, round(spec.fade_duration * spec.fps))

    for slide_index, base in enumerate(base_frames):
        zoom_in = slide_index % 2 == 0
        for frame_index in range(frames_per_slide):
            progress = frame_index / max(1, frames_per_slide - 1)
            frame = zoom_frame(base, progress, spec, zoom_in)
            if fade_frames and frame_index < fade_frames:
                alpha = frame_index / fade_frames
                frame = blend_frames(frame, previous_last, alpha)
            if frame_index == frames_per_slide - 1:
                previous_last = frame
            yield np.asarray(frame, dtype=np.uint8)

    ending = render_ending_frame(spec)
    ending_frames = round(spec.ending_duration * spec.fps)
    for frame_index in range(ending_frames):
        progress = frame_index / max(1, ending_frames - 1)
        frame = zoom_frame(ending, progress, spec, zoom_in=True)
        if fade_frames and frame_index < fade_frames:
            alpha = frame_index / fade_frames
            frame = blend_frames(frame, previous_last, alpha)
        yield np.asarray(frame, dtype=np.uint8)


def iter_reel_page_frames(pages: Sequence[ReelPage], spec: ReelSpec) -> Iterable[np.ndarray]:
    previous_last: Optional[Image.Image] = None
    fade_frames = max(0, round(spec.fade_duration * spec.fps))
    rendered_pages = [fit_on_canvas(Image.open(page.path), spec) for page in pages]

    for page_index, page in enumerate(pages):
        base = rendered_pages[page_index]
        next_base = rendered_pages[page_index + 1] if page_index + 1 < len(rendered_pages) else None
        frames_per_page = max(1, round(page.duration * spec.fps))
        transition_frames = min(fade_frames, max(0, frames_per_page - 1))
        zoom_in = page_index % 2 == 0
        for frame_index in range(frames_per_page):
            progress = frame_index / max(1, frames_per_page - 1)
            frame = zoom_frame(base, progress, spec, zoom_in)
            if spec.transition == "page" and next_base is not None and transition_frames and frame_index >= frames_per_page - transition_frames:
                transition_progress = (frame_index - (frames_per_page - transition_frames)) / max(1, transition_frames - 1)
                next_frame = zoom_frame(next_base, 0, spec, not zoom_in)
                frame = render_page_turn_frame(frame, next_frame, transition_progress, spec)
            elif fade_frames and frame_index < fade_frames:
                alpha = frame_index / fade_frames
                frame = blend_frames(frame, previous_last, alpha)
            if frame_index == frames_per_page - 1:
                previous_last = frame
            yield np.asarray(frame, dtype=np.uint8)

    ending = render_ending_frame(spec)
    ending_frames = round(spec.ending_duration * spec.fps)
    for frame_index in range(ending_frames):
        progress = frame_index / max(1, ending_frames - 1)
        frame = zoom_frame(ending, progress, spec, zoom_in=True)
        if fade_frames and frame_index < fade_frames:
            alpha = frame_index / fade_frames
            frame = blend_frames(frame, previous_last, alpha)
        yield np.asarray(frame, dtype=np.uint8)


def render_page_turn_frame(current: Image.Image, next_frame: Image.Image, progress: float, spec: ReelSpec) -> Image.Image:
    eased = 1 - (1 - progress) * (1 - progress)
    offset = round(spec.width * (1 - eased))
    canvas = Image.new("RGB", (spec.width, spec.height), spec.background)
    canvas.paste(current, (0, 0))
    canvas.paste(next_frame, (offset, 0))

    fold_w = min(70, spec.width - offset)
    if fold_w > 0:
        overlay = Image.new("RGBA", (fold_w, spec.height), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (fold_w, spec.height), (0, 0, 0, 42))
        overlay.alpha_composite(shadow)
        canvas.paste(overlay, (offset, 0), overlay)
    return canvas


def get_ffmpeg_command() -> List[str]:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return [ffmpeg_path]

    try:
        import imageio_ffmpeg
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "ffmpeg または imageio-ffmpeg が必要です。"
            " pip install -r requirements.txt を実行してください。"
        ) from error

    return [imageio_ffmpeg.get_ffmpeg_exe()]


def structured_video_duration(pages: Sequence[ReelPage], spec: ReelSpec) -> float:
    return sum(page.duration for page in pages) + spec.ending_duration


def mux_bgm(video_path: Path, bgm_path: Path, output_path: Path) -> None:
    if not bgm_path.exists():
        raise RuntimeError(f"BGM素材が見つかりません: {bgm_path}")
    ffmpeg = get_ffmpeg_command()
    command = [
        *ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"BGM合成に失敗しました: {process.stderr}")


def save_reel_thumbnail(pages: Sequence[ReelPage], output_dir: Path) -> Path:
    if not pages:
        raise RuntimeError("サムネ生成にはページが必要です。")
    output_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = output_dir / REEL_THUMBNAIL_FILENAME
    Image.open(pages[0].path).convert("RGB").save(thumbnail_path)
    return thumbnail_path


def write_mp4(slide_paths: Sequence[Path], output_path: Path, spec: ReelSpec) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_command()
    command = [
        *ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{spec.width}x{spec.height}",
        "-r",
        str(spec.fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-preset",
        "medium",
        "-crf",
        "20",
        str(output_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None

    try:
        for frame in iter_reel_frames(slide_paths, spec):
            process.stdin.write(frame.tobytes())
    except BrokenPipeError as error:
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        raise RuntimeError(f"ffmpeg への書き込みに失敗しました: {stderr}") from error
    finally:
        process.stdin.close()

    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg が失敗しました: {stderr}")


def write_structured_mp4(
    pages: Sequence[ReelPage],
    output_path: Path,
    spec: ReelSpec,
    with_bgm: bool = REEL_BGM_ENABLED,
    bgm_path: Path = REEL_BGM_PATH,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_output_path = output_path
    if with_bgm:
        video_output_path = output_path.with_name(f"{output_path.stem}_no_bgm{output_path.suffix}")

    ffmpeg = get_ffmpeg_command()
    command = [
        *ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{spec.width}x{spec.height}",
        "-r",
        str(spec.fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-preset",
        "medium",
        "-crf",
        "20",
        str(video_output_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None

    try:
        for frame in iter_reel_page_frames(pages, spec):
            process.stdin.write(frame.tobytes())
    except BrokenPipeError as error:
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        raise RuntimeError(f"ffmpeg への書き込みに失敗しました: {stderr}") from error
    finally:
        process.stdin.close()

    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg が失敗しました: {stderr}")

    if with_bgm:
        mux_bgm(video_output_path, bgm_path, output_path)


def build_test_carousel(
    source_images: Sequence[Path],
    text: str,
    output_dir: Path,
    count: int = 10,
) -> List[Path]:
    if not source_images:
        raise RuntimeError("テスト用画像が指定されていません。")

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered_paths: List[Path] = []
    for index in range(1, count + 1):
        screenshot = Image.open(source_images[(index - 1) % len(source_images)])
        slide = render_slide(index, count, "広告分析メモ", text, screenshot)
        path = output_dir / f"slide_{index:02d}.png"
        slide.save(path)
        rendered_paths.append(path)

    return rendered_paths


def repeat_to_length(values: Sequence[str], length: int) -> List[str]:
    if not values:
        raise RuntimeError("必要なテキストが指定されていません。")
    return [values[index % len(values)] for index in range(length)]


def build_structured_reel_pages(
    ad_images: Sequence[Path],
    ad_texts: Sequence[str],
    business_texts: Sequence[str],
    output_dir: Path,
    cover_title: str,
    max_ads: int = 5,
    font_style: str = REEL_BODY_FONT_STYLE,
    cover_font_style: str = REEL_COVER_FONT_STYLE,
    cover_duration: float = REEL_COVER_DURATION,
    spec: ReelSpec = ReelSpec(),
) -> List[ReelPage]:
    if not ad_images:
        raise RuntimeError("広告画像が指定されていません。")

    ad_count = min(len(ad_images), max_ads)
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: List[ReelPage] = []
    normalized_ad_texts = repeat_to_length(ad_texts, ad_count)
    normalized_business_texts = repeat_to_length(business_texts, ad_count)

    cover = render_reel_cover_slide(cover_title, Image.open(ad_images[0]), spec, cover_font_style)
    cover_path = output_dir / "page_00_cover.png"
    cover.save(cover_path)
    pages.append(ReelPage(cover_path, cover_duration))

    for index in range(1, ad_count + 1):
        screenshot = Image.open(ad_images[index - 1])
        ad_page = render_reel_analysis_slide(
            index,
            ad_count,
            normalized_ad_texts[index - 1],
            screenshot,
            spec,
            font_style,
        )
        ad_path = output_dir / f"page_{index:02d}_ad.png"
        ad_page.save(ad_path)
        pages.append(ReelPage(ad_path, spec.slide_duration))

        business_page = render_business_model_slide(
            normalized_business_texts[index - 1],
            spec,
            font_style,
        )
        business_path = output_dir / f"page_{index:02d}_business.png"
        business_page.save(business_path)
        pages.append(ReelPage(business_path, spec.slide_duration))

    return pages


def save_template_preview(
    source_image: Path,
    text: str,
    output_path: Path = REEL_PREVIEW_PATH,
    font_style: str = "mincho",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    slide = render_reel_analysis_slide(1, 1, text, Image.open(source_image), font_style=font_style)
    slide.save(output_path)
    return output_path


def wait_for_reels_container(creation_id: str, access_token: str) -> None:
    for _ in range(60):
        data = request_meta_graph_api(
            "GET",
            f"/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
        )
        status_code = data.get("status_code")
        if status_code in ("FINISHED", "PUBLISHED"):
            return
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram Reelsコンテナ作成に失敗しました: {data}")
        time.sleep(5)

    raise RuntimeError(f"Instagram Reelsコンテナが時間内に完了しませんでした: {creation_id}")


def post_instagram_reel(video_url: str, caption: str) -> str:
    if not video_url.startswith("https://"):
        raise RuntimeError("Instagram Reels投稿には公開HTTPSの --video-url が必要です。")

    credentials = load_instagram_credentials()
    user_id = credentials["INSTAGRAM_USER_ID"]
    access_token = credentials["INSTAGRAM_ACCESS_TOKEN"]
    container = request_meta_graph_api(
        "POST",
        f"/{user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": access_token,
        },
    )
    creation_id = container.get("id")
    if not creation_id:
        raise RuntimeError(f"Instagram ReelsコンテナIDを取得できませんでした: {container}")

    wait_for_reels_container(creation_id, access_token)
    published = request_meta_graph_api(
        "POST",
        f"/{user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
    )
    media_id = published.get("id")
    if not media_id:
        raise RuntimeError(f"Instagram Reels投稿IDを取得できませんでした: {published}")

    Path("sns_posts").mkdir(exist_ok=True)
    Path("sns_posts/instagram_reel_last.json").write_text(
        json.dumps(
            {"creation_id": creation_id, "published": published},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return get_instagram_media_permalink(media_id, access_token) or f"Instagram Reels投稿ID: {media_id}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Instagramカルーセル画像からReels用mp4を生成・投稿します。")
    parser.add_argument("--input-dir", default="deliverables/carousel_test")
    parser.add_argument("--output", default="deliverables/reels/reel.mp4")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--slide-duration", type=float, default=REEL_STRUCTURED_PAGE_DURATION)
    parser.add_argument("--fade-duration", type=float, default=0.35)
    parser.add_argument("--transition", choices=("fade", "none", "page"), default=REEL_STRUCTURED_TRANSITION)
    parser.add_argument("--ending-duration", type=float, default=2.0)
    parser.add_argument("--source-images", nargs="*", type=Path, help="テスト用に交互配置する元画像")
    parser.add_argument("--text", help="テスト用カルーセルに入れる分析文")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--post", action="store_true", help="生成後にInstagram Reelsへ投稿します。")
    parser.add_argument("--video-url", help="Instagram Graph APIから取得できる公開HTTPSのmp4 URL")
    parser.add_argument("--caption", default="")
    parser.add_argument("--template-preview", action="store_true", help="動画を作らずReels用テンプレ画像を1枚だけ生成します。")
    parser.add_argument("--template-image", type=Path, help="テンプレ確認に使う画像")
    parser.add_argument("--template-output", type=Path, default=REEL_PREVIEW_PATH)
    parser.add_argument("--font-style", choices=tuple(BODY_FONT_STYLES), default=REEL_BODY_FONT_STYLE)
    parser.add_argument("--cover-font-style", choices=tuple(COVER_FONT_STYLES), default=REEL_COVER_FONT_STYLE)
    parser.add_argument("--cover-duration", type=float, default=REEL_COVER_DURATION)
    parser.add_argument("--structured-reel", action="store_true", help="表紙、広告、ビジネスモデルを交互にしたReelsを生成します。")
    parser.add_argument("--ad-images", nargs="*", type=Path, help="最大5枚までの広告画像")
    parser.add_argument("--ad-text", action="append", default=[], help="広告ページに入れる文章。複数指定可。")
    parser.add_argument("--business-text", action="append", default=[], help="ビジネスモデルページに入れる文章。複数指定可。")
    parser.add_argument("--cover-title", default=REEL_COVER_TITLE_TEMPLATE.format(period="◯ヶ月"))
    parser.add_argument("--pages-dir", type=Path, default=Path("deliverables/reels/pages"))
    parser.add_argument("--max-ads", type=int, default=5)
    parser.add_argument("--no-bgm", action="store_true", help="固定BGMを付けず、無音MP4を生成します。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.template_preview:
        if not args.template_image or not args.text:
            raise RuntimeError("--template-preview には --template-image と --text が必要です。")
        print(save_template_preview(args.template_image, args.text, args.template_output, args.font_style))
        return 0

    spec = ReelSpec(
        fps=args.fps,
        slide_duration=args.slide_duration,
        fade_duration=0 if args.transition == "none" else args.fade_duration,
        transition=args.transition,
        ending_duration=args.ending_duration,
    )
    output_path = Path(args.output)

    if args.structured_reel:
        ad_images = args.ad_images or args.source_images
        ad_texts = args.ad_text or ([args.text] if args.text else [])
        if not ad_images:
            raise RuntimeError("--structured-reel には --ad-images が必要です。")
        pages = build_structured_reel_pages(
            ad_images=ad_images,
            ad_texts=ad_texts,
            business_texts=args.business_text,
            output_dir=args.pages_dir,
            cover_title=args.cover_title,
            max_ads=args.max_ads,
            font_style=args.font_style,
            cover_font_style=args.cover_font_style,
            cover_duration=args.cover_duration,
            spec=spec,
        )
        print(save_reel_thumbnail(pages, args.pages_dir.parent))
        write_structured_mp4(pages, output_path, spec, with_bgm=not args.no_bgm)
        for page in pages:
            print(page.path)
        print(output_path)
        if args.post:
            if not args.video_url:
                raise RuntimeError("--post には --video-url が必要です。ローカルmp4を公開HTTPS URLに置いて指定してください。")
            print(post_instagram_reel(args.video_url, args.caption))
        return 0

    input_dir = Path(args.input_dir)
    if args.source_images:
        if not args.text:
            raise RuntimeError("--source-images を使う場合は --text も指定してください。")
        slide_paths = build_test_carousel(args.source_images, args.text, input_dir, args.count)
    else:
        slide_paths = sorted_slide_paths(input_dir)

    write_mp4(slide_paths, output_path, spec)
    print(output_path)

    if args.post:
        if not args.video_url:
            raise RuntimeError("--post には --video-url が必要です。ローカルmp4を公開HTTPS URLに置いて指定してください。")
        print(post_instagram_reel(args.video_url, args.caption))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"エラーが発生しました: {error}", file=sys.stderr)
        raise SystemExit(1)
