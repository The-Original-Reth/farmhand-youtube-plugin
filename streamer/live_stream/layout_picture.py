"""
A layout's picture, made ready when going live or handing over to a new layout (runner.Stream.go_live): its own folder
with the words on the cameras, its squares, taking turns' timetable and each camera's "Camera down" square, and
make(), which gives the command for a picture ffmpeg starting now and the pump feeding it its cameras.
"""
from __future__ import annotations

import os
import tempfile

from ..store import Store
from . import staging
from .down_square import DownFrames
from .feeders import Feeder
from .fonts import font_file
from .picture import Look, command, timetable
from .pump import Pump
from .sources import Source
from .staging import write_atomically


def down_squares(chosen: dict, srcs: list[Source], fed: list[Feeder] | None = None) -> list[DownFrames]:
    """Each camera's "Camera down" square: its own square's colour and words' font and colour, else the stream's (raw
    frames for a camera its feeder hands over raw)."""
    own_cells = {c["camera"]: c for c in chosen["cells"] if c and "camera" in c}

    def looks(s: Source, raw: bool) -> DownFrames:
        mine = own_cells.get(s.key) or {}
        return DownFrames(mine.get("background") or chosen["background"], font_file(mine.get("words_font") or chosen["font"]),
                          mine.get("words_color") or chosen["color"], s.name or s.label, raw=raw)

    return [looks(s, bool(f and f.raw)) for s, f in zip(srcs, fed or [None] * len(srcs))]


def prepare(db: Store, chosen: dict, ffmpeg: str, url: str, srcs: list[Source], fed: list[Feeder], enc,
            resolution: str | None, cache: str | None):
    """The layout's picture made ready: (its folder, its words' files (None: no words on the cameras), make). Its own
    folder, so the old layout's keeps going until it's handed over; slideshow screens already put together this time
    live (`cache`) are reused. `enc`: how it's compressed (encoders.py), or a function saying so as each picture starts."""
    downs = down_squares(chosen, srcs, fed)
    folder = tempfile.mkdtemp(prefix="farmhand-stream-")
    files = [os.path.join(folder, f"{i}.txt") for i in range(len(srcs))] if chosen["show_status"] else None
    for path, s in zip(files or [], srcs):
        write_atomically(path, s.label)
    cycle = (chosen["seconds_all"], chosen["seconds_each"]) if chosen["layout"] == "cycle" and len(srcs) > 1 else None
    cells = staging.squares(db, chosen, folder, cache=cache)
    turns_file = os.path.join(folder, "turns.txt") if cycle else None
    starts = [0]

    def make(clock: float, offset: float) -> tuple[list[str], Pump]:
        """The command for a picture ffmpeg starting now (`clock`: the time of day, so its slideshows and turns keep
        the page's; `offset`: the stream's age, its times' start), and the pump feeding it its cameras: its own pipes,
        so one starting beside the one going out never takes its frames."""
        if turns_file:
            write_atomically(turns_file, timetable(len(srcs), *cycle, clock=clock))
        starts[0] += 1
        paths = [os.path.join(folder, f"camera-{i}-{starts[0]}.{'yuv' if f.raw else 'mjpeg'}") for i, f in enumerate(fed)]
        pump = Pump(list(zip(fed, downs)), paths)
        piped = [Source(s.printer_id, s.label, pump.input_args(i), [], key=s.key, name=s.name) for i, s in enumerate(srcs)]
        return command(ffmpeg, piped, url, Look(
            encoder=enc() if callable(enc) else enc, clock=clock, status_files=files, cycle=cycle, turns_file=turns_file, resolution=resolution,
            border_color=chosen["border_color"], background=chosen["background"], font=chosen["font"], color=chosen["color"],
            cols=chosen["cols"], rows=chosen["rows"], cells=cells, music=True, relay_offset=offset, lists_dir=folder,
        )), pump

    return folder, files, make
