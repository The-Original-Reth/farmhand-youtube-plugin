from __future__ import annotations

import os

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from . import live_stream, storage, stream_control, stream_media, youtube_live
from .errors import answer_errors
from .schemas import SlideshowIn, SlideshowOrderIn, StreamSettingsIn, YouTubeClientIn, YouTubeSignOutIn
from .store import Store, session_factory, store


def get_db() -> Store:
    """Every route's `db`: the plugin's store (store.py)."""
    return store()


def require_confirm(confirm: bool) -> None:
    """A delete must say confirm=true (as on Farmhand's API): the page's own question is the real guard."""
    if not confirm:
        raise HTTPException(status_code=409, detail="Deleting needs confirm=true.")

# The Stream page's API (as Farmhand's own Stream page had it, before Stream became this plugin), under the
# plugin's /api/stream: printers' cameras sent live to YouTube (see live_stream/), and the YouTube broadcast, made and
# changed through YouTube's API (live_stream/broadcast.py). The stream key, the Google client secret and the YouTube
# sign-in go in and never come back out: answers only say they're there. (Where the stream folder is isn't chosen
# here: it's the folder the plugin's container is given.)
router = APIRouter(prefix="/api/stream", tags=["stream"])

# Changing one of these while live means the picture (or the sound) changes, so the stream starts again. (Not the
# resolution: the size going out is set when going live, and a new one saved while live waits for the next Go live.)
PICTURE = ("destination", "cols", "rows", "cells", "show_status", "layout", "seconds_all", "seconds_each", "border_color",
           "background", "font", "color", "key_set")
MUSIC = ("music", "music_volume", "music_shuffle")  # these the music follows while live, without starting it again


def _restart_if_live(db: Store) -> None:
    if live_stream.stream.status()["live"]:
        live_stream.stream.go_live(db, session_factory)


@router.get("")
def stream_page(db: Store = Depends(get_db)):
    """{settings, media, status, destinations, resolutions, youtube}: see live_stream.page. Also moves a YouTube
    sign-in along."""
    return live_stream.page(db)


@router.put("")
def save_settings(payload: StreamSettingsIn, db: Store = Depends(get_db)):
    """
    Save the stream's settings. While live: a change to the picture starts the stream again with it; a new title,
    description or YouTube setting is set on the YouTube broadcast straight away; turning the stream off (enabled: false) stops it.
    """
    with answer_errors():
        before = live_stream.settings(db)
        after = live_stream.save(
            db, destination=payload.destination, chosen=payload.cameras, show_status=payload.show_status, layout=payload.layout,
            seconds_all=payload.seconds_all, seconds_each=payload.seconds_each, resolution=payload.resolution,
            border_color=payload.border_color, background=payload.background, font=payload.font, color=payload.color, enabled=payload.enabled, cols=payload.cols, rows=payload.rows, cells=payload.cells,
            title=payload.title, description=payload.description,
            music=payload.music, music_volume=payload.music_volume, music_shuffle=payload.music_shuffle, delay=payload.delay, youtube=payload.youtube, holding=payload.holding,
            stream_key=payload.stream_key, clear_key=payload.clear_key,
        )
        apply_change(db, before, after, new_key=bool(payload.stream_key))
    return live_stream.page(db)


def apply_change(db: Store, before: dict, after: dict, new_key: bool = False) -> None:
    """What's sent changed from `before` to `after` (settings(): saved on the page, or Stream control's design switched
    to, from, or saved again): while live, the stream follows. Off: it stops; the picture: it starts again with it (a
    hand-over, no break); the music: it follows as it plays; the title, description or YouTube settings: set on the
    broadcast. ValueError when the new picture can't go out (the stream carries on as it was)."""
    live = live_stream.stream.status()["live"]
    if live and not after["enabled"]:  # turned off: nothing streams
        live_stream.stream.stop()
    elif live and (any(before[k] != after[k] for k in PICTURE) or new_key):
        _restart_if_live(db)
    elif live and any(before[k] != after[k] for k in MUSIC):
        live_stream.stream.music_settings(after)
    elif (live and after["destination"] == "youtube"
          and (before["title"], before["description"], before["youtube"]) != (after["title"], after["description"], after["youtube"])):
        text_changed = (before["title"], before["description"]) != (after["title"], after["description"])
        live_stream.stream.send_to_youtube(db, after, text_changed=text_changed)


