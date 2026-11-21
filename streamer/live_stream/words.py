"""
The words written on the stream's cameras: each printer's name and print, kept in a file per camera that ffmpeg reads
again every frame.
"""
from __future__ import annotations

import os

from .constants import DEFAULT_TEXT_COLOR
from .fonts import font_file
from .squares import _text_box

# The words on a camera shown on its own, taking turns: written on the camera's own picture before it's enlarged, so
# sized to it (28 px on the 720-high stream, whatever the camera's size)
FULL_WORDS = {"size": "h/26", "foot": "h/30", "border": "h/120", "left": "w/128"}


def words_shown(cell: dict | None, show_status: bool) -> bool:
    """Whether a camera's square has its printer's name and print written on it: its own tick (show_words), else the
    stream's (show_status)."""
    own = (cell or {}).get("show_words")
    return own if isinstance(own, bool) else bool(show_status)


def filter_path(path: str) -> str:
    """A file's path as an ffmpeg filter option: forward slashes, and its : and ' escaped."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def camera_words(path: str, size: int | str, foot: int | str, border: int | str = 6, left: int | str = 10, own: dict | None = None,
                 square: tuple[int, int] | None = None) -> str:
    """The drawtext of the words on a camera; `own`: its square's choices (_camera_cell: words_color, words_font, and
    words_place: their band's left edge and foot, as parts of the square's width and height; none: `left` px in, `foot`
    px up). `square`: its size in px; None: the camera's own picture, as ffmpeg sizes it (on its own, taking turns)."""
    own = own or {}
    color = (own.get("words_color") or DEFAULT_TEXT_COLOR).lstrip("#")
    place = own.get("words_place")
    if place:
        if square:
            x, y = f"{round(place['x'] * square[0])}+{border}", f"{round(place['y'] * square[1])}-th-{border}"
        else:
            x, y = f"w*{place['x']}+{border}", f"h*{place['y']}-th-{border}"
    else:
        x, y = left, f"h-th-{foot}"
    return (f"drawtext=fontfile={filter_path(font_file(own.get('words_font')))}:textfile={filter_path(path)}:reload=1:expansion=none"
            f":fontsize={size}:fontcolor=0x{color}:box=1:boxcolor=black@0.6:boxborderw={border}:x={x}:y={y}")


def full_words(own: dict) -> dict:
    """The sizes of a camera's words when it's shown on its own: its own size (a part of the camera's own picture's
    height) when chosen, else FULL_WORDS."""
    f = own.get("words_size")
    if not f:
        return FULL_WORDS
    return {"size": f"h*{f}", "foot": f"h*{f * 2 / 3:.4f}", "border": f"h*{f / 3:.4f}", "left": f"h*{f / 2:.4f}"}


def status_line(label: str, p: dict) -> str:
    """What's written under a camera: "VT 1190 · Printing 42% · benchy.gcode", "VT 1190 · Idle", or "VT 1190 · Status
    unknown" when the printer couldn't be asked (not answering, or refusing Farmhand's login)."""
    if p.get("state"):
        pct = p.get("progress")
        line = f"{label} · {'Paused' if p['state'] == 'paused' else 'Printing'}"
        if isinstance(pct, (int, float)):
            line += f" {round(pct)}%"
        if p.get("file"):
            line += f" · {os.path.basename(str(p['file']))[:60]}"
        return line
    if p.get("error"):
        return f"{label} · Status unknown"
    return f"{label} · Idle"


def placed_words(layer: dict, w: int | None = None, h: int | None = None, font: str | None = None, color: str | None = None,
                 least: int = 10, band_least: int = 4) -> str:
    """
    The drawtext of a piece of text written over a camera (staging.staged_layers' {"words": its file, "box", "font",
    "color", "band"}): centred on its box's spot, its height a part of the picture's, each line centred as the page shows
    it, on a dark band when chosen. `w`, `h`: the square's size in px (at least `least` px high, the band's border at
    least `band_least`); None: the camera's own picture, as ffmpeg sizes it (a camera on its own, taking turns, before
    it's enlarged). `font`, `color`: the stream's, unless the text has its own.
    """
    tb = _text_box(layer.get("box"))
    if w is None or h is None:
        size, spacing = f"h*{tb['size']}", ""
        x, y, border = f"w*{tb['x']}-text_w/2", f"h*{tb['y']}-text_h/2", f"h*{tb['size'] / 5:.4f}"
    else:
        big = round(tb["size"] * h)
        size, spacing = max(least, big), f":line_spacing={big // 4}"
        x, y, border = f"{round(tb['x'] * w)}-text_w/2", f"{round(tb['y'] * h)}-text_h/2", max(band_least, big // 5)
    band = f":box=1:boxcolor=black@0.5:boxborderw={border}" if layer.get("band") else ""
    return (f"drawtext=fontfile={filter_path(font_file(layer.get('font') or font))}:textfile={filter_path(layer['words'])}:reload=1:expansion=none"
            f":fontsize={size}:fontcolor=0x{(layer.get('color') or color or DEFAULT_TEXT_COLOR).lstrip('#')}{spacing}"
            f":text_align=C:x={x}:y={y}{band}")
