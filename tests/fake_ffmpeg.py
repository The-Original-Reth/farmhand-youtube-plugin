"""
ffmpeg as the Stream plugin starts it (each camera's feeder, the picture, and the sender that holds the connection to
YouTube), for the stream tests: `ffmpeg` (conftest) puts FakeFfmpeg in its place. Each command is remembered, says what
it's given to say on stderr, and runs until ended; a picture one puts out a keyframe, so a hand-over to it completes; a
feeder puts out JPEGs while its camera is up (`down`: the addresses of cameras that are off). Copied from Farmhand's
backend/tests/fake_ffmpeg.py, with the feeders added.
"""
from __future__ import annotations

import threading


class FakeFfmpeg:
    """ffmpeg as the stream starts it: remembers each command (the picture's in `commands`, the sender's in `senders`),
    says `lines` on stderr, runs until ended. A picture one puts out a keyframe, so a hand-over to it completes."""

    def __init__(self, lines=(), exits=False):
        self.commands: list[list[str]] = []
        self.procs: list = []
        self.senders: list[list[str]] = []
        self.sender_procs: list = []
        self.lines = list(lines)
        self.exits = exits
        self.feeders: list[list[str]] = []
        self.feeder_procs: list = []
        self.down: set[str] = set()  # cameras (their addresses) that are off: their feeders end at once, with nothing
        # cameras (their addresses) an NVIDIA card can't unpack: a feeder reading one on the card ends at once, saying so
        self.gpu_trouble: set[str] = set()
        # the size a raw camera's frames come at (its address -> (width, height)); else the size it's fitted to
        self.shapes: dict[str, tuple[int, int]] = {}
        # cameras (their addresses) showing less than they send: each new picture is put out this many times, as a camera
        # sending the same picture again does (others send a new picture every frame)
        self.repeat: dict[str, int] = {}
        self.holdings: list[list[str]] = []  # the holding screen's ffmpegs (after the plugin's own restart)
        self.holding_procs: list = []

    def __call__(self, args, feed=False, out=False):
        if "-vf" in args and args[-1] == "pipe:1":  # a camera's feeder
            url = args[args.index("-i") + 1]
            proc = _Feeder(self, url, args)
            self.feeders.append(args)
            self.feeder_procs.append(proc)
            return proc
        if "-loop" in args and args[-1] == "pipe:1":  # the holding screen
            proc = _Holding()
            self.holdings.append(args)
            self.holding_procs.append(proc)
            return proc
        picture = "-filter_complex" in args
        proc = _Proc(self.lines, self.exits, picture=picture)
        if picture:
            self.commands.append(args)
            self.procs.append(proc)
        else:
            self.senders.append(args)
            self.sender_procs.append(proc)
        return proc


def _keyframe_packet() -> bytes:
    """One MPEG-TS packet starting a keyframe of the picture (PID 0x100, random access indicator set)."""
    return bytes([0x47, 0x41, 0x00, 0x30, 0x07, 0x40]) + bytes(182)


class _Out:
    """A picture ffmpeg's stdout: a keyframe, then nothing more until it's ended."""

    def __init__(self, ended):
        self._ended = ended
        self._sent = False

    def read(self, n=-1):
        if not self._sent and not self._ended.is_set():  # (one ended before it began sends nothing)
            self._sent = True
            return _keyframe_packet()
        self._ended.wait()
        return b""


class _Sink:
    """ffmpeg's stdin, as the music player writes to it: keeps what's written."""

    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    def flush(self):
        pass


class _Proc:
    def __init__(self, lines, exits, picture=False):
        self._ended = threading.Event()
        if exits:
            self._ended.set()
        self.stderr = iter([line + "\n" for line in lines])
        self.stdin = _Sink()
        self.stdout = _Out(self._ended) if picture else None

    def poll(self):
        return 1 if self._ended.is_set() else None

    def wait(self, timeout=None):
        self._ended.wait(timeout)
        return 1

    def terminate(self):
        self._ended.set()

    kill = terminate


HOLDING_FRAME = bytes([0x47, 0x41, 0x00, 0x10]) + b"HOLD" + bytes(180)  # a frame of the holding screen starting