@router.post("/start")
def go_live(db: Store = Depends(get_db)):
    """Go live with what's saved. 422 says why not (no key, no cameras, a camera that can't be streamed...)."""
    with answer_errors():
        live_stream.stream.go_live(db, session_factory)
    return live_stream.page(db)


@router.post("/stop")
def stop(db: Store = Depends(get_db)):
    live_stream.stream.stop()
    return live_stream.page(db)


# ------------------------------------------------------------------ the stream folder: pictures and slideshows


def _media(call):
    """A stream folder request: its errors as answers (503 while the folder isn't available)."""
    try:
        with answer_errors():
            return call()
    except storage.StorageUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None


def _restart_if_used(db: Store, slideshow: str | None, image: str | None = None) -> None:
    """Pictures changed: the stream starts again with them, when it's live and a square shows them."""
    if stream_control.design() is not None:  # (Stream control's design is sent: the stream folder's aren't shown)
        return
    def on_it(c) -> bool:  # (its own picture, or one of its more pictures, a slideshow screen's too)
        layers = [*(c.get("layers") or []), *(x for ls in (c.get("slide_layers") or {}).values() for x in ls)]
        return c.get("image") == image or any(x.get("type") == "picture" and x.get("image") == image for x in layers)

    for c in live_stream.settings(db)["cells"]:
        if c and "camera" not in c and ((slideshow and c.get("slideshow") == slideshow) or (image and on_it(c))):
            _restart_if_live(db)
            return


# An SVG can carry scripts: opened on its own it would run them as this page. It's sent as a picture that can't
# (CSP sandbox, no scripts), as an <img> shows it anyway.
SVG_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
               "X-Content-Type-Options": "nosniff"}


def _picture_answer(data: bytes, kind: str) -> Response:
    headers = {"Cache-Control": "no-cache", **(SVG_HEADERS if kind == "image/svg+xml" else {})}
    return Response(content=data, media_type=kind, headers=headers)


@router.get("/pictures/{name}")
def get_picture(name: str, db: Store = Depends(get_db)):
    return _picture_answer(*_media(lambda: stream_media.picture(db, None, name)))


async def _add_each(files: list[UploadFile], most: int, add) -> None:
    """Each uploaded file read (`most` bytes and one more, so one too big is told so) and added: add(name, data), its
    errors as answers. Stops at the first that can't be added (those before it stay)."""
    for file in files:
        data = await file.read(most + 1)
        _media(lambda: add(file.filename or "", data))


@router.post("/pictures", status_code=201)
async def add_pictures(files: list[UploadFile] = File(...), db: Store = Depends(get_db)):
    """Pictures (PNG, JPEG, WebP or SVG, under 10 MB each) into the stream folder's images. Answers the page."""
    await _add_each(files, stream_media.PICTURE_MAX, lambda name, data: stream_media.add_picture(db, None, name, data))
    return live_stream.page(db)


@router.delete("/pictures/{name}")
def delete_picture(name: str, confirm: bool = Query(False), db: Store = Depends(get_db)):
    """Delete a picture from the stream folder. Squares showing it keep their colour and words."""
    require_confirm(confirm)
    _media(lambda: stream_media.remove_picture(db, None, name))
    _restart_if_used(db, None, name)
    return live_stream.page(db)


@router.post("/slideshows", status_code=201)
def add_slideshow(payload: SlideshowIn, db: Store = Depends(get_db)):
    """A new, empty slideshow: a folder of that name in the stream folder's slideshows. Answers the page."""
    _media(lambda: stream_media.add_slideshow(db, payload.name))
    return live_stream.page(db)


