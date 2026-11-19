"""
Farmhand's Stream control (docs/plugins.md in Farmhand's repository, "Stream control"): the operator designs one stream
in Farmhand and ticks which stream plugins bring it in. While Farmhand says this plugin is one of them, its layout takes
over: the plugin sends that design, not its own (which stays saved as it was, untouched, and comes back as soon as
Farmhand says Stream control is off for it).

Farmhand is asked every EVERY seconds (GET /stream/design, stream:read):

    {"enabled": true, "design": {...}, "media": {...}}   follow it: its pictures, slideshows and music are fetched into
                                                         a folder of the plugin's own (CACHE, laid out as the stream
                                                         folder is), which the engine reads while following
    {"enabled": false}, 403 (stream:read not granted),   the plugin's own layout
    404 (a Farmhand from before Stream control)
    no answer, 429...                                    left as it was until Farmhand answers again

Switching to it or back, or a new `revision` of it while live, changes the stream as saving a change on the page does
(routes.apply_change). What only YouTube has (the stream key, the sign-in, privacy, tags...) stays the plugin's own.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.parse
from pathlib import Path

from . import config, farm, stream_media
from .farmhand import FarmhandError
from .store import store

EVERY = 5  # seconds between asking Farmhand for the design
ASK_TIMEOUT = 8
FETCH_TIMEOUT = 60  # one file (an MP3 can be big)
STAMPS_FILE = ".stamps.json"  # in the cache: Farmhand's stamp of each file as it was copied ({"images/logo.png": "size-mtime"})
RETRIES = 4  # times a file is tried again after Farmhand asks the plugin to slow down (429), waiting as it says
RETRY_MOST = 10  # seconds waited at most before each of those
_sleep = time.sleep  # (tests wait for nothing)
# what Stream control's design decides, in place of the plugin's own (live_stream/choices.py's names and shapes)
DESIGN_KEYS = ("cols", "rows", "cells", "show_status", "layout", "seconds_all", "seconds_each", "resolution", "border_color",
               "background", "font", "color", "music", "music_volume", "music_shuffle", "delay", "holding", "title",
               "description")
NOT_GRANTED = ("Farmhand hasn't given this plugin the stream:read permission, so it sends its own layout. Grant it on "
               "Farmhand's Plugins page for Stream control's design to be sent.")

_lock = threading.Lock()
# design: the design followed (None: the plugin's own layout); note: why the design couldn't be asked or fetched, or
# couldn't be put on the stream (None: all well); asked: Farmhand has answered (or refused) at least once
_state: dict = {"design": None, "note": None, "asked": False}
_asked = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None


def cache() -> Path:
    """Where Stream control's pictures, slideshows and music are kept while following (as the stream folder is laid out)."""
    return Path(config.settings.data_dir) / "stream-control"


def design() -> dict | None:
    """The design followed now, or None (the plugin's own layout)."""
    with _lock:
        return _state["design"]


def media_folder() -> Path | None:
    """The folder the engine reads pictures and music from while following (None: the stream folder)."""
    return cache() if design() is not None else None


def note() -> str | None:
    with _lock:
        return _state["note"]


def _noted(text: str | None) -> None:
    with _lock:
        _state["note"] = text


def page_part() -> dict:
    """What the page shows of it: {following, revision, saved_at, title, note}."""
    d = design()
    return {"following": d is not None, "revision": d.get("revision") if d else None, "saved_at": d.get("saved_at") if d else None,
            "title": d.get("title") if d else None, "note": note()}


def reset() -> None:
    """Back to the plugin's own layout, nothing asked yet (tests, and the plugin stopping)."""
    with _lock:
        _state.update(design=None, note=None, asked=False)
    _asked.clear()


# ------------------------------------------------------------------ asking Farmhand


def ask() -> bool:
    """Ask Farmhand for the design once, and follow what it says (fetching the files it names first). True when what's
    sent changed (switched in or out, or a new revision)."""
    try:
        answer = farm.client(ASK_TIMEOUT).stream_design()
    except FarmhandError as e:
        if e.kind == "forbidden" or e.code == 404:  # not granted, or a Farmhand from before Stream control: our own
            return _follow(None, NOT_GRANTED if e.kind == "forbidden" else None)
        _noted(f"Couldn't ask Farmhand for Stream control's design ({e.message}); sending what was sent before.")
        _done_asking()
        return False
    if not answer.get("enabled") or not isinstance(answer.get("design"), dict):
        return _follow(None, None)
    new = answer["design"]
    old = design()
    problems, arrived = _fetch(new, answer.get("media") if isinstance(answer.get("media"), dict) else {},
                               again=old is None or old.get("revision") != new.get("revision"))
    changed = _follow(new, "; ".join(problems) or None, pictures_copied=arrived)
    if arrived and not changed:
        _pictures_arrived()
    return changed


