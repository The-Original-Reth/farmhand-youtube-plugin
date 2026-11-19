"""
How the picture is compressed for sending: on the processor (libx264, fast settings for live), or on an NVIDIA card's
own video encoder (NVENC), which leaves the processor to read the cameras and lay out the grid. Both send H.264 at the
same bitrate, so YouTube sees the same stream.
"""
from __future__ import annotations

import os
import subprocess

from .constants import FPS

ENCODERS = {
    "cpu": ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency"],
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr"],
}

# FARMHAND_STREAM_ENCODER: "auto" (the default: an NVIDIA card's encoder when one works here, else the processor),
# "nvenc" or "cpu" to say which. What "auto" finds is remembered until the picture fails (encoder(fresh=True): asked
# again, so a card that won't open another session is left for the processor, and one that will again is used again).
ENCODER_SETTING = "FARMHAND_STREAM_ENCODER"
_found_encoder: dict[str, str] = {}
# What ffmpeg says when the card won't compress (no session free, the card or its driver gone)
NVENC_TROUBLE = ("nvenc", "openencodesession", "no capable devices", "cuda", "nvidia", "incompatible client key")


def nvenc_trouble(said) -> bool:
    """Whether these lines from a picture ffmpeg say the NVIDIA card wouldn't compress it."""
    return any(w in (line or "").lower() for line in said for w in NVENC_TROUBLE)


def encoder_args(encoder: str) -> list[str]:
    return list(ENCODERS.get(encoder, ENCODERS["cpu"]))


def compress_args(encoder: str, kbps: int) -> list[str]:
    """How every picture the stream sends is compressed: the live picture and the holding screen alike (the sender
    passes both on unchanged, so YouTube must see the same video and sound throughout): H.264 at FPS, a keyframe every
    two seconds, a steady `kbps`; AAC sound at 128 kbps, 44.1 kHz."""
    return [*encoder_args(encoder), "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2),
            "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100"]


def _nvenc_works(ffmpeg: str) -> bool:
    """Whether ffmpeg can compress with an NVIDIA card here: a moment's black picture through it (no card, no driver
    in reach, or an ffmpeg without it: no)."""
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=256x144:r={FPS}",
             "-frames:v", "5", *encoder_args("nvenc"), "-f", "null", "-"],
            capture_output=True, timeout=20,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def encoder(ffmpeg: str, fresh: bool = False) -> str:
    """"nvenc" or "cpu": how the stream is compressed (see FARMHAND_STREAM_ENCODER). `fresh`: the card tried again
    rather than what was found before."""
    wanted = (os.environ.get(ENCODER_SETTING) or "auto").strip().lower()
    if wanted in ("cpu", "nvenc"):
        return wanted
    if fresh or ffmpeg not in _found_encoder:
        _found_encoder[ffmpeg] = "nvenc" if _nvenc_works(ffmpeg) else "cpu"
    return _found_encoder[ffmpeg]