@router.put("/slideshows/{name}")
def rename_slideshow(name: str, payload: SlideshowIn, db: Store = Depends(get_db)):
    """Rename a slideshow: its folder in the stream folder, and every square showing it (on the page and in saved
    layouts) follows. 422 for a name another slideshow has. While live and it's shown, the stream starts again."""
    new = _media(lambda: stream_media.rename_slideshow(db, name, payload.name))
    if new != name and live_stream.rename_slideshow(db, name, new):
        _restart_if_used(db, new)
    return live_stream.page(db)


@router.post("/slideshows/{name}/blanks", status_code=201)
def add_blank(name: str, db: Store = Depends(get_db)):
    """A blank screen at the end of a slideshow (its colour and the words written on it). Deleted as its pictures are."""
    _media(lambda: stream_media.add_blank(db, name))
    _restart_if_used(db, name)
    return live_stream.page(db)


@router.put("/slideshows/{name}/order")
def order_slideshow(name: str, payload: SlideshowOrderIn, db: Store = Depends(get_db)):
    """Put a slideshow's pictures in the order chosen (kept in its folder). While live and it's shown, the stream starts again."""
    _media(lambda: stream_media.set_order(db, name, payload.order))
    _restart_if_used(db, name)
    return live_stream.page(db)


@router.delete("/slideshows/{name}")
def delete_slideshow(name: str, confirm: bool = Query(False), db: Store = Depends(get_db)):
    """Delete a slideshow: its pictures, then its folder (left when it holds anything else). Squares keep their colour."""
    require_confirm(confirm)
    _media(lambda: stream_media.remove_slideshow(db, name))
    _restart_if_used(db, name)
    return live_stream.page(db)


@router.post("/scan")
def scan_folder(db: Store = Depends(get_db)):
    """Read the stream folder again (pictures copied in, or taken out, by hand): it isn't read every time the page looks."""
    stream_media.media(db, scan=True)
    return live_stream.page(db)


@router.get("/fonts/{key}")
def get_font(key: str):
    """A font a square's words can be written in, for the page to show them as the stream does. 404 when the server
    doesn't have it (the stream then writes in its own font)."""
    path = live_stream.font_file(key)
    missing = key not in live_stream.FONTS or (path == live_stream.FONT and key != "dejavu-sans")  # (no file of its own)
    if missing or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="That font isn't in the Stream plugin.")
    kind = "font/otf" if path.endswith(".otf") else "font/ttf"
    return FileResponse(path, media_type=kind, headers={"Cache-Control": "max-age=86400"})


@router.get("/slideshows/{name}/pictures/{picture}")
def get_slideshow_picture(name: str, picture: str, db: Store = Depends(get_db)):
    return _picture_answer(*_media(lambda: stream_media.picture(db, name, picture)))


@router.post("/slideshows/{name}/pictures", status_code=201)
async def add_slideshow_pictures(name: str, files: list[UploadFile] = File(...), db: Store = Depends(get_db)):
    """Pictures into a slideshow's folder (made when it isn't there). While live and it's shown, the stream starts again."""
    await _add_each(files, stream_media.PICTURE_MAX, lambda filename, data: stream_media.add_picture(db, name, filename, data))
    _restart_if_used(db, name)
    return live_stream.page(db)


@router.delete("/slideshows/{name}/pictures/{picture}")
def delete_slideshow_picture(name: str, picture: str, confirm: bool = Query(False), db: Store = Depends(get_db)):
    require_confirm(confirm)
    _media(lambda: stream_media.remove_picture(db, name, picture))
    _restart_if_used(db, name)
    return live_stream.page(db)


# ------------------------------------------------------------------ the music: MP3s in the stream folder's music/
# Tracks added while live join the music's next round; it isn't stopped for them.


class TryVolumeIn(BaseModel):
    volume: int | None = Field(None, ge=0, le=100)  # None: back to the saved volume


@router.post("/music-volume")
def try_music_volume(payload: TryVolumeIn):
    """The volume slider moved on the page: the music going out follows it straight away, without saving it. The page
    says it again every few seconds while it stays, and sends {volume: null} when it's left (or the change undone); a
    volume not said again for 20 seconds goes back to the saved one by itself. (Not live: nothing to do.)"""
    live_stream.stream.try_volume(payload.volume)
    return {"volume": payload.volume}


