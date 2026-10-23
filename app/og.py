"""Открытки на Pillow: превью для соцсетей (1200×630) и квадрат для скачивания (1080×1080)."""

from __future__ import annotations

import io
import random
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.typo import NBSP, typograph

FONTS = Path(__file__).resolve().parent / "static" / "fonts"
SERIF = FONTS / "PTSerif-Regular.ttf"
SANS = FONTS / "PTSans-Regular.ttf"
SANS_BOLD = FONTS / "PTSans-Bold.ttf"

FORMATS: dict[str, tuple[int, int]] = {"wide": (1200, 630), "square": (1080, 1080)}
BACKGROUND = (20, 17, 15)
TEXT_COLOR = (239, 232, 220)
MUTED = (150, 140, 128)
ACCENTS: dict[str | None, tuple[int, int, int]] = {
    "dark": (196, 74, 70),
    "fairy": (214, 172, 86),
    "cyber": (72, 200, 208),
    "city": (214, 142, 92),
    "myth": (152, 130, 220),
    None: (214, 172, 86),
}
# Доля высоты текстового поля, которую должна занять фраза.
FILL_MIN, FILL_MAX = 0.60, 0.75


@cache
def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def wrap(text: str, face: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """Перенос по ширине. Рвём строки только по обычным пробелам — не по неразрывным."""
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and face.getlength(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def layout(text: str, width: int, height: int) -> tuple[int, list[str], int]:
    """Наибольший кегль, при котором фраза влезает в поле и занимает не больше FILL_MAX высоты.

    Возвращает (кегль, строки, межстрочный шаг).
    """
    low, high = 16, 160
    best = (low, wrap(text, font(SERIF, low), width), int(low * 1.3))
    while low <= high:
        size = (low + high) // 2
        face = font(SERIF, size)
        lines = wrap(text, face, width)
        step = int(size * 1.3)
        fits_width = all(face.getlength(line) <= width for line in lines)
        if fits_width and step * len(lines) <= height * FILL_MAX:
            best = (size, lines, step)
            low = size + 1
        else:
            high = size - 1
    return best


def _background(size: tuple[int, int], seed: str) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, BACKGROUND)
    rng = random.Random(seed)
    noise = Image.effect_noise(size, 40).point(lambda v: 128 + (v - 128) // 3)
    image = Image.blend(image, Image.merge("RGB", (noise,) * 3), 0.06)
    # Виньетка: радиальный градиент, растянутый на кадр и размытый.
    # Считаем на маленьком кадре и растягиваем: размытие полноразмерной маски — секунды.
    small = (max(1, width // 8), max(1, height // 8))
    vignette = Image.radial_gradient("L").resize(small).filter(ImageFilter.GaussianBlur(5))
    vignette = vignette.point(lambda v: int(min(255, v * 1.1) * 0.75)).resize(size, Image.BILINEAR)
    shadow = Image.new("RGB", size, (0, 0, 0))
    image = Image.composite(shadow, image, vignette)
    # Едва заметная «пыль» — детерминированная по slug, чтобы открытки различались.
    draw = ImageDraw.Draw(image)
    for _ in range(width * height // 6000):
        x, y = rng.randrange(width), rng.randrange(height)
        tone = rng.randrange(40, 70)
        draw.point((x, y), fill=(tone, tone - 4, tone - 8))
    return image


def render_card(
    text: str, preset_title: str, tone: str | None, fmt: str, domain: str, seed: str = ""
) -> bytes:
    if fmt not in FORMATS:
        raise ValueError(f"неизвестный формат {fmt!r}")
    width, height = FORMATS[fmt]
    accent = ACCENTS.get(tone, ACCENTS[None])
    image = _background((width, height), seed or text)
    draw = ImageDraw.Draw(image)

    margin_x = int(width * 0.09)
    top = int(height * 0.16)
    bottom = int(height * 0.14)
    field_height = height - top - bottom

    # Название пресета мелко сверху, с акцентной чертой.
    label = preset_title.upper()
    label_font = font(SANS_BOLD, max(18, height // 30))
    spaced = " ".join(label)
    label_y = int(height * 0.07)
    draw.text((margin_x, label_y), spaced, font=label_font, fill=accent)
    rule_y = label_y + label_font.size + 14
    draw.line((margin_x, rule_y, margin_x + 64, rule_y), fill=accent, width=3)

    # Фраза: кегль подобран под 60–75% высоты поля.
    phrase = typograph(text)
    size, lines, step = layout(phrase, width - 2 * margin_x, field_height)
    face = font(SERIF, size)
    block = step * len(lines)
    y = top + (field_height - block) // 2
    for line in lines:
        draw.text((margin_x, y), line.replace(NBSP, " "), font=face, fill=TEXT_COLOR)
        y += step

    # Водяной знак с доменом снизу.
    mark_font = font(SANS, max(16, height // 36))
    mark_width = draw.textlength(domain, font=mark_font)
    draw.text(
        (width - margin_x - mark_width, height - int(height * 0.08) - mark_font.size),
        domain,
        font=mark_font,
        fill=MUTED,
    )

    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=6)
    return buffer.getvalue()


def fill_ratio(text: str, fmt: str) -> float:
    """Какую долю высоты поля занимает фраза — для проверки подбора кегля."""
    width, height = FORMATS[fmt]
    field_height = height - int(height * 0.16) - int(height * 0.14)
    _, lines, step = layout(typograph(text), width - 2 * int(width * 0.09), field_height)
    return step * len(lines) / field_height
