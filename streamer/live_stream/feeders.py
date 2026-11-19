"""
Each camera read on its own, so one camera that stops never holds up or restarts the others: its square says "Camera
down" (down_square.py) until it's back, and the picture reads every camera from steady pipes (pump.py).

A Feeder per camera: a small ffmpeg reading the camera (as Farmhand's /cameras says), turning it the way Mainsail
shows it, fitting it in the stream's size and putting out each picture the camera sends as a JPEG on its stdout; a
thread keeps the last few (KEPT_FRAMES), numbered. One that stops is started again after a growing wait (RETRY_FIRST,
doubling up to RETRY_MOST), back to the shortest once frames come again. Feeders belong to the time live, not to a
layout: a new layout's picture reads the same ones, so a camera never gets a second connection.

With an NVIDIA card (camera_decoding.py) a feeder unpacks and fits the camera on the card and puts out raw frames
(raw_frames.py, as YUV4MPEG: a line saying their size, then the frames) instead of JPEGs, so neither it nor the
picture spends the processor on JPEGs. Raw frames are all one size, so the picture's pipe for a camera is made for its
`box`: the camera's own shape fitted in the stream's size, learnt from its first line (and remembered, SHAPES, for its
next feeder), the shape Mainsail gives it until then. A camera whose shape turns out other than its pipe's has the
picture handed over to a new one made for it (pump.Pump.stale, pictures.py). A camera the card can't unpack (its
ffmpeg ended with nothing and said why, GPU_TROUBLE) is read on the processor from then on, still as raw frames, the
others staying on the card.

Why the next picture and not the latest: a camera set to 15 a second never sends them exactly 1/15 s apart (a Pi's
encoder hands them over 60 to 80 ms apart). Taking the latest at each 1/15 s, two that come within one step would lose
the first and show the second twice: a small stutter or two every second. Each writer instead takes the picture after
the one it wrote last (next_after), so the few kept even out the uneven spacing and a camera at 15 has every picture
shown once; a faster one has the oldest let go (KEPT_FRAMES), a slower one has its last repeated.
"""
from __future__ import annotations

import collections
import threading
import time

from datetime import datetime, timezone

from . import procs, raw_frames
from .camera_decoding import GPU, HWACCEL, gpu_scale

DOWN_AFTER = 10  # seconds without a frame before a camera counts as down
RETRY_FIRST, RETRY_MOST = 5, 60  # seconds before a feeder that stopped is started again (doubling)
READ = 65536
MOST_HELD = 8 * 1024 * 1024  # bytes read without a whole frame in them: the feeder is started again
JPEG_QUALITY = 4  # ffmpeg's -q:v for the feeders' frames (2 best .. 31 worst)
KEPT_LINES = 10
KEPT_FRAMES = 4  # pictures kept per camera for the writers to take in turn (at FPS: 4/15 s to even out uneven spacing)
# A camera's own frame rate: the pictures that differ from the one before (a camera that sent the same picture twice
# hasn't shown anything new), over the last RATE_WINDOW seconds. Under SLOW_FPS (a camera set to 15 a second gives 14.5
# to 15.5), its square changes less often than the stream does, and the page says so.
RATE_WINDOW = 20
SLOW_FPS = 14.0
# What a camera's ffmpeg says when the card can't unpack or fit it (an unusual camera, or the card gone): read on the
# processor instead
GPU_TROUBLE = ("cuda", "cuvid", "nvdec", "hwaccel", "hwdownload", "hardware", "impossible to convert")
PREVIEW_EVERY = 0.25  # seconds a raw camera's preview JPEG is given again before another is made (raw_frames.to_jpeg)
SHAPE_WAIT = 6  # seconds going live waits for raw cameras to say their size (a camera not up by then: its pipe's guessed)
# Each raw camera's size as it last came (its camera, turn and the stream's size -> (width, height)), until the plugin
# starts again: a new feeder's frames are taken to be that size from the start
SHAPES: dict[tuple, tuple[int, int]] = {}


def fit(aspect: str | None, width: int, height: int, turned: bool = False) -> tuple[int, int]:
    """The size (even) a camera of this shape ("16:9", "4:3"; turned: on its side) has fitted in width x height."""
    try:
        a, b = (float(x) for x in str(aspect or "16:9").split(":"))
        ratio = a / b if a > 0 and b > 0 else 16 / 9
    except ValueError:
        ratio = 16 / 9
    if turned:
        ratio = 1 / ratio
    w, h = width, round(width / ratio)
    if h > height:
        w, h = round(height * ratio), height
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


