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

The files are copied by a worker of their own, one round at a time, so asking goes on every EVERY seconds meanwhile (a
"couldn't ask" goes as soon as Farmhand answers, and switching Stream control off is followed at once): the pictures and
slideshows first, then the design is followed, then the music, which the stream picks up as it plays (a round of music
is cut short by a newer revision). The page shows how far it's got. A track Farmhand has from this plugin's own folder
(its stamp is "<this plugin's id>:<name>", music_from naming it) is copied from that folder, never fetched back through
Farmhand. (Without the worker, as in the tests, ask() copies there and then.)
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
_following = threading.RLock()  # (the asking and the copying worker never change the stream at the same time)
OFF = "off"  # _state["latest"] while Farmhand says Stream control isn't this plugin's
# design: the design followed (None: the plugin's own layout); asking: why Farmhand couldn't be asked (gone at its next
# answer); note: why the design isn't followed or couldn't be put on the stream; files: the files that couldn't be
# copied; copying: how far a copy round has got; latest: the revision Farmhand last said (OFF: switched off; None:
# nothing yet); own_id: this plugin's id in Farmhand (GET /me), once known; asked: Farmhand has answered at least once
_state: dict = {"design": None, "asking": None, "note": None, "files": None, "copying": None, "latest": None,
                "own_id": None, "asked": False}
_asked = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None
_jobs = threading.Condition()
_pending: tuple | None = None  # (design, media, again) waiting for the copying worker (the newest only)
_copier: threading.Thread | None = None


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


def _said(*keys: str) -> str | None:
    """The lines of _state under `keys` that say something, in that order, as one (None: none do)."""
    with _lock:
        said = [_state[k] for k in keys if _state[k]]
    return " ".join(said) or None


def note() -> str | None:
    """What the page says about it, in a line or a few (None: all well), how far the copying has got included."""
    return _said("asking", "files", "note", "copying")


def problem() -> str | None:
    """What's wrong, if anything (note() without the copying's progress, which is no problem): what Farmhand's Stream
    control is told while live (routes.control_status)."""
    return _said("asking", "files", "note")


def _set(**values) -> None:
    with _lock:
        _state.update(values)


def page_part() -> dict:
    """What the page shows of it: {following, revision, saved_at, title, note}."""
    d = design()
    return {"following": d is not None, "revision": d.get("revision") if d else None, "saved_at": d.get("saved_at") if d else None,
            "title": d.get("title") if d else None, "note": note()}


def reset() -> None:
    """Back to the plugin's own layout, nothing asked yet (tests, and the plugin stopping)."""
    global _pending
    with _lock:
        _state.update(design=None, asking=None, note=None, files=None, copying=None, latest=None, own_id=None, asked=False)
    with _jobs:
        _pending = None
    _asked.clear()


# ------------------------------------------------------------------ asking Farmhand


def ask() -> bool:
    """Ask Farmhand for the design once, and follow what it says: its files are copied (by the copying worker while it
    runs, else now) and the design followed once its pictures are there. True when what's sent changed now (switched
    in or out, or a new revision)."""
    try:
        answer = farm.client(ASK_TIMEOUT).stream_design()
    except FarmhandError as e:
        if e.kind == "forbidden" or e.code == 404:  # not granted, or a Farmhand from before Stream control: our own
            return _switched_off(NOT_GRANTED if e.kind == "forbidden" else None)
        _set(asking=f"Couldn't ask Farmhand for Stream control's design ({e.message}); sending what was sent before.")
        _done_asking()
        return False
    _set(asking=None)  # (Farmhand answers: whatever went wrong asking before is over)
    if not answer.get("enabled") or not isinstance(answer.get("design"), dict):
        return _switched_off(None)
    new = answer["design"]
    old = design()
    _set(latest=new.get("revision"))
    job = (new, answer.get("media") if isinstance(answer.get("media"), dict) else {},
           old is None or old.get("revision") != new.get("revision"))
    return _copy_soon(job)