@router.post("/music/{action}")
def music_control(action: str, db: Store = Depends(get_db)):
    """The music going out while live: play, pause, next (the next track) or back (the track playing from its start,
    or in its first 3 seconds the one before). 422 when not live, or the music is off."""
    if action not in ("play", "pause", "next", "back"):
        raise HTTPException(status_code=404, detail="Not Found")
    with answer_errors():
        live_stream.stream.music_control(action)
    return live_stream.page(db)


@router.get("/music/{name}")
def get_track(name: str, db: Store = Depends(get_db)):
    """A track, for the page to play it."""
    path = _media(lambda: stream_media.track_path(db, name))
    return FileResponse(path, media_type=stream_media.TRACK_TYPES[path.suffix.lower()], headers={"Cache-Control": "no-cache"})


@router.post("/music", status_code=201)
async def add_tracks(files: list[UploadFile] = File(...), db: Store = Depends(get_db)):
    """MP3s into the music (under 50 MB each; bigger ones can be copied in by hand)."""
    await _add_each(files, stream_media.TRACK_MAX, lambda name, data: stream_media.add_track(db, name, data))
    return live_stream.page(db)


@router.delete("/music/{name}")
def delete_track(name: str, confirm: bool = Query(False), db: Store = Depends(get_db)):
    require_confirm(confirm)
    _media(lambda: stream_media.remove_track(db, name))
    return live_stream.page(db)


# ------------------------------------------------------------------ signing in to YouTube


@router.put("/youtube")
def youtube_client(payload: YouTubeClientIn, db: Store = Depends(get_db)):
    """The Google OAuth client to sign in with. A different client ID signs out."""
    with answer_errors():
        youtube_live.set_client(db, payload.client_id, payload.client_secret)
    return live_stream.page(db)


@router.post("/youtube/sign-in")
def youtube_sign_in(db: Store = Depends(get_db)):
    """Start signing in: youtube.signing_in gives the code to enter at google.com/device. GET /stream finishes it."""
    with answer_errors():
        youtube_live.start_sign_in(db)
    return live_stream.page(db)


@router.post("/youtube/sign-out")
def youtube_sign_out(payload: YouTubeSignOutIn | None = None, db: Store = Depends(get_db)):
    youtube_live.sign_out(db, forget_client=bool(payload and payload.forget_client))
    return live_stream.page(db)


@router.post("/youtube/apply")
def youtube_apply(db: Store = Depends(get_db)):
    """Set the saved title, description and YouTube settings on the live broadcast now. status.title says how it went."""
    with answer_errors():
        live_stream.stream.send_to_youtube(db, live_stream.settings(db), asked=True)
    return live_stream.page(db)


# ------------------------------------------------------------------ Farmhand's Stream control
# Farmhand starts and stops the stream from its Stream control page (the manifest's stream_control path), and asks how
# it's doing. Each answers {live, following (Stream control's design is sent, not this plugin's own layout), error (why
# it isn't live when it should be, in plain words), watch_url}. A start that can't happen is 422 {"detail": why}.
control = APIRouter(prefix="/api/stream-control", tags=["stream control"])


def control_status() -> dict:
    status = live_stream.stream.status()
    youtube = status.get("youtube") or {}
    error = status["error"] or (stream_control.problem() if status["live"] and stream_control.design() is not None else None)
    return {"live": status["live"], "following": stream_control.design() is not None, "error": error,
            "watch_url": youtube.get("watch_url") if status["live"] else None}


@control.get("")
def control_get():
    return control_status()


@control.post("/start")
def control_start(db: Store = Depends(get_db)):
    """Go live (with Stream control's design while it takes over); live already: carries on as it is."""
    if not live_stream.stream.status()["live"]:
        with answer_errors():
            live_stream.stream.go_live(db, session_factory)
    return control_status()


@control.post("/stop")
def control_stop():
    live_stream.stream.stop()
    return control_status()