def jpeg_size(frame: bytes) -> tuple[int, int] | None:
    """A JPEG's (width, height), from its frame header, or None."""
    i = 2
    while i + 9 < len(frame):
        if frame[i] != 0xFF:
            return None
        marker = frame[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return (frame[i + 7] << 8 | frame[i + 8], frame[i + 5] << 8 | frame[i + 6])
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + (frame[i + 2] << 8 | frame[i + 3])
    return None


def frames_in(buffer: bytearray) -> list[bytes]:
    """The whole JPEGs at the front of `buffer`, taken out of it (what follows the last is left for the next read)."""
    out = []
    while True:
        start = buffer.find(b"\xff\xd8")
        if start < 0:
            del buffer[:max(0, len(buffer) - 1)]
            return out
        end = buffer.find(b"\xff\xd9", start + 2)
        if end < 0:
            del buffer[:start]
            return out
        out.append(bytes(buffer[start:end + 2]))
        del buffer[:end + 2]


def feeder_command(ffmpeg: str, input_args: list[str], turn: list[str], size: tuple[int, int]) -> list[str]:
    """The camera read, turned, fitted in `size` (its own shape kept) and put out as JPEGs on stdout: each picture the
    camera sends, once (`-fps_mode passthrough`: none repeated or dropped). Not thinned out by when they come: a Pi's
    go2rtc camera at 15 a second sometimes hands two over a millisecond apart, and both are wanted. Each is stamped with
    its count (`settb`, `setpts=N`): stamped with when it came, two that share a stamp would have the second thrown away
    by ffmpeg ("non monotonically increasing dts"), and the pictures here are taken in turn, not by time."""
    w, h = size
    chain = [*turn, f"scale={w}:{h}:force_original_aspect_ratio=decrease", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "setsar=1",
             "settb=1/1000", "setpts=N"]
    return [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", *input_args, "-an", "-vf", ",".join(chain),
            "-fps_mode", "passthrough", "-enc_time_base", "1/1000",
            "-c:v", "mjpeg", "-q:v", str(JPEG_QUALITY), "-f", "image2pipe", "pipe:1"]


def raw_feeder_command(ffmpeg: str, input_args: list[str], turn: list[str], size: tuple[int, int], gpu: bool = True) -> list[str]:
    """The camera read, turned, fitted in `size` (its own shape kept, sides even) and put out as raw frames on stdout,
    as YUV4MPEG (a first line saying their size), each picture once, stamped with its count (as feeder_command's).
    `gpu`: unpacked and fitted on the NVIDIA card (fitted before it's turned, so a camera on its side is fitted the
    other way round), only turning it left to the processor; else all on the processor."""
    w, h = size
    if gpu:
        on_side = sum(t.startswith("transpose") for t in turn) % 2 == 1
        chain, first = [*gpu_scale(*((h, w) if on_side else (w, h))), *turn], HWACCEL
    else:
        chain, first = [*turn, f"scale={w}:{h}:force_original_aspect_ratio=decrease", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        f"format={raw_frames.PIX_FMT}"], []
    chain += ["setsar=1", "settb=1/1000", "setpts=N"]
    return [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", *first, *input_args, "-an", "-vf", ",".join(chain),
            "-fps_mode", "passthrough", "-enc_time_base", "1/1000", "-pix_fmt", raw_frames.PIX_FMT, "-f", "yuv4mpegpipe",
            "pipe:1"]


def wait_for_shapes(fed: list["Feeder"], most: float) -> None:
    """Up to `most` seconds for every raw camera that's being read to say its size (its first line)."""
    until = time.monotonic() + most
    while time.monotonic() < until and any(f.raw and f._args and not f.box_known for f in fed):
        time.sleep(0.1)


class Feeder:
    """One camera, read by its own ffmpeg for as long as the stream is live (`popen(args)` starts one with its stdout
    to read: the runner's, which tests swap). With no address (`input_args` empty) it's down until it has one.
    `decoding`: GPU (camera_decoding.py), raw frames unpacked on the NVIDIA card; else JPEGs, all on the processor."""

    def __init__(self, ffmpeg: str, key: str, input_args: list[str], turn: list[str], size: tuple[int, int], popen,
                 aspect: str | None = None, decoding: str | None = None):
        self.key, self.input_args, self.turn, self.size = key, list(input_args), list(turn), size
        # the size its frames are taken to be until the first comes (its shape as Mainsail has it, fitted in `size`)
        self.guess = fit(aspect, *size, turned=any(t.startswith("transpose") for t in turn))
        self.decoding = decoding
        self.raw = decoding == GPU
        # raw: the size of its frames (its camera's shape fitted in `size`), as it last came, else the guess until its
        # first line says; box_known once it has (or had, the last time it was read)
        self._shape_key = (tuple(input_args), tuple(turn), size)
        self.box = SHAPES.get(self._shape_key, self.guess)
        self.box_known = self._shape_key in SHAPES
        self.on_card = self.raw  # (raw: unpacked on the card, until the card can't unpack this camera)
        if not input_args:
            self._args = self._cpu_args = None
        elif self.raw:
            self._args = raw_feeder_command(ffmpeg, input_args, turn, size, gpu=True)
            self._cpu_args = raw_feeder_command(ffmpeg, input_args, turn, size, gpu=False)
        else:
            self._args = self._cpu_args = feeder_command(ffmpeg, input_args, turn, size)
        self._preview: tuple[float, int, bytes] | None = None  # raw: (when, picture number, the JPEG made of it)
        self._popen = popen
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._frame: bytes | None = None
        self._kept: collections.deque[tuple[int, bytes]] = collections.deque(maxlen=KEPT_FRAMES)  # (number, picture)
        self._number = 0  # the latest picture's number (counting up while live)
        self._frame_size: tuple[int, int] | None = None
        self._at = 0.0  # (monotonic) when the latest frame came
        self._proc = None
        self._started = time.monotonic()
        self._down_since: float | None = time.time()  # (time of day) since when it has been down; None while live
        self.said: collections.deque[str] = collections.deque(maxlen=KEPT_LINES)
        self.restarts = 0
        self._new: collections.deque[float] = collections.deque()  # (monotonic) when each new picture came, RATE_WINDOW's worth
        self._reading_since: float | None = None  # (monotonic) when the frames of this connection began

    def same(self, input_args: list[str], turn: list[str], size: tuple[int, int], decoding: str | None = None) -> bool:
        return (self.input_args == list(input_args) and self.turn == list(turn) and self.size == size
                and self.decoding == decoding)

    def start(self) -> "Feeder":
        if self._args:
            threading.Thread(target=self._run, name=f"stream-feeder {self.key}", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            proc = self._proc
        procs.end(proc)

    def latest(self) -> tuple[bytes | None, tuple[int, int] | None]:
        """The latest frame and its size, while it's fresh (DOWN_AFTER); (None, the last size known) when it's down."""
        with self._lock:
            fresh = self._frame is not None and time.monotonic() - self._at < DOWN_AFTER
            return (self._frame if fresh else None), self._frame_size

    def picture(self) -> bytes | None:
        """The latest frame as a JPEG (for the page's preview), while it's fresh; None when it's down. A raw camera's is
        made from its raw frame, at most every PREVIEW_EVERY seconds."""
        if not self.raw:
            return self.latest()[0]
        with self._lock:
            fresh = self._frame is not None and time.monotonic() - self._at < DOWN_AFTER
            frame, number, kept, size = self._frame, self._number, self._preview, self._frame_size
        if not fresh or not size:
            return None
        now = time.monotonic()
        if kept and (kept[1] == number or now - kept[0] < PREVIEW_EVERY):
            return kept[2]
        jpeg = raw_frames.to_jpeg(frame, size)
        with self._lock:
            self._preview = (now, number, jpeg)
        return jpeg

    def next_after(self, number: int | None) -> tuple[bytes | None, int | None, tuple[int, int] | None]:
        """For a writer that wrote picture `number` last (None: none yet): the picture after it that's still kept (the
        oldest kept, when it has fallen behind; the latest, for a writer just starting), or the latest again when no
        newer one has come. (picture, its number, size); (None, `number`, the last size known) when it's down."""
        with self._lock:
            if self._frame is None or time.monotonic() - self._at >= DOWN_AFTER or not self._kept:
                return None, number, self._frame_size
            if number is None or number >= self._number:
                n, frame = self._kept[-1]
            else:
                n, frame = next(((n, f) for n, f in self._kept if n > number), self._kept[-1])
            return frame, n, self._frame_size

    def fps(self) -> float | None:
        """The camera's own frame rate (see RATE_WINDOW), once it has sent frames for a whole RATE_WINDOW; else None."""
        now = time.monotonic()
        with self._lock:
            while self._new and now - self._new[0] > RATE_WINDOW:
                self._new.popleft()
            if self._reading_since is None or now - self._reading_since < RATE_WINDOW:
                return None
            return len(self._new) / RATE_WINDOW

    def state(self) -> dict:
        """{state: "live" | "down" | "starting", since (ISO time it went down, or None)}; while live also fps (the
        camera's own frame rate, one decimal, or None until it's known) and slow (under SLOW_FPS)."""
        frame, _ = self.latest()
        fps = self.fps() if frame is not None else None
        with self._lock:
            if frame is not None:
                if self._down_since is not None:
                    self._down_since = None
                out = {"state": "live", "since": None, "fps": None if fps is None else round(fps, 1),
                       "slow": fps is not None and fps < SLOW_FPS}
                if self.raw:  # where it's unpacked: "gpu" (the NVIDIA card) or "cpu" (the card couldn't)
                    out["decoding"] = GPU if self.on_card else "cpu"
                return out
            if self._frame is None and time.monotonic() - self._started < DOWN_AFTER and self._args:
                return {"state": "starting", "since": None}
            if self._down_since is None:
                self._down_since = time.time()
            since = datetime.fromtimestamp(self._down_since, timezone.utc).isoformat()
            last = next((l for l in reversed(self.said) if l), None)
            return {"state": "down", "since": since, "said": last}

    def _run(self) -> None:
        wait = RETRY_FIRST
        while not self._stopped.is_set():
            got, heard = False, []
            try:
                proc = self._popen(self._args if self.on_card or not self.raw else self._cpu_args, out=True)
            except OSError as e:
                self.said.append(f"ffmpeg couldn't be started: {e.strerror or e}")
                proc = None
            if proc is not None:
                with self._lock:
                    self._proc = proc
                if self._stopped.is_set():
                    procs.end(proc)
                    return
                listener = threading.Thread(target=self._listen, args=(proc, heard), name=f"stream-feeder-said {self.key}", daemon=True)
                listener.start()
                got = self._read(proc)
                procs.end(proc)
                listener.join(timeout=2)
            if self._stopped.is_set():
                return
            if self.on_card and not got and any(w in line.lower() for line in heard for w in GPU_TROUBLE):
                self.on_card = False  # the card can't unpack this camera: the processor, from now on, at once
                self.said.append("The NVIDIA card couldn't unpack this camera: it's read on the processor instead.")
                print(f"stream: camera {self.key} read on the processor: the NVIDIA card couldn't unpack it "
                      f"({heard[-1] if heard else ''})", flush=True)
                continue
            self.restarts += 1
            wait = RETRY_FIRST if got else min(RETRY_MOST, wait * 2 if self.restarts > 1 else wait)
            self._stopped.wait(wait)

    def _read(self, proc) -> bool:
        """Frames kept as they come, until the camera's ffmpeg ends. Whether any came."""
        if self.raw:
            return self._read_raw(proc)
        buffer, got = bytearray(), False
        while not self._stopped.is_set():
            try:
                chunk = proc.stdout.read1(READ) if hasattr(proc.stdout, "read1") else proc.stdout.read(READ)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buffer += chunk
            for frame in frames_in(buffer):
                self._keep(frame, first=not got)
                got = True
            if len(buffer) > MOST_HELD:
                self.said.append("The camera sent something that isn't a picture: reading it again.")
                break
        return got

    def _read_raw(self, proc) -> bool:
        """Raw frames (YUV4MPEG: its first line says their size, each frame after a FRAME line), kept as they come until
        the camera's ffmpeg ends. Whether any came."""
        try:
            size = raw_frames.y4m_size(_line(proc.stdout))
        except (OSError, ValueError):
            size = None
        if size is None:
            return False
        with self._lock:
            self.box, self.box_known = size, True
            SHAPES[self._shape_key] = size
        need, got = raw_frames.frame_bytes(size), False
        while not self._stopped.is_set():
            try:
                if not _line(proc.stdout).startswith(b"FRAME"):
                    return got
                frame = _exactly(proc.stdout, need)
            except (OSError, ValueError):
                return got
            if frame is None:
                return got
            self._keep(frame, first=not got)
            got = True
        return got

    def _keep(self, frame: bytes, first: bool = False) -> None:
        """A picture as it comes from the camera's ffmpeg (`first`: its connection's first, its frame rate counted from
        here): kept, numbered, as the latest."""
        size = self.box if self.raw else jpeg_size(frame)
        now = time.monotonic()
        with self._lock:
            if first:
                self._new.clear()
                self._reading_since = now
            if frame != self._frame:  # (the same picture again shows nothing new)
                self._new.append(now)
            self._number += 1
            self._kept.append((self._number, frame))
            self._frame, self._at = frame, now
            if size:
                self._frame_size = size

    def _listen(self, proc, heard: list[str] | None = None) -> None:
        for line in proc.stderr or []:
            line = line.strip() if isinstance(line, str) else line.decode("utf-8", "replace").strip()
            if line:
                self.said.append(line)
                if heard is not None:
                    heard.append(line)


def _line(out, most: int = 256) -> bytes:
    """One line of a YUV4MPEG stream (its newline left off); b"" at its end."""
    got = bytearray()
    while len(got) < most:
        b = out.read(1)
        if not b:
            return b""
        if b == b"\n":
            return bytes(got)
        got += b
    raise ValueError("not YUV4MPEG")


def _exactly(out, need: int) -> bytearray | None:
    """`need` bytes read straight into a new buffer (kept as it is, never changed); None when the stream ends first."""
    frame = bytearray(need)
    into = getattr(out, "readinto", None)
    with memoryview(frame) as view:
        n = 0
        while n < need:
            if into:
                r = into(view[n:])
            else:
                chunk = out.read(need - n)
                r = len(chunk)
                view[n:n + r] = chunk
            if not r:
                return None
            n += r
    return frame
