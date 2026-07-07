import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List

import requests
from PIL import Image, ImageDraw
from PIL import ImageFont

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from carousel_generator import (
    CANVAS_SIZE,
    MARGIN_X,
    fit_contain,
    fit_text_lines,
    load_font,
    load_carousel_body_font,
    render_carousel_ending_slide,
    split_japanese_line,
)
from reels_generator import ReelPage, ReelSpec, select_bgm_source, write_structured_mp4
from sheets_api import build_sheets_service, load_sheets_config, read_values
from scripts.append_meta_visible_rows_to_sheet import quote_sheet_name


SPREADSHEET_ID = "15mskJs84UE7-CUtwELlCnjw3_DoWpAIYnZUvqiJvrdc"
TODAY_SHEET = "今日の広告DB"
FONT_VARIANTS = {
    "w4": "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "w5": "/System/Library/Fonts/ヒラギノ角ゴシック W5.ttc",
}
COUNTER_POSITIONS = {"badge-right", "top-right"}


SAMPLE_POST = """広告分析vol.001

【ビジネスモデル】
介護タクシー開業を検討している個人に向けて、開業ノウハウや集客支援を提供する支援型ビジネス。広告では資料請求を入口にして、まずは独立への不安を下げて見込み客を集めている。
商材は単発の商品ではなく、開業準備、許認可、車両準備、集客方法までをまとめて支援する高単価サービスだと考えられる。未経験でも始められるという切り口で、介護・福祉領域に関心がある人の副業や独立ニーズを拾っている。
LPでは無料資料を配布し、その後に個別相談や説明会へ誘導する流れが想定できる。広告の役割は今すぐ契約ではなく、独立したいけど何から始めればいいか分からない層をリード化することにある。

【なぜこの広告が勝ってるか】
一番強いのは、画像だけで誰向けの広告かが一瞬で分かること。「介護タクシー」という大きな文字と、車いす・送迎の写真で、サービス内容がほぼ説明なしで伝わる。
さらに「知識・経験なしでも安心して起業」というコピーが、開業前の最大の不安を直接つぶしている。専門知識がない、経験がない、自分でもできるのか不安という心理に対して、安心という言葉でハードルを下げている。
最後に「資料プレゼント中」で行動の負担を軽くしている。いきなり相談や申込みではなく、まず資料を見るだけでいいため、興味はあるけどまだ迷っている人でもクリックしやすい。

【広告の学び】
ニッチな開業支援広告では、かっこよさよりも「何の仕事か」「未経験でもできるか」「次に何をすればいいか」を一瞬で伝えることが大事。大きなカテゴリ名、不安解消コピー、低ハードルCTAの3点セットは、他の独立・副業系広告にも横展開しやすい。"""


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def read_first_today_ad() -> Dict[str, str]:
    service = build_sheets_service(load_sheets_config())
    values = read_values(service, SPREADSHEET_ID, f"{quote_sheet_name(TODAY_SHEET)}!A1:AZ")
    headers = [clean(header) for header in values[0]]
    for row in values[1:]:
        item = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        if clean(item.get("広告スクショ")):
            return item
    raise RuntimeError("今日の広告DBに広告スクショ付きの広告が見つかりません。")


