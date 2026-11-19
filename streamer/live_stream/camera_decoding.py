"""
Where the cameras' video is unpacked and fitted to the stream's size: on an NVIDIA card (its video decoder, NVDEC, and
CUDA's scaler), or on the processor as before. The card is far cheaper: each camera's H.264 is unpacked and fitted on
the card, and handed to the picture as it is (raw frames, feeders.py), so no camera is turned into JPEGs and back.

FARMHAND_STREAM_CAMERAS: "auto" (the default: the card when it works here, else the processor) or "cpu" (always the
processor). The card is tried first with a moment's H.264 through its decoder and scaler; what that finds is remembered
until the plugin starts again, and where it fell back to the processor, decoding() says why, for the page. A single
camera the card can't unpack (feeders.py) is unpacked on the processor on its own, the others staying on the card.
"""
from __future__ import annotations

import os
import subprocess

from .constants import FPS

CAMERAS_SETTING = "FARMHAND_STREAM_CAMERAS"
GPU, CPU = "gpu", "cpu"
_found: dict[str, tuple[str, str | None]] = {}  # ffmpeg -> (GPU or CPU, why not the card)

# The card's part of each camera's chain: unpacked on the card (-hwaccel cuda, the frames left on it), fitted there
# (scale_cuda, which also gives the planar 4:2:0 the picture reads), then brought back to the processor.
HWACCEL = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]


def gpu_scale(w: int, h: int) -> list[str]:
    return [f"scale_cuda={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:format=yuv420p",
            "hwdownload", "format=yuv420p"]


def _why(said: str) -> str:
    """What the card's test said, as the page puts it."""
    if "PTX JIT" in said or "ptxjitcompiler" in said:
        return ("the NVIDIA driver's libnvidia-ptxjitcompiler.so.1 isn't given to the container (see the README's NVIDIA "
                "card lines)")
    if "libnvcuvid" in said or "cuvid" in said.lower():
        return "the NVIDIA driver's libnvcuvid.so.1 isn't given to the container (see the README's NVIDIA card lines)"
    if "libcuda" in said or "CUDA_ERROR_NO_DEVICE" in said or "Cannot load" in said:
        return "no NVIDIA card or driver is in reach of the container"
    last = next((l.strip() for l in reversed(said.splitlines()) if l.strip()), "")
    return f"the NVIDIA card's video decoder didn't work here ({last[:200]})" if last else "the NVIDIA card's video decoder didn't work here"


def _gpu_works(ffmpeg: str) -> tuple[bool, str | None]:
    """Whether a camera's H.264 can be unpacked and fitted on an NVIDIA card here: a moment's H.264 made on the
    processor, then put through the card's decoder and scaler. (False, why) when it can't."""
    try:
        made = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=320x240:r={FPS}",
             "-frames:v", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "h264", "pipe:1"],
            capture_output=True, timeout=20,
        )
        if made.returncode != 0 or not made.stdout:
            return False, "the test video couldn't be made (" + made.stderr.decode("utf-8", "replace").strip()[-200:] + ")"
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", *HWACCEL, "-f", "h264", "-i", "pipe:0",
             "-vf", ",".join(gpu_scale(160, 120)), "-f", "null", "-"],
            input=made.stdout, capture_output=True, timeout=60,  # (the scaler is compiled for the card the first time)
        )
        if r.returncode == 0:
            return True, None
        return False, _why(r.stderr.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"the NVIDIA card couldn't be tried ({e})"


def decoding(ffmpeg: str) -> tuple[str, str | None]:
    """(GPU or CPU, why not the card: None when it is, or when the processor was asked for): where the cameras are
    unpacked (see FARMHAND_STREAM_CAMERAS)."""
    wanted = (os.environ.get(CAMERAS_SETTING) or "auto").strip().lower()
    if wanted == CPU:
        return CPU, None
    if ffmpeg not in _found:
        works, why = _gpu_works(ffmpeg)
        _found[ffmpeg] = (GPU, None) if works else (CPU, why)
        print(f"stream: cameras unpacked on the {'NVIDIA card' if works else 'processor'}" + (f": {why}" if why else ""), flush=True)
    return _found[ffmpeg]


def forget(ffmpeg: str) -> None:
    """What the card's test found is forgotten (the stream failed): the next Go live or new layout tries it again."""
    _found.pop(ffmpeg, None)