def _pictures_arrived() -> None:
    """Pictures the design shows were copied after it was put on the stream (they couldn't all be copied in time, or
    one was replaced in Stream control's folder): while live, the picture starts again with them, as it does when a
    picture on the page changes (a hand-over, no break). Without this a slideshow copied late stays its plain colour
    until the stream is next started."""
    from . import live_stream

    if not live_stream.stream.status()["live"]:
        return
    from .routes import _restart_if_live

    stream_media.forget()
    print("stream: Stream control's pictures copied; the picture starts again with them", flush=True)
    try:
        _restart_if_live(store())
    except ValueError as e:  # (the stream carries on as it was)
        _noted(f"Stream control's pictures couldn't be put on the stream: {e}")


def _done_asking() -> None:
    with _lock:
        _state["asked"] = True
    _asked.set()


def _follow(new: dict | None, why: str | None, pictures_copied: bool = False) -> bool:
    """Send `new` (None: the plugin's own layout) from now on; while live, the stream changes as a saved change does
    (and starts its picture again when `pictures_copied`, pictures it shows newly copied, even with the layout as it was)."""
    from . import live_stream

    db = store()
    old = design()
    changed = (old is None) != (new is None) or (old is not None and old.get("revision") != new.get("revision"))
    before = live_stream.settings(db) if changed else None
    with _lock:
        _state["design"] = new
        _state["note"] = why
    _done_asking()
    if not changed:
        return False
    print(f"stream: {'following Stream control (revision ' + str(new.get('revision')) + ')' if new else 'sending its own layout'}", flush=True)
    stream_media.forget()
    if live_stream.stream.status()["live"]:
        from .routes import PICTURE, _restart_if_live, apply_change

        after = live_stream.settings(db)
        try:
            apply_change(db, before, after)
            if pictures_copied and after["enabled"] and all(before[k] == after[k] for k in PICTURE):
                _restart_if_live(db)  # (the same layout, a picture in it replaced)
        except ValueError as e:  # (the stream carries on as it was)
            _noted(f"Stream control's design couldn't be put on the stream: {e}")
    return True


# ------------------------------------------------------------------ its files


def _names(design_: dict) -> tuple[set[str], set[str]]:
    """The pictures (of the images) and the slideshows the design shows."""
    images, shows = set(), set()

    def pictures_in(layers) -> None:
        for layer in layers if isinstance(layers, list) else []:
            if isinstance(layer, dict) and layer.get("type") == "picture" and isinstance(layer.get("image"), str):
                images.add(layer["image"])

    for cell in design_.get("cells") or []:
        if not isinstance(cell, dict) or "camera" in cell:
            continue
        if isinstance(cell.get("image"), str) and cell["image"]:
            images.add(cell["image"])
        if isinstance(cell.get("slideshow"), str) and cell["slideshow"]:
            shows.add(cell["slideshow"])
        pictures_in(cell.get("layers"))
        for layers in (cell.get("slide_layers") or {}).values() if isinstance(cell.get("slide_layers"), dict) else []:
            pictures_in(layers)
    holding = design_.get("holding") if isinstance(design_.get("holding"), dict) else {}
    if isinstance(holding.get("image"), str) and holding["image"]:
        images.add(holding["image"])
    return images, shows


def _ok(name) -> bool:
    try:
        stream_media._name(name, "file")
        return True
    except ValueError:
        return False


def _keep_only(d: Path, names: set[str], dirs: bool = False) -> None:
    """Remove what's in `d` that isn't named (files, or with `dirs`, folders); the order file and part files aside."""
    if not d.is_dir():
        return
    for p in d.iterdir():
        if p.name in names or p.name == stream_media.ORDER_FILE:
            continue
        if dirs and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif not dirs and p.is_file():
            p.unlink(missing_ok=True)