class _HoldingOut:
    """The holding screen's stdout: a frame's first packet every 20 ms until it's ended."""

    def __init__(self, ended):
        self._ended = ended

    def read(self, n=-1):
        import time

        if self._ended.is_set():
            return b""
        time.sleep(0.02)
        return HOLDING_FRAME


class _Holding(_Proc):
    def __init__(self):
        super().__init__([], False)
        self.stdout = _HoldingOut(self._ended)


def jpeg(colour=(200, 40, 40), size=(64, 48)) -> bytes:
    """A small real JPEG, as a camera's feeder puts out."""
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "JPEG")
    return out.getvalue()


_PICTURES = []  # two different frames, taken turns with, so each frame is a new picture (made once: Pillow is slow-ish)


class _FeederOut:
    """A feeder's stdout: a JPEG every 20 ms while its camera is up; ends (b"") once it's off or the feeder is ended.
    Each is a new picture, or the same one `fake.repeat[url]` times over."""

    def __init__(self, fake, url, ended):
        self._fake, self._url, self._ended = fake, url, ended
        self._n = 0

    def read(self, n=-1):
        import time

        if self._ended.is_set() or self._url in self._fake.down:
            self._ended.set()
            return b""
        time.sleep(0.02)
        if not _PICTURES:
            _PICTURES.extend([jpeg((200, 40, 40)), jpeg((40, 40, 200))])
        picture = _PICTURES[(self._n // self._fake.repeat.get(self._url, 1)) % 2]
        self._n += 1
        return picture


class _RawFeederOut:
    """A raw feeder's stdout (the cameras unpacked on an NVIDIA card): YUV4MPEG, its first line saying `size`, then a
    raw frame every 20 ms while its camera is up, two different ones in turn, handed over in pieces as a pipe does; ends
    as _FeederOut's does."""

    def __init__(self, fake, url, ended, size):
        self._fake, self._url, self._ended = fake, url, ended
        self._frame_bytes = size[0] * size[1] * 3 // 2
        self._left, self._n = f"YUV4MPEG2 W{size[0]} H{size[1]} F15:1 Ip A1:1 C420mpeg2\n".encode(), 0

    def read(self, n=-1):
        import time

        if self._ended.is_set() or self._url in self._fake.down:
            self._ended.set()
            return b""
        if not self._left:
            time.sleep(0.02)
            self._left = b"FRAME\n" + bytes([16 + self._n % 2]) * self._frame_bytes
            self._n += 1
        n = 65536 if n is None or n < 0 else min(n, 65536)
        piece, self._left = self._left[:n], self._left[n:]
        return piece


class _Feeder(_Proc):
    def __init__(self, fake, url, args=()):
        super().__init__([], False)
        on_card = "-hwaccel" in args
        trouble = on_card and url in fake.gpu_trouble
        if "yuv4mpegpipe" in args:
            chain = args[args.index("-vf") + 1]
            target = next(p for p in chain.split(",") if p.startswith(("scale_cuda=", "scale=")) and "iw" not in p)
            w, h = (int(x) for x in target.split("=")[1].split(":")[:2])
            size = fake.shapes.get(url, (w, h))  # (as it comes: the size it's fitted to, unless it's said otherwise)
            self.stdout = _RawFeederOut(fake, "<trouble>" if trouble else url, self._ended, size)
            if trouble:
                fake.down.add("<trouble>")
        else:
            self.stdout = _FeederOut(fake, url, self._ended)
        said = ["Connection refused\n"] if url in fake.down else []
        if trouble:
            said = ["[h264 @ 0x1] Failed setup for format cuda: hwaccel initialisation returned error.\n"]
        self.stderr = iter(said)


def frame_at(seconds: float) -> bytes:
    """A TS packet starting a frame of the picture at this time (its PES header's PTS; times start again from 0 after
    about 26.5 hours, as MPEG-TS's do)."""
    pts = int(seconds * 90000) % (1 << 33)
    b = bytes([0x21 | ((pts >> 29) & 0xE), (pts >> 22) & 0xFF, ((pts >> 14) & 0xFE) | 1, (pts >> 7) & 0xFF, ((pts << 1) & 0xFE) | 1])
    pes = bytes([0, 0, 1, 0xE0, 0, 0, 0x80, 0x80, 5]) + b
    return bytes([0x47, 0x41, 0x00, 0x10]) + pes + bytes(184 - len(pes))
