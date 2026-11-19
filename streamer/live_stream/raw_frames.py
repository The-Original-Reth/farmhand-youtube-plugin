"""
Raw frames, as a camera's feeder hands them over when the cameras are unpacked on an NVIDIA card (camera_decoding.py):
planar 4:2:0 (ffmpeg's yuv420p) at video levels (16-235), a whole frame width x height x 1.5 bytes, the picture
ffmpeg reading them with input_args(). Pillow makes the "Camera down" squares in it, and turns a frame back into a JPEG
for the page's preview.
"""
from __future__ import annotations

import io

from PIL import Image

from .constants import FPS

PIX_FMT = "yuv420p"

# Pillow's YCbCr is JPEG's, full range (0-255); the picture's raw frames are at video levels: Y 16-235, Cb and Cr 16-240
_TO_VIDEO_Y = [16 + round(v * 219 / 255) for v in range(256)]
_TO_VIDEO_C = [16 + round(v * 224 / 255) for v in range(256)]
_TO_FULL_Y = [min(255, max(0, round((v - 16) * 255 / 219))) for v in range(256)]
_TO_FULL_C = [min(255, max(0, round((v - 16) * 255 / 224))) for v in range(256)]


def frame_bytes(size: tuple[int, int]) -> int:
    w, h = size
    return w * h + 2 * ((w // 2) * (h // 2))


def y4m_size(header: bytes) -> tuple[int, int] | None:
    """The (width, height) a YUV4MPEG stream's first line says its frames are (4:2:0 only), or None."""
    words = header.split()
    if not words or words[0] != b"YUV4MPEG2":
        return None
    got = {w[:1]: w[1:] for w in words[1:]}
    if not got.get(b"C", b"420").startswith(b"420"):
        return None
    try:
        return int(got[b"W"]), int(got[b"H"])
    except (KeyError, ValueError):
        return None


def input_args(path: str, size: tuple[int, int]) -> list[str]:
    """How the picture ffmpeg reads a pipe of raw frames of `size`, at FPS."""
    return ["-f", "rawvideo", "-pix_fmt", PIX_FMT, "-video_size", f"{size[0]}x{size[1]}", "-framerate", str(FPS), "-i", path]


def from_picture(picture: Image.Image) -> bytes:
    """A Pillow picture (width and height even) as one raw frame."""
    y, cb, cr = picture.convert("YCbCr").split()
    half = (picture.width // 2, picture.height // 2)
    return (y.point(_TO_VIDEO_Y).tobytes() + cb.resize(half, Image.BILINEAR).point(_TO_VIDEO_C).tobytes()
            + cr.resize(half, Image.BILINEAR).point(_TO_VIDEO_C).tobytes())


def to_jpeg(frame: bytes, size: tuple[int, int], quality: int = 80) -> bytes:
    """One raw frame of `size` as a JPEG half its size (a preview: cheap to make, its colour planes taken as they are)."""
    w, h = size
    half = (w // 2, h // 2)
    n, c = w * h, half[0] * half[1]
    y = Image.frombytes("L", size, bytes(frame[:n])).reduce(2).point(_TO_FULL_Y)
    cb = Image.frombytes("L", half, bytes(frame[n:n + c])).point(_TO_FULL_C)
    cr = Image.frombytes("L", half, bytes(frame[n + c:n + 2 * c])).point(_TO_FULL_C)
    out = io.BytesIO()
    Image.merge("YCbCr", (y, cb, cr)).save(out, "JPEG", quality=quality)
    return out.getvalue()
