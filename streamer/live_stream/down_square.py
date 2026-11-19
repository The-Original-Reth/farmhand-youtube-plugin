"""
A camera's "Camera down" square, shown while it sends nothing (feeders.DOWN_AFTER): drawn with Pillow at the camera's
own size, in the square's background colour, "Camera down" in the chosen font and words colour, the printer's name
under it. A JPEG, or a raw frame when the cameras are handed over raw (raw_frames.py).
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from . import raw_frames


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def down_frame(size: tuple[int, int], background: str, font_file: str, color: str, name: str, raw: bool = False) -> bytes:
    """A JPEG of `size` (`raw`: a raw frame): the background colour, "Camera down" in the middle, the printer's name
    under it."""
    w, h = size
    picture = Image.new("RGB", (w, h), "#" + background.lstrip("#"))
    draw = ImageDraw.Draw(picture)
    ink = "#" + color.lstrip("#")
    big, small = _font(font_file, max(10, h // 9)), _font(font_file, max(8, h // 16))
    for text, font, y in (("Camera down", big, h * 0.42), (name, small, h * 0.58)):
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        draw.text(((w - tw) / 2 - box[0], y - th / 2 - box[1]), text, font=font, fill=ink)
    if raw:
        return raw_frames.from_picture(picture)
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=85)
    return out.getvalue()


class DownFrames:
    """One camera's "Camera down" frame for a layout, drawn again only when the camera's size changes (`raw`: raw
    frames, else JPEGs)."""

    def __init__(self, background: str, font_file: str, color: str, name: str, raw: bool = False):
        self._look = (background, font_file, color, name)
        self.raw = raw
        self._made: dict[tuple[int, int], bytes] = {}

    def at(self, size: tuple[int, int]) -> bytes:
        if size not in self._made:
            self._made = {size: down_frame(size, *self._look, raw=self.raw)}
        return self._made[size]
