"""Картинки к фразам: проверка, пережатие и хранение в ``DATA_DIR/media``."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 15 * 1024 * 1024  # фото с телефона бывают крупными; храним пережатое
MAX_SIDE = 1600
QUALITY = 85
NAME = re.compile(r"^[A-Za-z0-9]{1,16}-[0-9a-f]{12}\.jpg$")


class BadImage(ValueError):
    pass


def store(data: bytes, media_dir: Path, slug: str) -> str:
    """Сохраняет картинку как JPEG не больше MAX_SIDE по длинной стороне; возвращает имя.

    Пережатие заодно убирает EXIF с геометками телефона; поворот из EXIF применяется.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (20, 17, 15))
                background.paste(image, mask=image.getchannel("A"))
                image = background
            image = image.convert("RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise BadImage("это не картинка или файл повреждён") from error
    jpeg = out.getvalue()
    name = f"{slug}-{hashlib.sha256(jpeg).hexdigest()[:12]}.jpg"
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp = media_dir / f"{name}.tmp"
    tmp.write_bytes(jpeg)
    tmp.replace(media_dir / name)
    return name


def remove(media_dir: Path, name: str | None) -> None:
    if name and NAME.match(name):
        (media_dir / name).unlink(missing_ok=True)