def _switched_off(why: str | None) -> bool:
    global _pending
    _set(latest=OFF)
    with _jobs:
        _pending = None
    return _follow(None, why)


def _copy_soon(job: tuple) -> bool:
    """The copying worker running: `job` is handed to it (in place of one still waiting) and False; else done now."""
    global _pending
    with _jobs:
        if _copier is not None and _copier.is_alive():
            _pending = job
            _jobs.notify()
            return False
    return _copy_and_follow(job)


def _overtaken(new: dict) -> bool:
    """Farmhand has said something newer than `new` since (another revision, or Stream control switched off)."""
    with _lock:
        return _state["latest"] != new.get("revision")


def _copy_and_follow(job: tuple, worker: bool = False) -> bool:
    """One round: the pictures and slideshows `job`'s design shows copied, the design followed, then its music copied
    (given up between tracks for a newer design, or, in the worker, the plugin stopping). True when what's sent changed."""
    new, media, again = job
    failures, arrived = _fetch(new, media, again, music=False)
    if _overtaken(new):  # (a newer design, or none: the next round follows that)
        return False
    _set(files=_problems(failures, media))
    changed = _follow(new, None, pictures_copied=arrived)
    if arrived and not changed:
        _pictures_arrived()
    more, _ = _fetch(new, media, again, music=True, wanted=lambda: not _overtaken(new) and not (worker and _stop.is_set()))
    _set(files=_problems(failures + more, media))
    return changed


def _pictures_arrived() -> None:
    """Pictures the design shows were copied after it was put on the stream (they couldn't all be copied in time, or
    one was replaced in Stream control's folder): while live, the picture starts again with them, as it does when a
    picture on the page changes (a hand-over, no break). Without this a slideshow copied late stays its plain colour
    until the stream is next started."""
    from . import live_stream

    with _following:
        if not live_stream.stream.status()["live"]:
            return
        from .routes import _restart_if_live

        stream_media.forget()
        print("stream: Stream control's pictures copied; the picture starts again with them", flush=True)
        try:
            _restart_if_live(store())
        except ValueError as e:  # (the stream carries on as it was)
            _set(note=f"Stream control's pictures couldn't be put on the stream: {e}")


def _done_asking() -> None:
    with _lock:
        _state["asked"] = True
    _asked.set()


def _follow(new: dict | None, why: str | None, pictures_copied: bool = False) -> bool:
    """Send `new` (None: the plugin's own layout) from now on; while live, the stream changes as a saved change does
    (and starts its picture again when `pictures_copied`, pictures it shows newly copied, even with the layout as it was)."""
    from . import live_stream

    with _following:
        db = store()
        old = design()
        changed = (old is None) != (new is None) or (old is not None and old.get("revision") != new.get("revision"))
        before = live_stream.settings(db) if changed else None
        with _lock:
            _state["design"] = new
            _state["note"] = why
            if new is None:  # (nothing of Stream control's is copied or followed now)
                _state.update(files=None, copying=None)
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
                _set(note=f"Stream control's design couldn't be put on the stream: {e}")
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


def _problems(failures: list[tuple[str, str]], media: dict) -> str | None:
    """What went wrong with the files, in a line (None: nothing): the folder Farmhand reads them from, then the files."""
    said = [f"Stream control's folder: {media['error']}"] if isinstance(media.get("error"), str) and media["error"] else []
    said += [p for p in (_failed(failures),) if p]
    return "; ".join(said) or None


def _own_id() -> str | None:
    """This plugin's id in Farmhand (GET /me, asked once it's needed; None while Farmhand can't say)."""
    with _lock:
        if _state["own_id"]:
            return _state["own_id"]
    try:
        got = farm.client(ASK_TIMEOUT).me().get("plugin_id")
    except FarmhandError:
        return None
    if isinstance(got, str) and got:
        _set(own_id=got)
        return got
    return None


