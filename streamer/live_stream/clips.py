"""
Clips: the last second to five minutes of what YouTube has been sent, saved as an MP4 in the clips folder.

What goes to YouTube (the delay's buffer hands each piece to the sender, buffer.py) is also kept here, the last
MOST_SECONDS and a little more, by when it went out: so a clip is what YouTube was given, holding screen and all, and
"the last 30 seconds" is the 30 seconds just played out. A clip starts at the picture's first whole frame (a keyframe,
every 2 s) at or just before the time asked for, and is copied as it is (-c copy: no work, no loss) from MPEG-TS into
an MP4 named for when it was taken. It's written beside its final name and renamed into place once whole, so the
folder (a NAS share, say) never shows half a clip.

How long a clip is (CLIP_KEY in the plugin's store) is set on the page: LEAST_SECONDS to MOST_SECONDS.
"""
from __future__ import annotations

import collections
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .relay import keyframe_at

LEAST_SECONDS = 1
MOST_SECONDS = 300
DEFAULT_SECONDS = 3
EXTRA = 10  # seconds kept beyond MOST_SECONDS, so the longest clip can still start at a whole frame
CLIP_KEY = "clip_seconds"
SHOWN = 10  # the newest clips the page lists
SAVE_TIMEOUT = 120  # seconds ffmpeg may take to copy a clip into an MP4 (five minutes of stream, onto a NAS share)
NAME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}-[0-9]{2}-[0-9]{2}( [0-9]+)? \([0-9]+ s\)\.mp4$")


class Recent:
    """What YouTube has been sent lately: (when it went out, MPEG-TS) pieces, the last MOST_SECONDS + EXTRA."""

    def __init__(self, keep: float = MOST_SECONDS + EXTRA, clock=time.monotonic):
        self.keep = keep
        self._clock = clock
        self._lock = threading.Lock()
        self._pieces: collections.deque[tuple[float, bytes]] = collections.deque()

    def add(self, data: bytes) -> None:
        if not data:
            return
        now = self._clock()
        with self._lock:
            self._pieces.append((now, bytes(data)))
            while self._pieces and now - self._pieces[0][0] > self.keep:
                self._pieces.popleft()

    def held(self) -> float:
        """Seconds of what went to YouTube kept now (0 before anything has)."""
        with self._lock:
            return round(self._clock() - self._pieces[0][0], 1) if self._pieces else 0.0

    def last(self, seconds: float) -> tuple[bytes, float]:
        """The last `seconds` sent, from the whole frame at or just before then (the one after it when there's none
        before: the start of what's kept), and how many seconds that is. (b"", 0) when there's nothing."""
        now = self._clock()
        with self._lock:
            pieces = list(self._pieces)
        if not pieces:
            return b"", 0.0
        cut = now - seconds
        first = next((i for i, (sent, _) in enumerate(pieces) if sent >= cut), len(pieces) - 1)
        # a whole frame at or before the cut, else the first after it
        for i in [*range(first, -1, -1), *range(first + 1, len(pieces))]:
            at = keyframe_at(pieces[i][1])
            if at is not None:
                data = pieces[i][1][at:] + b"".join(p for _, p in pieces[i + 1:])
                return data, round(now - pieces[i][0], 1)
        return b"", 0.0

    def clear(self) -> None:
        with self._lock:
            self._pieces.clear()


# What YouTube has been sent, for clips: the one the stream adds to (runner gives it to each delay's buffer)
recent = Recent()


def seconds_of(db) -> int:
    """How long a clip is, as set on the page."""
    try:
        return clamp(int(db.get(CLIP_KEY) or DEFAULT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_SECONDS


def set_seconds(db, seconds: int) -> int:
    if not LEAST_SECONDS <= int(seconds) <= MOST_SECONDS:
        raise ValueError(f"A clip is {LEAST_SECONDS} second to {MOST_SECONDS // 60} minutes long.")
    db.put(CLIP_KEY, str(int(seconds)))
    return int(seconds)


def clamp(seconds: int) -> int:
    return max(LEAST_SECONDS, min(MOST_SECONDS, seconds))


def folder_problem(folder: Path) -> str | None:
    """Why clips can't be saved in `folder` now, in words for the page, or None."""
    if not folder.is_dir():
        return (f"There's no clips folder: give the plugin's container one at {folder} (the NAS's YouTube clips folder, "
                "mounted on its machine), then start it again.")
    if not os.access(folder, os.W_OK):
        return f"The plugin can't write in the clips folder ({folder}): check the share's permissions."
    return None


def listed(folder: Path, most: int = SHOWN) -> list[dict]:
    """The newest clips in the folder: [{name, size (bytes), made (ISO time)}], newest first."""
    try:
        found = [p for p in folder.iterdir() if p.is_file() and NAME.match(p.name)]
    except OSError:
        return []
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size,
             "made": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat()} for p in found[:most]]


def clip_path(folder: Path, name: str) -> Path:
    """A clip in the folder, by its name as listed; LookupError when there's none by that name."""
    path = folder / name
    if not NAME.match(name) or not path.is_file():
        raise LookupError("There's no clip by that name.")
    return path


def save(ffmpeg: str, folder: Path, seconds: int, tz: str | None = None, run=None) -> dict:
    """The last `seconds` that went to YouTube, saved in the folder as an MP4 named for now (in time zone `tz`, the
    page's own; the plugin's otherwise): {name, seconds (as saved), size}. ValueError says why it couldn't be. (`run`:
    subprocess.run, which tests swap.)"""
    run = run or subprocess.run
    problem = folder_problem(folder)
    if problem:
        raise ValueError(problem)
    data, got = recent.last(clamp(seconds))
    if not data:
        raise ValueError("Nothing has gone to YouTube yet: a clip is taken from what the stream has sent.")
    when = datetime.now(_zone(tz))
    name = _free_name(folder, f"{when:%Y-%m-%d %H-%M-%S}", round(got))
    final = folder / name
    part = folder / f".{name}.part"
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as ts:
        ts.write(data)
    try:
        done = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "mpegts", "-i", ts.name,
                    "-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", "-f", "mp4", str(part)],
                   capture_output=True, timeout=SAVE_TIMEOUT)
        if done.returncode != 0 or not part.exists() or part.stat().st_size == 0:
            said = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise ValueError(f"The clip couldn't be saved ({said[-1] if said else 'ffmpeg gave nothing'}).")
        os.replace(part, final)
    except subprocess.TimeoutExpired:
        raise ValueError("Saving the clip took too long: is the clips folder's share answering?") from None
    except OSError as e:
        raise ValueError(f"The clip couldn't be written in the clips folder ({e.strerror or e}).") from None
    finally:
        for leftover in (Path(ts.name), part):
            try:
                leftover.unlink()
            except OSError:
                pass
    return {"name": name, "seconds": round(got), "size": final.stat().st_size}


def _zone(tz: str | None):
    try:
        return ZoneInfo(tz) if tz else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _free_name(folder: Path, stamp: str, seconds: int) -> str:
    """"<date> <time> (<n> s).mp4", with a number added when one taken the same second is there already."""
    name, n = f"{stamp} ({seconds} s).mp4", 2
    while (folder / name).exists():
        name, n = f"{stamp} {n} ({seconds} s).mp4", n + 1
    return name
