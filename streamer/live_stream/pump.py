"""
A Pump per picture ffmpeg: for each camera square, a named pipe the picture ffmpeg reads as an image2pipe input, written
at a steady FPS with the camera's next picture (feeders.Feeder.next_after), or its "Camera down" frame (down_square.py)
when it has sent nothing for feeders.DOWN_AFTER seconds. The picture's inputs never end or stall, so it never has to be
started again for a camera.
"""
from __future__ import annotations

import os
import threading
import time

from .constants import FPS
from .down_square import DownFrames
from .feeders import Feeder
from .pacing import pace


def make_fifo(path: str) -> None:
    """A named pipe the picture ffmpeg reads (tests swap this: Windows has none)."""
    os.mkfifo(path)


PROBE = 500000  # µs of a pipe the picture ffmpeg looks at before it starts (its default, 5 s, is 5 s a camera, one after another)


def pipe_input(path: str) -> list[str]:
    """How the picture ffmpeg reads one of a pump's pipes: JPEGs at FPS, looked at only briefly before it starts (the
    frame rate is given, so there's nothing more to find out)."""
    return ["-f", "image2pipe", "-framerate", str(FPS), "-analyzeduration", str(PROBE), "-c:v", "mjpeg", "-i", path]


class Pump:
    """
    One picture ffmpeg's camera inputs: a pipe per camera square, each written at a steady FPS by its own thread with the
    camera's latest frame or its "Camera down" frame. Started before the picture ffmpeg (each writer waits for it to
    open its pipe) and ended when it ends (watch()).
    """

    def __init__(self, cameras: list[tuple[Feeder, DownFrames]], paths: list[str]):
        self._cameras = cameras
        self.paths = paths
        self._stopped = threading.Event()
        self._files: list = []
        self._lock = threading.Lock()

    def start(self) -> "Pump":
        for path in self.paths:
            make_fifo(path)
        for (feeder, downs), path in zip(self._cameras, self.paths):
            threading.Thread(target=self._write, args=(feeder, downs, path), name="stream-pump", daemon=True).start()
        return self

    def watch(self, proc) -> None:
        """End the pump once `proc` (the picture ffmpeg reading it) has ended."""
        def wait():
            while proc.poll() is None and not self._stopped.is_set():
                time.sleep(0.5)
            self.stop()

        threading.Thread(target=wait, name="stream-pump-watch", daemon=True).start()

    def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        for path in self.paths:  # a writer still waiting for its reader is let go: it opens, sees it's stopped, ends
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
                os.close(fd)
            except OSError:
                pass
        with self._lock:
            files, self._files = self._files, []
        for f in files:
            try:
                f.close()
            except OSError:
                pass

    def frame_for(self, feeder: Feeder, downs: DownFrames, last: int | None = None) -> tuple[bytes, int | None]:
        """What a writer that wrote picture `last` writes next: the camera's next picture, or its "Camera down" frame;
        and the number to pass next time."""
        frame, number, size = feeder.next_after(last)
        return (frame if frame is not None else downs.at(size or feeder.guess)), number

    def _write(self, feeder: Feeder, downs: DownFrames, path: str) -> None:
        try:
            f = open(path, "wb", buffering=0)
        except OSError:
            return
        with self._lock:
            if self._stopped.is_set():
                f.close()
                return
            self._files.append(f)
        step = 1 / FPS
        due, written, last = time.monotonic(), 0, None
        try:
            while not self._stopped.is_set():
                frame, last = self.frame_for(feeder, downs, last)
                f.write(frame)
                due, written = due + step, written + 1
                due = pace(due, written, step, time.monotonic())
        except (OSError, ValueError):
            pass  # the picture ffmpeg ended, or the pump was stopped
        finally:
            try:
                f.close()
            except OSError:
                pass