def _own_track(name: str, stamp) -> Path | None:
    """Where this plugin's own folder has the track `name`, when Farmhand's stamp says it's from there ("<own id>:<name>":
    Stream control's music_from names this plugin's folder), so it's copied from there rather than fetched back."""
    if not isinstance(stamp, str) or not stamp.endswith(f":{name}"):
        return None
    own = _own_id()
    if not own or stamp != f"{own}:{name}":
        return None
    try:
        path = stream_media.folder(store()) / stream_media.MUSIC / name
    except Exception:  # noqa: BLE001 - the stream folder away (an unmounted share...): fetched from Farmhand instead
        return None
    return path if path.is_file() else None


def _copy_local(source: Path, target: Path) -> None:
    """`source` copied to `target` whole, or not at all (as a download is)."""
    part = target.with_name(f".{target.name}.part")
    try:
        shutil.copyfile(source, part)
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)


def _download(path: str, target: Path) -> str | None:
    """One of Farmhand's files to `target`, Farmhand asking to slow down (429) waited out: why it couldn't (None: done)."""
    for tries in range(RETRIES + 1):
        try:
            farm.client(FETCH_TIMEOUT).download(path, target)
            return None
        except FarmhandError as e:
            if e.kind == "busy" and tries < RETRIES:
                _sleep(min(RETRY_MOST, max(0.5, e.retry_after or 1)))
                continue
            return e.message
    return None


def _fetch(design_: dict, media: dict, again: bool, music: bool, wanted=lambda: True) -> tuple[list[tuple[str, str]], bool]:
    """The pictures and slideshows the design shows (or with `music`, its music) into the cache. A file is fetched only
    when it isn't copied yet or Farmhand's stamp for it (media.stamps, its size and time) has changed since it was; the
    stamps copied are kept in the cache (STAMPS_FILE). (A Farmhand that sends no stamps: pictures fetched again on a new
    revision, music once.) A track from this plugin's own folder is copied from there. Farmhand asking to slow down
    (429) is waited out. Files no longer named are removed. `wanted()` turning False stops the round between files (the
    next one goes on from there). The files that couldn't be copied, (name, why), and whether a picture or slideshow
    screen was copied or a slideshow's order changed (the music aside, which the stream picks up as it plays)."""
    root = cache()
    images_dir, shows_dir, music_dir = root / stream_media.IMAGES, root / stream_media.SLIDESHOWS, root / stream_media.MUSIC
    for d in (images_dir, shows_dir, music_dir):
        d.mkdir(parents=True, exist_ok=True)
    stamps = media.get("stamps") if isinstance(media.get("stamps"), dict) else None
    copied = _read_stamps(root)
    in_music = lambda rel: rel.startswith(f"{stream_media.MUSIC}/")  # noqa: E731
    named: set[str] = set()
    jobs: list[tuple[str, str, Path, bool]] = []  # (its place, as stamps name it; Farmhand's path; where it goes; refetch)
    arrived = False
    q = lambda s: urllib.parse.quote(s, safe="")  # noqa: E731

    if not music:
        images, shows = _names(design_)
        images = {n for n in images if _ok(n)}
        jobs += [(f"{stream_media.IMAGES}/{n}", f"/stream/pictures/{q(n)}", images_dir / n, again) for n in sorted(images)]
        _keep_only(images_dir, images)
        listed = {s["name"]: s.get("images") or [] for s in media.get("slideshows") or [] if isinstance(s, dict) and isinstance(s.get("name"), str)}
        shows = {s for s in shows if _ok(s)}
        for show in sorted(shows):
            d = shows_dir / show
            d.mkdir(exist_ok=True)
            order = [n for n in listed.get(show, []) if isinstance(n, str) and (stream_media.is_blank(n) or _ok(n))]
            jobs += [(f"{stream_media.SLIDESHOWS}/{show}/{n}", f"/stream/slideshows/{q(show)}/pictures/{q(n)}", d / n, again)
                     for n in order if not stream_media.is_blank(n)]
            order_file, order_text = d / stream_media.ORDER_FILE, json.dumps(order, ensure_ascii=False)
            try:
                arrived = arrived or order_file.read_text(encoding="utf-8") != order_text
            except OSError:
                arrived = True
            order_file.write_text(order_text, encoding="utf-8")
            _keep_only(d, {n for n in order if not stream_media.is_blank(n)})
        _keep_only(shows_dir, shows, dirs=True)
    else:
        tracks = {n for n in media.get("music") or [] if isinstance(n, str) and _ok(n)} if design_.get("music") is not False else set()
        jobs += [(f"{stream_media.MUSIC}/{n}", f"/stream/music/{q(n)}", music_dir / n, False) for n in sorted(tracks)]
        _keep_only(music_dir, tracks)

    def stamp_of(rel: str):
        return stamps.get(rel) if stamps is not None else None

    def needed(rel: str, target: Path, refetch: bool) -> bool:
        named.add(rel)
        if not target.is_file():
            return True
        if stamps is None:
            return refetch
        stamp = stamp_of(rel)
        return stamp is None or copied.get(rel) != stamp

    todo = [j for j in jobs if needed(j[0], j[2], j[3])]
    failures: list[tuple[str, str]] = []
    what = "music" if music else "pictures"
    try:
        for i, (rel, path, target, _) in enumerate(todo, 1):
            if not wanted():
                break
            _set(copying=f"Copying Stream control's {what}: {i} of {len(todo)}{' tracks' if music else ''}.")
            stamp, had = stamp_of(rel), target.is_file()
            own = _own_track(target.name, stamp) if music else None
            if own is not None:
                try:
                    _copy_local(own, target)
                    why = None
                except OSError as e:
                    why = f"the plugin's own copy: {e.strerror or e}"
            else:
                why = _download(path, target)
            if why is not None:
                copied.pop(rel, None)
                failures.append((target.name, why))
                continue
            if not music and (not had or stamp is not None):  # (not one fetched again only because Farmhand sends no stamps)
                arrived = True
            if stamp is not None:
                copied[rel] = stamp
            else:
                copied.pop(rel, None)
    finally:
        _set(copying=None)

    try:  # (only the files still named, as copied; the other half's kept as they were)
        kept = {k: v for k, v in _read_stamps(root).items() if in_music(k) != music}
        kept.update({k: v for k, v in copied.items() if in_music(k) == music and k in named})
        (root / STAMPS_FILE).write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return failures, arrived