def download_image(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def save_contact_sheet(slide_paths: List[Path], output_path: Path) -> Path:
    thumbs = []
    thumb_size = (216, 270)
    for path in slide_paths:
        with Image.open(path) as slide:
            thumbs.append(slide.convert("RGB").resize(thumb_size, Image.LANCZOS))
    cols = 5
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (thumb_size[0] * cols, thumb_size[1] * rows), "#f2f2f2")
    for index, thumb in enumerate(thumbs):
        x = (index % cols) * thumb_size[0]
        y = (index // cols) * thumb_size[1]
        sheet.paste(thumb, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    return output_path


def draw_centered_text(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], font: object, fill: str) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x1, y1, x2, y2 = box
    x = x1 + (x2 - x1 - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = y1 + (y2 - y1 - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def fit_font_for_width(text: str, start_size: int, min_size: int, max_width: int, bold: bool = True) -> object:
    for size in range(start_size, min_size - 1, -2):
        font = load_font(size, bold=bold)
        if font.getlength(text) <= max_width:
            return font
    return load_font(min_size, bold=bold)


def load_variant_font(size: int, variant: str = "") -> object:
    path = FONT_VARIANTS.get(variant)
    if path and Path(path).exists():
        return ImageFont.truetype(path, size=size)
    return load_carousel_body_font(size)


def fit_variant_text_lines(
    text: str,
    variant: str,
    max_width: int,
    max_height: int,
    preferred_size: int,
    min_size: int,
    line_height_ratio: float,
    max_lines: int,
) -> tuple[object, List[str], int]:
    for size in range(preferred_size, min_size - 1, -2):
        font = load_variant_font(size, variant)
        line_height = round(size * line_height_ratio)
        lines = wrap_text_unlimited(text, font, max_width)
        if len(lines) <= max_lines and len(lines) * line_height <= max_height:
            return font, lines, line_height
    font = load_variant_font(min_size, variant)
    line_height = round(min_size * line_height_ratio)
    return font, wrap_text_unlimited(text, font, max_width), line_height


def render_dynamic_cover(period: str, screenshot: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", CANVAS_SIZE, "#ffffff")
    draw = ImageDraw.Draw(canvas)
    black = "#111111"
    title_font = load_font(78, bold=True)
    sub_font = load_carousel_body_font(36)
    period_text = period or "◯ヶ月"
    period_font = fit_font_for_width(period_text, 116, 70, CANVAS_SIZE[0] - MARGIN_X * 2 - 96)
    period_w = round(period_font.getlength(period_text))
    badge_w = min(CANVAS_SIZE[0] - MARGIN_X * 2, max(360, period_w + 96))

    top = 105
    draw.text((MARGIN_X, top), "なぜこの広告は", font=title_font, fill=black)
    badge_top = top + 98
    badge = (MARGIN_X, badge_top, MARGIN_X + badge_w, badge_top + 132)
    draw.rounded_rectangle(badge, radius=24, fill=black)
    draw_centered_text(draw, period_text, badge, period_font, "#ffffff")
    draw.text((MARGIN_X, badge_top + 154), "回っているのか？", font=title_font, fill=black)
    draw.text((MARGIN_X, 490), "広告クリエイティブを分解して考える", font=sub_font, fill="#222222")

    ad_box = (155, 595, CANVAS_SIZE[0] - 155, 1260)
    ad_area = fit_contain(screenshot, (ad_box[2] - ad_box[0], ad_box[3] - ad_box[1]), background="#ffffff")
    canvas.paste(ad_area, (ad_box[0], ad_box[1]))
    return canvas


def wrap_text_unlimited(text: str, font: object, max_width: int) -> List[str]:
    lines: List[str] = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            lines.append("")
            continue
        lines.extend(split_japanese_line(raw_line, font, max_width))
    return lines


def render_segment_text_slide(
    label: str,
    counter: str,
    body: str,
    body_font_variant: str = "",
    counter_position: str = "badge-right",
) -> Image.Image:
    canvas = Image.new("RGB", CANVAS_SIZE, "#ffffff")
    draw = ImageDraw.Draw(canvas)
    label_font = load_font(42, bold=True)
    counter_font = load_font(34, bold=True)
    black = "#111111"
    body_area = (96, 285, CANVAS_SIZE[0] - 96, 1195)
    body_font, body_lines, line_height = fit_variant_text_lines(
        body,
        body_font_variant,
        body_area[2] - body_area[0],
        body_area[3] - body_area[1],
        preferred_size=46,
        min_size=34,
        line_height_ratio=1.72,
        max_lines=11,
    )
    if body and not any(line.strip() for line in body_lines):
        body_lines = wrap_text_unlimited(body, body_font, body_area[2] - body_area[0])

    label_bbox = draw.textbbox((0, 0), label, font=label_font)
    label_w = label_bbox[2] - label_bbox[0]
    badge = (MARGIN_X, 76, MARGIN_X + label_w + 70, 140)
    draw.rounded_rectangle(badge, radius=32, fill=black)
    draw_centered_text(draw, label, badge, label_font, "#ffffff")
    if counter:
        counter_bbox = draw.textbbox((0, 0), counter, font=counter_font)
        if counter_position == "top-right":
            counter_x = CANVAS_SIZE[0] - MARGIN_X - (counter_bbox[2] - counter_bbox[0])
        else:
            counter_x = min(badge[2] + 22, CANVAS_SIZE[0] - MARGIN_X - (counter_bbox[2] - counter_bbox[0]))
        draw.text((counter_x, 92 - counter_bbox[1]), counter, font=counter_font, fill=black)

    total_height = len(body_lines) * line_height
    y = body_area[1] + max(0, (body_area[3] - body_area[1] - total_height) // 2)
    for line in body_lines:
        draw.text((body_area[0], y), line, font=body_font, fill="#222222")
        y += line_height
    return canvas


def render_today_ad_slide(company: str, service_name: str, screenshot: Image.Image, body_font_variant: str = "") -> Image.Image:
    canvas = Image.new("RGB", CANVAS_SIZE, "#ffffff")
    draw = ImageDraw.Draw(canvas)
    label = "今日の広告"
    label_font = load_font(42, bold=True)
    body_font = load_variant_font(36, body_font_variant)
    black = "#111111"

    label_bbox = draw.textbbox((0, 0), label, font=label_font)
    label_w = label_bbox[2] - label_bbox[0]
    badge = (MARGIN_X, 76, MARGIN_X + label_w + 70, 140)
    draw.rounded_rectangle(badge, radius=32, fill=black)
    draw_centered_text(draw, label, badge, label_font, "#ffffff")

    body = f"{company}の{service_name}の広告"
    body_lines = wrap_text_unlimited(body, body_font, CANVAS_SIZE[0] - MARGIN_X * 2)
    line_height = 56
    text_top = 325
    total_height = len(body_lines) * line_height
    y = text_top - total_height // 2
    for line in body_lines:
        bbox = draw.textbbox((0, 0), line, font=body_font)
        x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) / 2 - bbox[0]
        draw.text((x, y), line, font=body_font, fill="#222222")
        y += line_height

    draw.line((0, 430, CANVAS_SIZE[0], 430), fill="#eeeeee", width=2)
    ad_box = (150, 490, CANVAS_SIZE[0] - 150, 1260)
    ad_area = fit_contain(screenshot, (ad_box[2] - ad_box[0], ad_box[3] - ad_box[1]), background="#ffffff")
    canvas.paste(ad_area, (ad_box[0], ad_box[1]))
    return canvas


def extract_section(text: str, heading: str) -> str:
    pattern = rf"【{re.escape(heading)}】\s*(.*?)(?=\n【|$)"
    match = re.search(pattern, text, flags=re.S)
    return match.group(1).strip() if match else ""


def split_near_periods(text: str, parts: int) -> List[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n+", text) if paragraph.strip()]
    if len(paragraphs) == parts:
        return paragraphs
    if len(paragraphs) > parts:
        total = sum(len(paragraph) for paragraph in paragraphs)
        target = max(1, total // parts)
        chunks: List[str] = []
        current: List[str] = []
        current_len = 0
        for index, paragraph in enumerate(paragraphs):
            remaining_paragraphs = len(paragraphs) - index - 1
            remaining_slots = parts - len(chunks) - 1
            current.append(paragraph)
            current_len += len(paragraph)
            if len(chunks) < parts - 1 and current_len >= target and remaining_paragraphs >= remaining_slots:
                chunks.append("\n".join(current).strip())
                current = []
                current_len = 0
        if current:
            chunks.append("\n".join(current).strip())
        while len(chunks) < parts:
            chunks.append("")
        return chunks[:parts]

    sentences = [item.strip() + "。" for item in re.split(r"。+", text) if item.strip()]
    if len(sentences) <= parts:
        return sentences + [""] * (parts - len(sentences))
    total = sum(len(sentence) for sentence in sentences)
    target = max(1, total // parts)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for index, sentence in enumerate(sentences):
        remaining_sentences = len(sentences) - index - 1
        remaining_slots = parts - len(chunks) - 1
        current.append(sentence)
        current_len += len(sentence)
        if (
            len(chunks) < parts - 1
            and current_len >= target
            and remaining_sentences >= remaining_slots
        ):
            chunks.append("".join(current).strip())
            current = []
            current_len = 0
    if current:
        chunks.append("".join(current).strip())
    while len(chunks) < parts:
        chunks.append("")
    if len(chunks) > parts:
        chunks = chunks[: parts - 1] + ["".join(chunks[parts - 1 :]).strip()]
    return chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="1広告1投稿スタイルの試作用カルーセル/動画を生成します。")
    parser.add_argument("--images-only", action="store_true", help="カルーセル画像と確認用一覧だけ生成し、動画は生成しません。")
    parser.add_argument("--body-font", choices=("", "w4", "w5"), default="", help="本文フォントの比較用指定。")
    parser.add_argument("--counter-position", choices=sorted(COUNTER_POSITIONS), default="badge-right", help="1/3などのページ数表示位置。")
    parser.add_argument("--output-name", default="single_ad_post_prototype", help="deliverables配下の出力フォルダ名。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ad = read_first_today_ad()
    company = clean(ad.get("会社名"))
    service_name = clean(ad.get("サービス名"))
    period = clean(ad.get("掲載期間")) or "◯ヶ月"
    screenshot_url = clean(ad.get("広告スクショ"))

    output_dir = ROOT_DIR / "deliverables" / args.output_name
    slides_dir = output_dir / "carousel"
    slides_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = download_image(screenshot_url, output_dir / "source_ad.jpg")
    screenshot = Image.open(screenshot_path)

    business = extract_section(SAMPLE_POST, "ビジネスモデル")
    why = extract_section(SAMPLE_POST, "なぜこの広告が勝ってるか")
    learning = extract_section(SAMPLE_POST, "広告の学び")

    slides = []
    cover = render_dynamic_cover(period, screenshot)
    slides.append(("slide_01_cover.png", cover))
    ad_intro = render_today_ad_slide(company, service_name, screenshot, args.body_font)
    slides.append(("slide_02_ad.png", ad_intro))

    for index, chunk in enumerate(split_near_periods(business, 3), start=1):
        slides.append((f"slide_{index + 2:02d}_business.png", render_segment_text_slide("ビジネスモデル", f"{index}/3", chunk, args.body_font, args.counter_position)))
    for index, chunk in enumerate(split_near_periods(why, 3), start=1):
        slides.append((f"slide_{index + 5:02d}_why.png", render_segment_text_slide("なぜこの広告が勝ってるか", f"{index}/3", chunk, args.body_font, args.counter_position)))
    slides.append(("slide_09_learning.png", render_segment_text_slide("今日の広告の学び", "", learning, args.body_font, args.counter_position)))
    slides.append(("slide_10_ending.png", render_carousel_ending_slide()))

    paths = []
    for filename, image in slides:
        path = slides_dir / filename
        image.save(path)
        paths.append(path)

    video_path = output_dir / "single_ad_post_prototype.mp4"
    if not args.images_only:
        reel_spec = ReelSpec(slide_duration=2.4, fade_duration=0, transition="none", ending_duration=2.0)
        video_paths = paths[:-1]
        pages = [
            ReelPage(path, 1.5 if index == 0 else 2.4)
            for index, path in enumerate(video_paths)
        ]
        write_structured_mp4(pages, video_path, reel_spec, with_bgm=True, bgm_path=select_bgm_source(output_dir))

    caption = (
        "広告分析vol.001\n\n"
        f"引用元：{company} / {service_name} / 掲載期間：{period}"
    )
    (output_dir / "caption.txt").write_text(caption + "\n", encoding="utf-8")
    contact_sheet_path = save_contact_sheet(paths, output_dir / "contact_sheet.png")

    print(output_dir)
    for path in paths:
        print(path)
    if not args.images_only:
        print(video_path)
    print(output_dir / "caption.txt")
    print(contact_sheet_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
