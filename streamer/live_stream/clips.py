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
NAME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}-[0-9]{2}-[0-9]{2}( [0-9]+)?( [^/\\]{1,80}?)? \([0-9]+ s\)\.mp4$")
KEYFRAME_EVERY = 2  # seconds: how often a go2rtc camera (and the stream) sends a whole picture
STREAM = "stream"  # the source that is what goes to YouTube (the others: cameras, "printer id:camera name")
UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]+')  # (left out of a camera's name in a clip's name)


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
        for i in [*range(first, -1, -1), *range(first + 1, len(pieces))]:  # a whole frame at or before the cut, else after
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


def clamp(seconds: int) -> int:
    return max(LEAST_SECONDS, min(MOST_SECONDS, seconds))


def seconds_of(db) -> int:
    """How long a clip is, as set on the page."""
    try:
        return clamp(int(db.get(CLIP_KEY) or DEFAULT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_SECONDS


def set_seconds(db, seconds: int) -> int:
    seconds = int(seconds)
    if clamp(seconds) != seconds:
        raise ValueError(f"A clip is {LEAST_SECONDS} second to {MOST_SECONDS // 60} minutes long.")
    db.put(CLIP_KEY, str(seconds))
    return seconds


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
        found = [(p.name, p.stat()) for p in folder.iterdir() if p.is_file() and NAME.match(p.name)]
    except OSError:
        return []
    found.sort(key=lambda f: f[1].st_mtime, reverse=True)
    return [{"name": name, "size": st.st_size, "made": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat()}
            for name, st in found[:most]]


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
    final, part = folder / name, folder / f".{name}.part"
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


def _free_name(folder: Path, stamp: str, seconds: int, label: str | None = None) -> str:
    """"<date> <time> (<n> s).mp4" (a camera's: "<date> <time> <camera> (<n> s).mp4"), with a number added when one
    taken the same second is there already."""
    what = f" {UNSAFE.sub('-', label).strip(' .-')[:80]}" if label else ""
    name, n = f"{stamp}{what} ({seconds} s).mp4", 2
    while (folder / name).exists():
        name, n = f"{stamp} {n}{what} ({seconds} s).mp4", n + 1
    return name


# ------------------------------------------------------------------ clips from a camera: the next N seconds


def sources(farm: dict) -> list[dict]:
    """What can be clipped: the stream first, then every camera Farmhand can see that can be read (`farm`: the cameras
    as last heard, sources.known), as [{key, label}]."""
    out = [{"key": STREAM, "label": "Stream"}]
    for pid, printer in sorted(farm.items(), key=lambda kv: (kv[1].get("name") or "").lower()):
        every = printer.get("cameras") or []
        for cam in every:
            if not cam.get("ffmpeg_input"):
                continue
            label = printer.get("name") or f"Printer {pid}"
            out.append({"key": f"{pid}:{cam.get('name')}", "label": label if len(every) <= 1 else f"{label} · {cam.get('name')}"})
    return out


def camera_of(farm: dict, key: str) -> tuple[str, dict]:
    """(its label, the camera as Farmhand gave it) for a source key; ValueError when it can't be clipped."""
    pid, _, name = str(key).partition(":")
    printer = farm.get(pid)
    every = (printer or {}).get("cameras") or []
    cam = next((c for c in every if c.get("name") == name), None)
    if not printer or cam is None:
        raise ValueError("That camera isn't one Farmhand lists any more.")
    if not cam.get("ffmpeg_input"):
        raise ValueError(cam.get("error") or f"{printer.get('name')}'s {name} camera can't be recorded.")
    label = printer.get("name") or f"Printer {pid}"
    return (label if len(every) <= 1 else f"{label} · {name}"), cam


def camera_command(ffmpeg: str, cam: dict, seconds: int, out: str) -> list[str]:
    """Recording a camera for `seconds` into an MP4: copied as it comes when it's H.264 over RTSP needing no turning
    (go2rtc, a Buddy3D: no work, no loss); otherwise compressed (an MJPEG camera, or one Mainsail turns)."""
    args, turn = list(cam["ffmpeg_input"]), list(cam.get("ffmpeg_filters") or [])
    copy = cam.get("kind") == "rtsp" and not turn
    if copy:
        video = ["-c:v", "copy"]
        # (a copy starts at the camera's first whole picture, up to KEYFRAME_EVERY seconds in: that much more is recorded,
        # so the clip is never shorter than asked, as a clip of the stream can also be up to that much longer)
        seconds = int(seconds) + KEYFRAME_EVERY
    else:
        video = [*(["-vf", ",".join(turn)] if turn else []), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args, "-t", str(int(seconds)), "-an", *video,
            "-movflags", "+faststart", "-f", "mp4", out]


class Recordings:
    """Cameras being recorded now (one at a time per camera), and how the last few ended, for the page."""

    KEPT = 5

    def __init__(self):
        self._lock = threading.Lock()
        self.now: dict[str, dict] = {}  # source key -> {source, label, seconds, started, until}
        self.ended: collections.deque[dict] = collections.deque(maxlen=self.KEPT)  # {source, label, ok, text, name}

    def state(self) -> dict:
        with self._lock:
            return {"recording": list(self.now.values()), "ended": list(self.ended)}

    def start(self, key: str, label: str, seconds: int) -> dict:
        with self._lock:
            if key in self.now:
                raise ValueError(f"{label} is being recorded already: that clip ends first.")
            started = datetime.now().astimezone()
            job = {"source": key, "label": label, "seconds": seconds, "started": started.isoformat(),
                   "until": started.timestamp() + seconds}
            self.now[key] = job
            return dict(job)

    def end(self, key: str, ok: bool, text: str, name: str | None = None) -> None:
        with self._lock:
            job = self.now.pop(key, None) or {"label": key}
            self.ended.appendleft({"source": key, "label": job["label"], "ok": ok, "text": text, "name": name})


recordings = Recordings()


def record_camera(ffmpeg: str, folder: Path, key: str, label: str, cam: dict, seconds: int, tz: str | None = None,
                  run=None) -> dict:
    """The next `seconds` from a camera, recorded in the background into the clips folder as
    "<date> <time> <camera> (<n> s).mp4" (written beside its name and renamed once whole). Answers the recording
    ({source, label, seconds, started, until}) at once; how it ended is in recordings.state(). ValueError when it
    can't start (no clips folder, that camera being recorded already)."""
    run = run or subprocess.run
    problem = folder_problem(folder)
    if problem:
        raise ValueError(problem)
    seconds = clamp(int(seconds))
    job = recordings.start(key, label, seconds)
    when = datetime.now(_zone(tz))

    def go() -> None:
        name = _free_name(folder, f"{when:%Y-%m-%d %H-%M-%S}", seconds, label)
        part = folder / f".{name}.part"
        try:
            done = run(camera_command(ffmpeg, cam, seconds, str(part)), capture_output=True, timeout=seconds + SAVE_TIMEOUT)
            if done.returncode != 0 or not part.exists() or part.stat().st_size == 0:
                said = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                raise ValueError(f"{label} couldn't be recorded ({said[-1] if said else 'ffmpeg gave nothing'}).")
            os.replace(part, folder / name)
            recordings.end(key, True, f"Saved “{name}”.", name)
        except subprocess.TimeoutExpired:
            recordings.end(key, False, f"Recording {label} took too long: is the camera answering?")
        except ValueError as e:
            recordings.end(key, False, str(e))
        except OSError as e:
            recordings.end(key, False, f"{label}'s clip couldn't be written ({e.strerror or e}).")
        finally:
            try:
                part.unlink()
            except OSError:
                pass

    threading.Thread(target=go, name=f"stream-clip {key}", daemon=True).start()
    return job
