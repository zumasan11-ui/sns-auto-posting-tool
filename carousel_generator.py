import argparse
import re
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont


CANVAS_SIZE = (1080, 1350)
TOP_HEIGHT = 500
MARGIN_X = 72
CAROUSEL_COVER_TITLE_TEMPLATE = "なぜこの広告は{period}回っているのか？"
CAROUSEL_MAX_ADS = 4
CAROUSEL_ENDING_LINES = ("続きは", "プロフィールへ")
FONT_PATHS = [
    "assets/fonts/NotoSansJP-Regular.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]
CAROUSEL_BODY_FONT_PATHS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "assets/fonts/NotoSansJP-Regular.ttf",
]
BOLD_FONT_PATHS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
    "assets/fonts/NotoSansJP-Bold.ttf",
    "assets/fonts/NotoSansJP-Regular.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]


def load_font(size: int, index: int = 0, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = BOLD_FONT_PATHS if bold else FONT_PATHS
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size, index=index)
    return ImageFont.load_default()


def load_carousel_body_font(size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    for path in CAROUSEL_BODY_FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size, index=index)
    return load_font(size)


def normalize_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def split_japanese_line(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if font.getlength(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = char
    if current:
        lines.append(current)
    return lines


def wrap_text(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int
) -> List[str]:
    wrapped: List[str] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            wrapped.append("")
            continue
        wrapped.extend(split_japanese_line(raw_line.strip(), font, max_width))

    if len(wrapped) <= max_lines:
        return wrapped

    clipped = wrapped[:max_lines]
    clipped[-1] = clipped[-1].rstrip("。 、,.") + "..."
    return clipped


def fit_text_lines(
    text: str,
    max_width: int,
    max_height: int,
    preferred_size: int,
    min_size: int,
    line_height_ratio: float = 1.55,
    max_lines: int = 20,
    bold: bool = False,
) -> Tuple[ImageFont.FreeTypeFont, List[str], int, bool]:
    loader = load_font if bold else load_carousel_body_font
    preferred_font = loader(preferred_size, bold=bold) if bold else loader(preferred_size)
    preferred_line_height = round(preferred_size * line_height_ratio)
    full_preferred_lines = wrap_text(normalize_text(text), preferred_font, max_width, 10000)
    preferred_overflows = len(full_preferred_lines) * preferred_line_height > max_height or len(full_preferred_lines) > max_lines
    if not preferred_overflows and all(preferred_font.getlength(line) <= max_width for line in full_preferred_lines):
        return preferred_font, full_preferred_lines, preferred_line_height, False

    for size in range(preferred_size, min_size - 1, -2):
        font = loader(size, bold=bold) if bold else loader(size)
        line_height = round(size * line_height_ratio)
        full_lines = wrap_text(normalize_text(text), font, max_width, 10000)
        lines = full_lines[:max_lines]
        total_height = len(lines) * line_height
        if len(full_lines) <= max_lines and total_height <= max_height and all(font.getlength(line) <= max_width for line in lines):
            return font, lines, line_height, True

    font = loader(min_size, bold=bold) if bold else loader(min_size)
    line_height = round(min_size * line_height_ratio)
    return font, wrap_text(normalize_text(text), font, max_width, max(1, max_height // line_height)), line_height, True


def draw_centered_block(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    box: Tuple[int, int, int, int],
    font: ImageFont.FreeTypeFont,
    fill: str,
    line_height: int,
) -> None:
    x1, y1, x2, y2 = box
    total_height = len(lines) * line_height
    y = y1 + max(0, (y2 - y1 - total_height) // 2)
    for line in lines:
        draw.text((x1, y), line, font=font, fill=fill)
        y += line_height


def fit_cover(image: Image.Image, size: Tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    src = image.convert("RGB")
    scale = max(target_w / src.width, target_h / src.height)
    resized = src.resize((round(src.width * scale), round(src.height * scale)), Image.LANCZOS)
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def fit_contain(image: Image.Image, size: Tuple[int, int], background: str = "#ffffff") -> Image.Image:
    target_w, target_h = size
    src = image.convert("RGB")
    scale = min(target_w / src.width, target_h / src.height)
    resized = src.resize((round(src.width * scale), round(src.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", size, background)
    left = (target_w - resized.width) // 2
    top = (target_h - resized.height) // 2
    canvas.paste(resized, (left, top))
    return canvas


def derive_slides(source_text: str) -> List[Tuple[str, str]]:
    text = normalize_text(source_text)
    base_title = "伴走型の訴求を強める"
    cleaned = re.sub(r"^[①-⑳0-9０-９.．、\s]+", "", text.splitlines()[0]).strip() if text else base_title
    if cleaned:
        base_title = cleaned

    return [
        ("改善テーマ", base_title),
        ("今の見え方", "「二人三脚」は良い言葉だけど、見た瞬間にスクールの伴走感までは伝わりにくい。"),
        ("伝えるべきこと", "ここは一人で頑張る講座ではなく、合格まで横についてくれる場所だと分かる表現にする。"),
        ("弱いポイント", "抽象的な安心感だけだと、具体的に何をしてくれるのかが想像しにくい。"),
        ("入れたい具体性", "月何回の1対1指導、質問対応、学習計画の見直しなど、実際のサポート内容を見せる。"),
        ("コピー案", "「二人三脚」よりも「月◯回の1対1指導で伴走」の方が、サービス内容がすぐ伝わる。"),
        ("受講者目線", "宅建は独学で詰まりやすい。だから、迷った時に相談できる人がいる価値を前に出す。"),
        ("バナーでの見せ方", "上部コピーは短く、下部の補足で「専任ナビゲーター」「個別指導」などを補強する。"),
        ("改善後の印象", "ただ仲良く支える印象から、合格まで管理してくれるスクールという印象に変わる。"),
        ("次に試す案", "「月◯回の1対1伴走」「学習計画まで個別サポート」など、制度が見える言葉でABテストする。"),
    ]


def draw_multiline(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    xy: Tuple[int, int],
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int],
    line_height: int,
) -> None:
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height


def render_slide(
    index: int,
    total: int,
    heading: str,
    body: str,
    screenshot: Image.Image,
) -> Image.Image:
    canvas = Image.new("RGB", CANVAS_SIZE, "#ffffff")
    draw = ImageDraw.Draw(canvas)

    label_font = load_font(42, bold=True)
    body_font, body_lines, body_line_height, body_resized = fit_text_lines(
        body,
        CANVAS_SIZE[0] - MARGIN_X * 2,
        250,
        preferred_size=42,
        min_size=31,
        line_height_ratio=1.57,
        max_lines=6,
    )

    accent = "#111111"
    black = "#111111"
    gray = "#222222"

    label = heading.strip()
    label_bbox = draw.textbbox((0, 0), label, font=label_font)
    label_w = label_bbox[2] - label_bbox[0]
    label_h = label_bbox[3] - label_bbox[1]
    badge = (MARGIN_X, 76, MARGIN_X + label_w + 70, 140)
    label_x = badge[0] + (badge[2] - badge[0] - label_w) / 2 - label_bbox[0]
    label_y = badge[1] + (badge[3] - badge[1] - label_h) / 2 - label_bbox[1] + 3
    draw.rounded_rectangle(badge, radius=32, fill=accent)
    draw.text((label_x, label_y), label, font=label_font, fill="#ffffff")

    if body_resized:
        draw_centered_block(
            draw,
            body_lines,
            (MARGIN_X, 205, CANVAS_SIZE[0] - MARGIN_X, 470),
            body_font,
            gray,
            body_line_height,
        )
    else:
        draw_multiline(draw, body_lines, (MARGIN_X, 235), body_font, gray, body_line_height)

    draw.line((0, TOP_HEIGHT, CANVAS_SIZE[0], TOP_HEIGHT), fill="#e5e5e5", width=2)

    ad_margin_x = 155
    ad_margin_y = 40
    ad_box = (
        ad_margin_x,
        TOP_HEIGHT + ad_margin_y,
        CANVAS_SIZE[0] - ad_margin_x,
        1310,
    )
    ad_area = fit_contain(
        screenshot,
        (ad_box[2] - ad_box[0], ad_box[3] - ad_box[1]),
        background="#ffffff",
    )
    canvas.paste(ad_area, (ad_box[0], ad_box[1]))

    return canvas


def render_text_slide(label: str, body: str) -> Image.Image:
    canvas = Image.new("RGB", CANVAS_SIZE, "#ffffff")
    draw = ImageDraw.Draw(canvas)
    label_font = load_font(42, bold=True)
    body_font, body_lines, body_line_height, body_resized = fit_text_lines(
        body,
        CANVAS_SIZE[0] - MARGIN_X * 2,
        980,
        preferred_size=42,
        min_size=30,
        line_height_ratio=1.57,
        max_lines=14,
    )

    label_bbox = draw.textbbox((0, 0), label, font=label_font)
    label_w = label_bbox[2] - label_bbox[0]
    label_h = label_bbox[3] - label_bbox[1]
    badge = (MARGIN_X, 76, MARGIN_X + label_w + 70, 140)
    label_x = badge[0] + (badge[2] - badge[0] - label_w) / 2 - label_bbox[0]
    label_y = badge[1] + (badge[3] - badge[1] - label_h) / 2 - label_bbox[1] + 3
    draw.rounded_rectangle(badge, radius=32, fill="#111111")
    draw.text((label_x, label_y), label, font=label_font, fill="#ffffff")

    if body_resized:
        draw_centered_block(
            draw,
            body_lines,
            (MARGIN_X, 210, CANVAS_SIZE[0] - MARGIN_X, 1240),
            body_font,
            "#222222",
            body_line_height,
        )
    else:
        draw_multiline(draw, body_lines, (MARGIN_X, 235), body_font, "#222222", body_line_height)
    return canvas


def render_carousel_cover_slide(title: str, screenshot: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", CANVAS_SIZE, "#ffffff")
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(78, bold=True)
    highlight_font = load_font(116, bold=True)
    sub_font = load_carousel_body_font(36)
    black = "#111111"
    gray = "#222222"

    title_match = re.fullmatch(r"なぜこの広告は(.+)回っているのか？", title)
    period = title_match.group(1) if title_match else "◯ヶ月"
    top = 115
    draw.text((MARGIN_X, top), "なぜこの広告は", font=title_font, fill=black)
    badge_top = top + 98
    badge = (MARGIN_X, badge_top, MARGIN_X + 360, badge_top + 132)
    draw.rounded_rectangle(badge, radius=24, fill=black)
    bbox = draw.textbbox((0, 0), period, font=highlight_font)
    tx = badge[0] + (badge[2] - badge[0] - (bbox[2] - bbox[0])) / 2 - bbox[0]
    ty = badge[1] + (badge[3] - badge[1] - (bbox[3] - bbox[1])) / 2 - bbox[1] + 3
    draw.text((tx, ty), period, font=highlight_font, fill="#ffffff")
    draw.text((MARGIN_X, badge_top + 154), "回っているのか？", font=title_font, fill=black)
    draw.text((MARGIN_X, 490), "広告クリエイティブを分解して考える", font=sub_font, fill=gray)

    image_box = (120, 575, CANVAS_SIZE[0] - 120, 1260)
    image_area = fit_contain(
        screenshot,
        (image_box[2] - image_box[0], image_box[3] - image_box[1]),
        background="#ffffff",
    )
    canvas.paste(image_area, (image_box[0], image_box[1]))
    return canvas


def render_carousel_ending_slide() -> Image.Image:
    canvas = Image.new("RGB", CANVAS_SIZE, "#ffffff")
    draw = ImageDraw.Draw(canvas)
    black = "#111111"
    gray = "#222222"
    main_font = load_font(78, bold=True)
    accent_font = load_font(54, bold=True)
    body_font = load_carousel_body_font(38)

    badge = (MARGIN_X, 110, MARGIN_X + 300, 176)
    draw.rounded_rectangle(badge, radius=33, fill=black)
    label = "続きはこちら"
    label_bbox = draw.textbbox((0, 0), label, font=body_font)
    label_x = badge[0] + (badge[2] - badge[0] - (label_bbox[2] - label_bbox[0])) / 2 - label_bbox[0]
    label_y = badge[1] + (badge[3] - badge[1] - (label_bbox[3] - label_bbox[1])) / 2 - label_bbox[1] + 2
    draw.text((label_x, label_y), label, font=body_font, fill="#ffffff")

    y = 330
    for line in CAROUSEL_ENDING_LINES:
        bbox = draw.textbbox((0, 0), line, font=main_font)
        draw.text(((CANVAS_SIZE[0] - (bbox[2] - bbox[0])) / 2 - bbox[0], y - bbox[1]), line, font=main_font, fill=black)
        y += 100

    sub = "毎日広告分析を発信中"
    bbox = draw.textbbox((0, 0), sub, font=accent_font)
    draw.text(((CANVAS_SIZE[0] - (bbox[2] - bbox[0])) / 2 - bbox[0], 640 - bbox[1]), sub, font=accent_font, fill=black)

    card = (150, 820, 930, 1045)
    draw.rounded_rectangle(card, radius=28, outline=black, width=4)
    draw.ellipse((205, 880, 300, 975), outline=black, width=4)
    draw.text((335, 865), "プロフィール欄から", font=body_font, fill=gray)
    draw.text((335, 930), "詳しい分析・事例を見る", font=body_font, fill=gray)

    footer = "保存して、あとで見返す"
    bbox = draw.textbbox((0, 0), footer, font=body_font)
    draw.text(((CANVAS_SIZE[0] - (bbox[2] - bbox[0])) / 2 - bbox[0], 1175 - bbox[1]), footer, font=body_font, fill=gray)
    return canvas


def save_pdf(slides: Iterable[Image.Image], output_path: Path) -> None:
    images = [slide.convert("RGB") for slide in slides]
    if not images:
        raise RuntimeError("PDF化するスライドがありません。")
    images[0].save(output_path, save_all=True, append_images=images[1:])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="広告スクショからSNS用カルーセル画像を生成します。")
    parser.add_argument("--image", required=True, help="広告スクショ画像のパス")
    parser.add_argument("--text", required=True, help="分析文")
    parser.add_argument(
        "--output-dir",
        default="deliverables/carousel_test",
        help="PNGとPDFを書き出すディレクトリ",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="書き出すスライド枚数。テンプレ確認だけなら 1 を指定します。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    screenshot = Image.open(args.image)
    slides_data = derive_slides(args.text)[: args.count]
    rendered: List[Image.Image] = []

    for index, (heading, body) in enumerate(slides_data, start=1):
        slide = render_slide(index, len(slides_data), heading, body, screenshot)
        path = output_dir / f"slide_{index:02d}.png"
        slide.save(path)
        rendered.append(slide)
        print(path)

    pdf_path = output_dir / "linkedin_carousel.pdf"
    save_pdf(rendered, pdf_path)
    print(pdf_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
