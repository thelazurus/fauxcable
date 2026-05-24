from __future__ import annotations
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_FONTS_DIR = Path("fonts")
POSTER_W, POSTER_H = 300, 450


def list_fonts() -> list[str]:
    if not _FONTS_DIR.exists():
        return []
    return sorted(
        f.name for f in _FONTS_DIR.iterdir()
        if f.suffix.lower() in (".ttf", ".otf")
    )


def _load_font(font_name: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_name:
        # Use only the bare filename — Path().name strips any directory component,
        # preventing traversal like "../etc/passwd" or "/etc/passwd" (absolute paths
        # would otherwise override the base dir in Python's pathlib join).
        safe_name = Path(font_name).name
        if safe_name:
            path = _FONTS_DIR / safe_name
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    pass  # not a valid font file — fall through to default
    return ImageFont.load_default(size=size)


def render_poster(
    label: str,
    bg_color: str = "#1e293b",
    bg_image_bytes: bytes | None = None,
    blur_radius: int = 0,
    text_color: str = "#ffffff",
    font_name: str | None = None,
    font_size: int = 40,
    text_position: str = "center",
) -> bytes:
    img = Image.new("RGB", (POSTER_W, POSTER_H), bg_color)

    if bg_image_bytes:
        try:
            bg = Image.open(io.BytesIO(bg_image_bytes)).convert("RGB")
            bg = bg.resize((POSTER_W, POSTER_H), Image.LANCZOS)
            img = bg
        except Exception:
            pass

    if blur_radius > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    draw = ImageDraw.Draw(img)
    font = _load_font(font_name, font_size)

    bbox = draw.textbbox((0, 0), label, font=font, stroke_width=2)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (POSTER_W - tw) / 2

    if text_position == "top":
        ty = POSTER_H * 0.08
    elif text_position == "bottom":
        ty = POSTER_H * 0.82 - th
    else:
        ty = (POSTER_H - th) / 2

    draw.text(
        (tx, ty), label, font=font,
        fill=text_color,
        stroke_width=2,
        stroke_fill=(0, 0, 0),
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