def _read_stamps(root: Path) -> dict[str, str]:
    try:
        kept = json.loads((root / STAMPS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in kept.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(kept, dict) else {}


def _failed(failures: list[tuple[str, str]]) -> str | None:
    """Files that couldn't be copied, in one short line: how many, and the first one or two by name."""
    if not failures:
        return None
    n = len(failures)
    first = ", ".join(f"{name} ({why})" for name, why in failures[:1]) + "".join(f", {name}" for name, _ in failures[1:2])
    more = f" and {n - 2} more" if n > 2 else ""
    return f"{n} of Stream control's files couldn't be copied from Farmhand: {first}{more}."


def _fetch(design_: dict, media: dict, again: bool) -> tuple[list[str], bool]:
    """The files the design names, into the cache: its pictures, its slideshows' pictures in their order, and the music.
    A file is fetched only when it isn't copied yet or Farmhand's stamp for it (media.stamps, its size and time) has
    changed since it was; the stamps copied are kept in the cache (STAMPS_FILE). (A Farmhand that sends no stamps:
    pictures fetched again on a new revision, music once.) Farmhand asking to slow down (429) is waited out. Files it
    no longer names are removed. What went wrong, in a line or two (a square without its picture shows its colour),
    and whether a picture or slideshow screen was copied or a slideshow's order changed (the music aside, which the
    stream picks up as it plays)."""
    root = cache()
    images_dir, shows_dir, music_dir = root / stream_media.IMAGES, root / stream_media.SLIDESHOWS, root / stream_media.MUSIC
    for d in (images_dir, shows_dir, music_dir):
        d.mkdir(parents=True, exist_ok=True)
    client = farm.client(FETCH_TIMEOUT)
    stamps = media.get("stamps") if isinstance(media.get("stamps"), dict) else None
    copied = _read_stamps(root)
    wanted: set[str] = set()
    failures: list[tuple[str, str]] = []
    arrived = False
    q = lambda s: urllib.parse.quote(s, safe="")  # noqa: E731

    def get(rel: str, path: str, target: Path, refetch: bool) -> None:
        nonlocal arrived
        wanted.add(rel)
        stamp = stamps.get(rel) if stamps is not None else None
        had = target.is_file()
        if had:
            if stamps is None and not refetch:
                return
            if stamp is not None and copied.get(rel) == stamp:
                return
        for tries in range(RETRIES + 1):
            try:
                client.download(path, target)
                break
            except FarmhandError as e:
                if e.kind == "busy" and tries < RETRIES:
                    _sleep(min(RETRY_MOST, max(0.5, e.retry_after or 1)))
                    continue
                copied.pop(rel, None)
                failures.append((target.name, e.message))
                return
        if not rel.startswith(f"{stream_media.MUSIC}/") and (not had or stamp is not None):  # (not one fetched again only
            arrived = True                                                                # because Farmhand sends no stamps)
        if stamp is not None:
            copied[rel] = stamp
        else:
            copied.pop(rel, None)

    images, shows = _names(design_)
    images = {n for n in images if _ok(n)}
    for name in sorted(images):
        get(f"{stream_media.IMAGES}/{name}", f"/stream/pictures/{q(name)}", images_dir / name, again)
    _keep_only(images_dir, images)

    listed = {s["name"]: s.get("images") or [] for s in media.get("slideshows") or [] if isinstance(s, dict) and isinstance(s.get("name"), str)}
    shows = {s for s in shows if _ok(s)}
    for show in sorted(shows):
        d = shows_dir / show
        d.mkdir(exist_ok=True)
        order = [n for n in listed.get(show, []) if isinstance(n, str) and (stream_media.is_blank(n) or _ok(n))]
        for name in order:
            if not stream_media.is_blank(name):
                get(f"{stream_media.SLIDESHOWS}/{show}/{name}", f"/stream/slideshows/{q(show)}/pictures/{q(name)}", d / name, again)
        order_file, order_text = d / stream_media.ORDER_FILE, json.dumps(order, ensure_ascii=False)
        try:
            arrived = arrived or order_file.read_text(encoding="utf-8") != order_text
        except OSError:
            arrived = True
        order_file.write_text(order_text, encoding="utf-8")
        _keep_only(d, {n for n in order if not stream_media.is_blank(n)})
    _keep_only(shows_dir, shows, dirs=True)

    tracks = {n for n in media.get("music") or [] if isinstance(n, str) and _ok(n)} if design_.get("music") is not False else set()
    for name in sorted(tracks):
        get(f"{stream_media.MUSIC}/{name}", f"/stream/music/{q(name)}", music_dir / name, False)
    _keep_only(music_dir, tracks)

    try:  # (only the files still named, as copied)
        (root / STAMPS_FILE).write_text(json.dumps({k: v for k, v in copied.items() if k in wanted}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    problems = [p for p in (_failed(failures),) if p]
    if isinstance(media.get("error"), str) and media["error"]:
        problems.insert(0, f"Stream control's folder: {media['error']}")
    return problems, arrived


# ------------------------------------------------------------------ while the plugin runs


def start() -> None:
    """Ask Farmhand every EVERY seconds, from now until stop()."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def run() -> None:
        while not _stop.is_set():
            try:
                ask()
            except Exception as e:  # noqa: BLE001 - a hiccup here never stops the plugin: asked again next time
                _noted(f"Couldn't follow Stream control's design ({e}).")
                _done_asking()
                print(f"stream: {note()}", flush=True)
            _stop.wait(EVERY)

    _thread = threading.Thread(target=run, name="stream-control", daemon=True)
    _thread.start()


def first_answer(timeout: float) -> bool:
    """Wait (up to `timeout` seconds) for Farmhand's first answer, so going live again after a restart sends the right layout."""
    return _asked.wait(timeout)


def stop() -> None:
    _stop.set()