# ------------------------------------------------------------------ while the plugin runs


def start() -> None:
    """Ask Farmhand every EVERY seconds, and copy the files it names in a worker of their own, from now until stop()."""
    global _thread, _copier
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def run() -> None:
        while not _stop.is_set():
            try:
                ask()
            except Exception as e:  # noqa: BLE001 - a hiccup here never stops the plugin: asked again next time
                _set(asking=f"Couldn't follow Stream control's design ({e}).")
                _done_asking()
                print(f"stream: {note()}", flush=True)
            _stop.wait(EVERY)

    def copy() -> None:
        global _pending
        while not _stop.is_set():
            with _jobs:
                while _pending is None and not _stop.is_set():
                    _jobs.wait(1)
                job, _pending = _pending, None
            if job is None:
                continue
            try:
                _copy_and_follow(job, worker=True)
            except Exception as e:  # noqa: BLE001 - a hiccup here never stops the plugin: tried again next round
                _set(files=f"Couldn't copy Stream control's files ({e}).", copying=None)
                print(f"stream: {note()}", flush=True)

    _copier = threading.Thread(target=copy, name="stream-control-files", daemon=True)
    _copier.start()
    _thread = threading.Thread(target=run, name="stream-control", daemon=True)
    _thread.start()


def first_answer(timeout: float) -> bool:
    """Wait (up to `timeout` seconds) for Farmhand's first answer, so going live again after a restart sends the right layout."""
    return _asked.wait(timeout)


def stop() -> None:
    """Asking and copying stopped (a file being fetched is let finish, for a few seconds at most)."""
    _stop.set()
    with _jobs:
        _jobs.notify_all()
    for t in (_thread, _copier):
        if t is not None and t is not threading.current_thread():
            t.join(5)
