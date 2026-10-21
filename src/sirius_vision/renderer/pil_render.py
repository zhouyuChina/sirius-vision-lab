"""Deterministic repainting of solid text bands."""
import io
import logging
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def _has_glyphs(font: ImageFont.FreeTypeFont, text: str) -> bool:
    # FreeType substitutes the same missing-glyph box for unsupported characters.
    missing = font.getmask(chr(0x10FFFF))
    missing_signature = (missing.size, bytes(missing))
    for char in set(text):
        if char.isspace():
            continue
        mask = font.getmask(char)
        if (mask.size, bytes(mask)) == missing_signature:
            return False
    return True


def render_pil(image: bytes, items: list[dict], font_path: str) -> tuple[bytes, list[int], list[int], list[str]]:
    with Image.open(io.BytesIO(image)) as source:
        canvas = source.convert('RGB')
    draw = ImageDraw.Draw(canvas)
    rendered, skipped, warnings = [], [], []
    for item in items:
        if item['background_type'] != 'solid_band' or item['band_bbox'] is None:
            skipped.append(item['id'])
            continue
        left, top, right, bottom = item['band_bbox']
        box = (round(left * canvas.width / 1000), round(top * canvas.height / 1000),
               round(right * canvas.width / 1000), round(bottom * canvas.height / 1000))
        if box[2] <= box[0] or box[3] <= box[1]:
            skipped.append(item['id'])
            warnings.append(f"Invalid band rectangle for item {item['id']}")
            continue
        width, height = box[2] - box[0], box[3] - box[1]
        size = max(10, int(height * .52))
        while True:
            try:
                font = ImageFont.truetype(font_path, size)
            except OSError:
                font = ImageFont.load_default(size=size)
            if not _has_glyphs(font, item['translated_text']):
                raise ValueError('Font lacks required glyphs')
            text_box = draw.textbbox((0, 0), item['translated_text'], font=font)
            text_width, text_height = text_box[2] - text_box[0], text_box[3] - text_box[1]
            if (text_width <= width and text_height <= height) or size <= 10:
                break
            size = max(10, size - 2)
        if text_width > width or text_height > height:
            warnings.append(f"Text overflows band for item {item['id']}")
            logger.warning('render_text_overflow item_id=%s', item['id'])
        x = box[0] + (width - text_width) / 2 - text_box[0]
        y = box[1] + (height - text_height) / 2 - text_box[1]
        draw.rectangle(box, fill=item['bg_hex'])
        draw.text((x, y), item['translated_text'], font=font, fill=item['text_hex'])
        rendered.append(item['id'])
    output = io.BytesIO()
    canvas.save(output, format='PNG')
    return output.getvalue(), rendered, skipped, warnings
