"""
The farm's cameras kept in step with Farmhand, while the plugin runs: Farmhand is asked for them every EVERY seconds
(farm.cameras, which keeps the answer: sources.remember), so a camera added in Farmhand (or Mainsail) shows up in the
Clip drop-down, and one removed goes from it, within a minute, without anything else having to ask first. A printer
that's off keeps the cameras it last had (it's only off); one taken off Farmhand goes, cameras and all.
"""
from __future__ import annotations

import threading

from . import farm
from .store import store

EVERY = 60  # seconds between asking Farmhand for the cameras

_stop = threading.Event()
_thread: threading.Thread | None = None


def refresh() -> None:
    """Ask Farmhand for the cameras now (what it answers is kept; Farmhand not answering keeps what was known)."""
    try:
        farm.cameras(store())
    except Exception as e:  # noqa: BLE001 - a hiccup here never stops the plugin: tried again next time
        print(f"stream: couldn't ask Farmhand for the cameras ({e})", flush=True)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def run() -> None:
        while not _stop.is_set():
            refresh()
            _stop.wait(EVERY)

    _thread = threading.Thread(target=run, name="stream-camera-watch", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
